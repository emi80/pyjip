#!/usr/bin/env python
"""
Executes jobs from the job database

Usage:
   jip-exec [--help|-h] [-d <db>] <id>

Options:
    -d, --db <db>  the database source that will be used to find the job
    <id>           the job id of the job that will be executed

Other Options:
    -h --help             Show this help message
"""

from jip.executils import run_job
from . import parse_args


def main():
    args = parse_args(__doc__, options_first=True)
    try:
        run_job(args["<id>"], db=args["--db"])
    except Exception, e:
        import sys
        sys.stderr.write(str(e))
        sys.stderr.write("\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
