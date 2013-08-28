#!/usr/bin/env python
"""The jip models cover the basic Script class, the executable
Block model and teh custom Exceptions.
"""
import sys

from jip.block_functions import TemplateBlock, PipelineBlock
from jip.utils import render_table, log, flat_list


#currently supported block type
VALIDATE_BLOCK = "validate"
COMMAND_BLOCK = "command"
PIPELINE_BLOCK = "pipeline"

# currently supported block types as a list
SUPPORTED_BLOCKS = [
    VALIDATE_BLOCK,
    COMMAND_BLOCK,
    PIPELINE_BLOCK
]


class ScriptError(Exception):
    """Default error raised by the script parser"""

    def __init__(self, msg=None):
        self._message = msg
        self._block = None
        self._script = None
        self._offset = None
        self._text = None
        self._lineno = 0

    def __str__(self):
        path = None if self._script is None else self._script.path
        lines = []
        if path is not None:
            lines = ["""Error in script %s\n""" % path]

        if self._block is None:
            lines.append(self._message)
            return "\n".join(lines)

        block_line = self._block.lineno if self._lineno == 0 else self._lineno
        lines.append("Block at line %d: %s" % (block_line, self.message))
        if block_line > 0:
            # load script and hightlight error
            lines.append("-" * 80)
            with open(self._script.path, 'r') as f:
                script_lines = ["%d: %s" % (i + 1, l.rstrip())
                                for i, l in enumerate(f.readlines())]
                start = max(0, block_line - 5)
                end = min(block_line + 2, len(script_lines))
                if self._offset is not None:
                    end = min(block_line, len(script_lines))
                lines += script_lines[start:end]
                if self._offset is not None:
                    lines.append(" " * (self._offset + 1 +
                                        (len(str(end)))) + "^")
            lines.append("-" * 80)
        return "\n".join(lines)

    @classmethod
    def from_block_exception(cls, block, e):
        script_error = cls()
        script_error._script = block.script
        script_error._block = block
        script_error._lineno = block.lineno
        script_error.message = str(e) if str(e) != "" else "Syntax error"
        if hasattr(e, "lineno"):
            script_error._lineno = block.lineno + e.lineno
        if hasattr(e, "offset"):
            script_error._offset = e.offset
        if hasattr(e, "text"):
            script_error._text = e.text
        return script_error

    @classmethod
    def from_block_fail(cls, block, msg="", e=None):
        script_error = cls()
        script_error._script = block.script
        script_error._block = block
        script_error._lineno = block.lineno
        script_error.message = msg
        if e is not None:
            if hasattr(e, "lineno"):
                script_error._lineno = block.lineno + e.lineno
            if hasattr(e, "offset"):
                script_error._offset = e.offset
            if hasattr(e, "text"):
                script_error._text = e.text
        return script_error

    @classmethod
    def from_script_fail(cls, script, msg=""):
        script_error = cls()
        script_error._script = script
        script_error.message = msg
        return script_error


class ExecutionError(ScriptError):
    def __str__(self):
        block_line = self._block.lineno if self._lineno == 0 else self._lineno
        path = "<unknown>" if self._script is None else self._script.path
        lines = ["""Error running script %s""" % path,
                 "Block at line %d: %s" % (block_line, self.message)]
        return "\n".join(lines)


class ValidationException(ScriptError):
    """The validation exception is raised by :func:`Script.validate`
    implementations in case a validation error occurred. The errors
    dictionary contains the invalid field names as keys and a more explicit
    error messages as value.

    Properties:
        errors: dictionary
            The error dict where keys are the invalid field names and values
            are the error messages
    """
    def __init__(self, errors, block):
        """
        Create a new instance of Validation exception

        :param errors: the errors
        :type errors: dict
        """
        ScriptError.__init__(self, "Validation Error")
        self.errors = errors
        self._script = block.script
        self._block = block

    def __unicode__(self):
        return self.__str__()

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        s = "Block validation failed for " \
            "script %s in block %s" % (self._script.path, self._block)
        if self.errors is None:
            return s
        table = render_table(["Field", "Message"],
                             [(k, v) for k, v in self.errors.iteritems()])
        return "%s\n%s" % (s, table)


