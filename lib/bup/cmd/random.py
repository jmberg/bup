
import sys

from bup import options, _helpers
from bup.helpers import \
    (EXIT_FAILURE,
     EXIT_SUCCESS,
     handle_ctrl_c,
     istty1,
     log,
     parse_num,
     make_repo_id)


optspec = """
bup random [-S seed] <size>
bup random --repo-id N
--
v,verbose print byte counter to stderr
S,seed=   optional random number seed (default 1)
f,force   print random data to stdout even if it's a tty
repo-id=  generate an N character repository id
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    if opt.repo_id is not None:
        if opt.seed is not None : o.fatal('--repo-id does not support -S')
        if opt.force: o.fatal('--repo-id does not support --force')
        if len(extra): o.fatal('--repo-id allows no positional arguments')
        if not isinstance(opt.repo_id, int):
            o.fatal('--repo-id is not an integer')
        # Likely unnecessary (ascii incompatible locale would be "interesteing")
        sys.stderr.flush()
        sys.stderr.buffer.write(make_repo_id(opt.repo_id))
        return EXIT_SUCCESS

    if opt.seed is None: opt.seed = 1
    if len(extra) != 1:
        o.fatal('numbytes was not provided')
    if not opt.force and istty1:
        log('error: not writing binary data to a terminal. Use -f to force.\n')
        sys.exit(EXIT_FAILURE)
    try:
        total = parse_num(extra[0])
    except ValueError as ex:
        o.fatal(ex)

    handle_ctrl_c()
    _helpers.write_random(sys.stdout.fileno(), total, opt.seed,
                          opt.verbose and 1 or 0)
    return 0
