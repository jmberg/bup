
from __future__ import absolute_import

from bup.compat import argv_bytes
from bup.options import Options
from bup.helpers import die_if_errors, handle_ctrl_c, log
from bup.repo import from_opts
from bup.rm import bup_rm

optspec = """
bup rm <branch|save...>
--
r,remote=    hostname:/path/to/repo of remote repository
#,compress=  set compression level to # (0-9, 9 is highest) [6]
v,verbose    increase verbosity (can be specified multiple times)
unsafe       use the command even though it may be DANGEROUS
"""

def main(argv):
    o = Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    if not opt.unsafe:
        o.fatal('refusing to run dangerous, experimental command without --unsafe')

    if len(extra) < 1:
        o.fatal('no paths specified')

    repo = from_opts(opt)
    bup_rm(repo, [argv_bytes(x) for x in extra], verbosity=opt.verbose)
    die_if_errors()