class Block(object):
    """Represents a single block form a script. The content
    is stored as a list of lines.

    In addition, the constructor sets the interpreter, if not specified
    to 'python' for validation blocks and to 'bash' otherwise.
    """
    def __init__(self, type, interpreter=None, block_args=None, script=None,
                 lineno=0):
        self.lineno = lineno
        self.script = script
        self.type = type
        self.interpreter = interpreter
        self.content = []
        self.block_args = block_args
        self.process = None

        if interpreter is None or len(interpreter) == 0:
            self.interpreter = "bash"

    def run(self, args):
        """Execute this block
        """
        if self.type == PIPELINE_BLOCK:
            return self._run_pipeline_block(args)
        if self.interpreter == "python" and self.type == VALIDATE_BLOCK:
            return self._run_python_block(args)
        else:
            return self._run_interpreter_block(args)

    def render(self, args):
        """Execute this block
        """
        rendered = None
        if self.interpreter == "python":
            rendered = self._render_python_block(args)
        else:
            rendered = self._render_interpreter_block(args)
        return "\n".join(["#%%begin %s %s" % (self.type, self.interpreter),
                          rendered,
                          "#%%end %s\n" % self.type])

    def terminate(self):
        """
        Terminate currently running blocks
        """
        if self.process is not None:
            if self.process._popen is not None:
                self.process.terminate()
                # give it 5 seconds to cleanup and exit
                import time
                time.sleep(5)
                if self.process.is_alive():
                    # kill it
                    import os
                    import signal
                    os.kill(self.process._popen.pid, signal.SIGKILL)

    def _run_python_block(self, args):
        """Run this block as a python block"""
        block_func = TemplateBlock(args, script=self.script)
        env = {"args": args, "jip": block_func}
        try:
            exec "\n".join(self.content) in locals(), env
        except Exception, e:
            script_error = ScriptError.from_block_exception(self, e)
            raise script_error
        if len(block_func._errors) > 0:
            raise ValidationException(block_func._errors, self)

    def _run_pipeline_block(self, args):
        """Run this block as a pipeline block"""
        template_block = TemplateBlock(args, script=self.script)
        script_block = PipelineBlock(self.script, args)
        env = {"jip": template_block, "args": args}
        for k, v in vars(script_block).items():
            if k[0] != "_":
                env[k] = v
        try:
            exec "\n".join(self.content) in locals(), env
        except Exception, e:
            script_error = ScriptError.from_block_exception(self, e)
            raise script_error
        return script_block.pipeline

    def _run_interpreter_block(self, args):
        """Execute a interpreter block"""
        import subprocess
        import jip

        # write template to named temp file and run with interpreter
        script_file = jip.create_temp_file()
        try:
            script_file.write(self._render_interpreter_block(args))
            script_file.close()
            self.process = subprocess.Popen(
                [self.interpreter, script_file.name],
                stdin=self.script.stdin,
                stdout=self.script.stdout
            )
            return self.process
        except OSError, err:
            # catch the errno 2 No such file or directory, which indicates the
            # interpreter is not available
            if err.errno == 2:
                raise ScriptError.from_block_fail(self,
                                                  "Interpreter %s not found!"
                                                  % self.interpreter)
            raise err

    def _render_python_block(self, args):
        """Run this block as a python block"""
        return "\n".join(self.content)

    def _render_interpreter_block(self, args):
        """Execute a interpreter block"""
        from jip.templates import render_template
        # create template context
        block_func = TemplateBlock(args, script=self.script)
        env = dict(args)
        env["jip"] = block_func
        env['script'] = self.script
        template = "\n".join(self.content)
        return render_template(template, **env)

    def __str__(self):
        return "Block[type:'%s']" % self.type


class Script(object):
    """JIP script class that wraps around a JIP script and
    captures meta information about the script.

    The  wrapper can be used to run scripts and build pipelines
    out of a set of scripts
    """
    def __init__(self, path, name=None, args=None):
        # the path to the script file
        self.path = path
        self.name = path
        self.args = args
        if name is not None:
            self.name = name
        # dict that maps from block type to ordered list of blocks
        # of that type
        self.blocks = {}
        # the script doctring
        self.doc_string = None
        self.inputs = {}
        self.outputs = {}
        self.options = {}
        self.running_block = None
        self.pipeline = None
        self.supports_stream_out = False
        self.supports_stream_in = False
        self.default_input = None
        self.default_output = None
        self.stdout = None
        self.stdin = None
        self.threads = 1
        self.validated = False
        self.script_options = None
        self.job = None
        if self.args is None:
            self.args = {}

    def parse_args(self, script_args):
        """Parse the command line parameters"""
        from jip.parser import parse_script_args
        parse_script_args(self, script_args)

    def __run_blocks(self, key):
        """Internal method that executes all blocks of the given type"""
        result = None
        for i, v_block in enumerate(self.blocks.get(key, [])):
            self.running_block = v_block
            args = self._clean_args()
            result = v_block.run(args)
            self.args = args
        self.running_block = None
        return result

    def _load_pipeline(self):
        """Check if the script contains pipeline blocks. If thats
        the case, run the pipeline blocks to initialize the pipeline and return
        true, else return false.
        The pipeline scripts are stored in self.pipeline and this is
        also checked at the beginning and the function return True immediately
        if the pipeline is already initialized.
        """
        if self.pipeline is not None:
            return True
        if len(self.blocks.get(PIPELINE_BLOCK, [])) == 0:
            return False
        self.validate()
        self.pipeline = self.__run_blocks(PIPELINE_BLOCK)
        return True

    def _get_output_files(self, only_files=False):
        """Generates a list of filenames that are marked as output
        files of this process"""
        if self.outputs is None:
            return []
        files = [self.args.get(k, None) if isinstance(self.args.get(k, None),
                                                      (list, tuple))
                 else [self.args.get(k, None)]
                 for k in self.outputs.iterkeys()]
        # flatten
        files = [y if not isinstance(y, dependency) else y.value for x in files
                 for y in x if not only_files or isinstance(y, basestring) or
                 isinstance(y, dependency) or y is None]
        files = filter(lambda x: x is not None, files)
        return files

    def validate(self):
        """Call all validation blocks of a script
        """
        if self.validated:
            return
        # make sure we do not mix pipeline and command blocks
        if(len(self.blocks.get(COMMAND_BLOCK, [])) > 0
           and len(self.blocks.get(PIPELINE_BLOCK, [])) > 0):
            raise ScriptError.from_script_fail(self,
                                               "Mixing command and pipeline "
                                               "blocks is currently not "
                                               "supported!")
        self.__run_blocks(VALIDATE_BLOCK)
        self.validated = True
        if self._load_pipeline():
            self.pipeline.validate()

    def run(self):
        """Call all command blocks of a script
        """
        if self._load_pipeline():
            return self.pipeline.runo()
        else:
            #log("Running command:\n%s", self.render_command())
            return self.__run_blocks(COMMAND_BLOCK)

    def terminate(self):
        """
        Terminate currently running blocks
        """
        if self.running_block is not None:
            self.running_block.terminate()

    def cleanup(self):
        """Remove any temporary files and if force is true,
        remove all generated output"""
        import shutil
        from os.path import exists, isdir
        from os import unlink
        for f in (f for f in self._get_output_files(only_files=True)
                  if exists(f)):
            try:
                log("Cleanup:remove:%s" % f)
                shutil.rmtree(f) if isdir(f) else unlink(f)
            except:
                sys.stderr.write("Cleanup:error removing file:%s\n" % f)

    def is_done(self):
        """Returns true if all detectable script output exists
        and execution can be skipped
        """
        from os.path import exists
        files = self._get_output_files()
        if files is None or len(files) == 0:
            return False
        for f in files:
            if not isinstance(f, basestring) or not exists(f):
                return False
        return True

    def help(self):
        """Return the help string for this script"""
        return self.doc_string if self.doc_string is not None else self.__doc__

    def render_command(self):
        """Render this script blocks to a string"""
        lines = ["#!/usr/bin/env jip"]
        # render validate blocks
        args = self._clean_args()
        for block in self.blocks.get(VALIDATE_BLOCK, []):
            lines.append(block.render(args))
        for block in self.blocks.get(COMMAND_BLOCK, []):
            lines.append(block.render(args))
        return "\n".join(lines)

    def _clean_args(self):
        """Check multiplicity of inputs/outputs and options
        and create a clean copy of the args map"""
        clean = {}
        for k, v in self.args.iteritems():
            o = self.get_option(k)
            if isinstance(v, dependency):
                v = v.value
            clean[k] = v
            if o is not None:
                if isinstance(v, (list, tuple)):
                    if o.multiplicity == 1:
                        if len(v) == 1:
                            clean[k] = v[0] if v[0] is not None else None
                        if len(v) == 0:
                            clean[k] = None
        return clean

    def validate_args(self, exclude=None):
        valid = True
        exclude = set([] if not exclude else exclude)
        for k, v in self.args.iteritems():
            o = self.get_option(k)
            if o is None and k not in exclude:
                valid = False
                print >>sys.stderr, "Unknonwn option %s" % k
        return valid

    def get_value(self, opt):
        if opt is None:
            return None
        name = opt
        multi = None
        if hasattr(opt, "name"):
            name = opt.name
            multi = opt.multiplicity
        value = self.args.get(name, None)
        if value is not None and multi is not None \
                and isinstance(value, (list, tuple)) \
                and multi == 1 and len(value) == 1:
            value = value[0]
        return value

    def get_option(self, name):
        if name in self.inputs:
            return self.inputs[name]
        if name in self.outputs:
            return self.outputs[name]
        return self.options.get(name, None)

    def get_script_option(self, name):
        if not name in self.script_options:
            name = name.replace("-", "")
        if not name in self.script_options:
            name = "-" + name
        if not name in self.script_options:
            name = "--" + name

        return self.script_options.get(name, None)

    def __repr__(self):
        return self.name

    @classmethod
    def from_file(cls, path):
        from jip.parser import parse_script
        return parse_script(path, cls)


class Option(object):
    def __init__(self):
        self.short = None
        self.long = None
        self.value = None
        self.name = None
        self.multiplicity = 0

    #def __str__(self):
        #return self.__repr__()

    def __repr__(self):
        return "{Option:: name:%s short:%s long:%s multi:%s value:%s}" % \
            (self.name, self.short, self.long, str(self.multiplicity),
             str(self.value))


class PythonClassScript(Script):
    """Script extension that allows to wrap
    python classes as executable units
    """
    def __init__(self, cls, decorator, add_outputs=None):
        Script.__init__(self, None)
        self.cls = cls
        self.add_outputs = add_outputs
        self.decorator = decorator
        try:
            self.instance = cls()
        except:
            self.instance = cls
        self.doc_string = self.instance.__doc__
        if self.doc_string:
            import textwrap
            self.doc_string = textwrap.dedent(self.doc_string)
        self.name = decorator.name
        self.argparser = None
        self.script_options = {}
        ## parse options
        if self.decorator.argparse:
            from argparse import ArgumentParser
            argparser = ArgumentParser(prog=self.name)
            init_parser = getattr(self.instance, self.decorator.argparse)
            init_parser(argparser)
            for action in argparser._optionals._actions:
                target = self.options
                o = Option()
                o.value = action.default
                if o.value is None and action.nargs in ("*", "+"):
                    o.value = []
                o.name = action.dest
                if o.name == "help":
                    o.value = False
                if self.decorator.check_option(self.decorator.inputs, o.name):
                    target = self.inputs
                    self.default_input = o.name
                    if o.value == sys.stdin:
                        self.supports_stream_in = True
                if self.decorator.check_option(self.decorator.outputs, o.name):
                    target = self.outputs
                    self.default_output = o.name
                    if o.value == sys.stdout:
                        self.supports_stream_out = True

                o.multiplicity = 1 if o.value is None or \
                    not isinstance(o.value, bool) else 0
                if isinstance(o.value, (list, tuple)):
                    o.multiplicity = 2
                for s in action.option_strings:
                    if s.startswith("--") and o.long is None:
                        o.long = s
                    elif s.startswith("-") and o.short is None:
                        o.short = s
                    if self.decorator.check_option(self.decorator.inputs, s):
                        target = self.inputs
                    if self.decorator.check_option(self.decorator.outputs, s):
                        target = self.outputs
                target[o.name] = o
                self.args[o.name] = o.value
                self.script_options[o.name] = o
        else:
            from jip.parser import parse_script_options
            parse_script_options(self)

        if self.add_outputs:
            for out in self.add_outputs:
                o = Option()
                o.name = out.replace("-", "_")
                o.long = "--%s" % out.replace("_", "-")
                o.multiplicity = 1
                o.value = None
                self.outputs[o.name] = o
                #self.script_options[o.name] = o

    def clone(self):
        return PythonClassScript(self.cls, self.decorator, self.add_outputs)

    def parse_args(self, script_args):
        if self.decorator.argparse:
            from argparse import ArgumentParser
            argparser = ArgumentParser(prog=self.name)
            init_parser = getattr(self.instance, self.decorator.argparse)
            init_parser(argparser)
            args = argparser.parse_args(script_args)
            self.args = vars(args)
        else:
            Script.parse_args(self, script_args)

    def _load_pipeline(self):
        if self.pipeline:
            True
        if self.decorator.pipeline:
            pipeline_fun = getattr(self.instance, self.decorator.pipeline)
            self.pipeline = pipeline_fun(self.args)
            return True
        return False

    def validate(self):
        if self.decorator.validate:
            validate_fun = getattr(self.instance, self.decorator.validate)
            r = validate_fun(self.args)
            return r
        return False

    def run(self):
        if self._load_pipeline():
            return self.pipeline.run()
        else:
            self.instance(**(self.args))

    def render_command(self):
        if self.decorator.get_command is not None:
            cmd_fun = getattr(self.instance, self.decorator.get_command)
            cmd = cmd_fun(self.args)
            from jip.templates import render_template
            # create template
            # create template context
            block_func = TemplateBlock(self.args, script=self)
            env = dict(self.args)
            env["jip"] = block_func
            env['script'] = self
            return render_template(cmd, **env)

        import cPickle
        template = """
python -c '
import sys;
import cPickle;
import jip;
jip._disable_module_search = True;
source="".join([l for l in sys.stdin]).decode("base64");
cPickle.loads(source).run();
'<< __EOF__
%s__EOF__
"""
        return template % (cPickle.dumps(self).encode("base64"))


class Pipeline(object):
    """Represents a pipeline graph as a set of ScriptNodes and edges"""
    def __init__(self, script=None):
        self.nodes = []
        self.script = script

    def run(self, name, **kwargs):
        """Find named script and add it"""
        # find script with name
        from jip.parser import parse_script
        from jip.utils import find_script_in_modules, find_script
        try:
            path = find_script(name, self.script)
            script = parse_script(path, args=kwargs)
        except:
            script = find_script_in_modules(name)
            if not script:
                raise

        script.args.update(kwargs)
        try:
            script.validate()
            script.validated = False
        except:
            pass
        return self.add(script)

    def add(self, script):
        """Add a script to the pipeline and return the ScriptNode that
        wraps the script
        """
        node = ScriptNode(script, len(self.nodes), self)
        self.nodes.append(node)
        for k, value in list(script.args.iteritems()):
            if not isinstance(value, (list, tuple)):
                value = [value]
            new_values = None
            for v in value:
                if isinstance(v, parameter) and v.node is not None:
                    #print ">>>>", v
                    if not v.node in node.parents:
                        node.parents.append(v.node)
                    if not node in v.node.children:
                        v.node.children.append(node)
                if isinstance(v, ScriptNode):
                    if new_values is None:
                        new_values = []
                    new_values.append(v.script.args[v.script.default_output])
                    if not v in node.parents:
                        node.parents.append(v)
                    if not node in v.children:
                        v.children.append(node)
            if new_values is not None:
                if script.script_options[k].multiplicity > 1:
                    script.args[k] = new_values
                elif len(new_values) == 1:
                    script.args[k] = new_values[0]
                else:
                    script.args[k] = new_values
        return node

    def validate(self):
        for script in (s.script for s in self.nodes):
            script.validate()

    def _sort_nodes(self):
        count = {}
        for node in self.nodes:
            count[node] = 0

        for node in self.nodes:
            for successor in node.children:
                count[successor] += 1
        ready = [node for node in self.nodes if count[node] == 0]
        result = []
        while ready:
            node = ready.pop(-1)
            result.append(node)
            for successor in node.children:
                count[successor] -= 1
                if count[successor] == 0:
                    ready.append(successor)
        self.nodes = result


class parameter(object):
    def __init__(self, source, name, value, multiplicity=0, node=None):
        self.source = source
        self.name = name
        self.value = value
        self.multiplicity = multiplicity
        self.node = node

    def __getstate__(self):
        odict = self.__dict__.copy()
        del odict['node']
        del odict['source']
        return odict

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return str(self.value)


class dependency(object):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return str(self.value)


class ScriptNode(object):
    """Pipeline node that wraps around a script"""

    def __init__(self, script, id, pipeline):
        object.__setattr__(self, 'id', id)
        object.__setattr__(self, 'script', script)
        object.__setattr__(self, 'parents', [])
        object.__setattr__(self, 'children', [])
        object.__setattr__(self, 'siblings', [])
        object.__setattr__(self, '_pipe_to', [])
        object.__setattr__(self, '_pipe_from', [])
        object.__setattr__(self, 'pipeline', pipeline)

    def __getattr__(self, name):
        if not name in self.script.args:
            raise LookupError("Argument not found: %s %s" % (name,
                                                             self.script.args))
        value = self.script.args[name]
        if isinstance(value, parameter):
            return parameter(self, name, value.value,
                             multiplicity=value.multiplicity,
                             node=value.node)
        return parameter(self, name, value, node=self)

    def __setattr__(self, name, value):
        if isinstance(value, parameter):
            self.script.args[name] = dependency(value.value)
            self.parents.append(value.source)
            value.source.children.append(self)
        elif isinstance(value, ScriptNode):
            other = value.script
            self.script.args[name] = other.args[other.default_output]
            self.parents.append(value)
            value.children.append(self)
        else:
            self.script.args[name] = value

    def __or__(self, other):
        """Create a dependency between this script and the other script
        and, if supported by both ends, allow streaming data from this
        script to the other
        """
        #todo: check for cycles
        self.children.append(other)
        for sibling in other.siblings:
            self.children.append(sibling)
            if self.script.supports_stream_out and \
               sibling.script.supports_stream_in:
                self._pipe_to.append(sibling)
                sibling._pipe_from.append(self)
            else:
                self.pipe_default_io(sibling)
            sibling.parents.append(self)

        if self.script.supports_stream_out and other.script.supports_stream_in:
            self._pipe_to.append(other)
            other._pipe_from.append(self)
        else:
            self.pipe_default_io(other)
        other.parents.append(self)

        ## also pipe things from my siblings
        for sibling in self.siblings:
            sibling.children.append(other)
            other.parents.append(sibling)
            if sibling.script.supports_stream_out and \
               other.script.supports_stream_in:
                sibling._pipe_to.append(other)
                other._pipe_from.append(sibling)
            else:
                sibling.pipe_default_io(other)

        self.pipeline._sort_nodes()
        return other

    def __rshift__(self, others):
        """Create a non piped dependency"""
        #todo: check for cycles
        for other in flat_list(others):
            other.depends_on(self)
            for sibling in other.siblings:
                sibling.depends_on(self)
                self.pipe_default_io(sibling)
            self.pipe_default_io(other)
            ## also pipe things from my siblings
            for sibling in self.siblings:
                other.depends_on(sibling)
                sibling.pipe_default_io(other)
            self.pipeline._sort_nodes()
        return other

    def __lshift__(self, others):
        for other in flat_list(others):
            if isinstance(other, ScriptNode):
                other.__rshift__(self)
            elif isinstance(other, parameter):
                self._add_to_default_inputs(other.value)
                self.depends_on(other.node)
        return self

    def depends_on(self, others):
        for other in flat_list(others):
            if not other in self.parents:
                self.parents.append(other)
            if not self in other.children:
                other.children.append(self)

    def pipe_default_io(self, other):
        """Set this nodes default output as the other nodes default input"""
        dop = self.script.default_output
        dip = other.script.default_input
        if dip is not None and dop is not None and \
                self.script.args[dop] is not None:
            other._add_to_default_inputs(self.script.args.get(dop, None))

    def _add_to_default_inputs(self, value):
        dip = self.script.default_input
        if dip is not None and value is not None:
            l = self.script.args[dip]
            if l is None:
                self.script.args[dip] = value
            elif isinstance(l, (list, tuple)):
                l.append(value)
            else:
                self.script.args[dip] = [l] + [value]

    def __add__(self, other):
        """Create a parallel run of two nodes"""
        self.siblings.append(other)
        for parent in self.parents:
            parent.children.append(other)
        for parent in other.parents:
            parent.children.append(self)
        self.parents.extend(other.parents)
        other.parents.extend(self.parents)
        self.pipeline._sort_nodes()
        return self

    def __repr__(self):
        return "[%d][SIBLINGS:%s][PIPE_TO:%s][PIPE_FROM:%s]" % \
               (self.id, str(self.siblings),
                str(",".join([str(x.id) for x in self._pipe_to])),
                str(",".join([str(x.id) for x in self._pipe_from])))
