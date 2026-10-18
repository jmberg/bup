

from bup.compat import argv_bytes
from bup.git import check_repo_or_die
from bup.options import Options
from bup.repo import main_repo_location, repo_for_location
from bup.rm import bup_rm
from bup.url import URL

optspec = """
bup rm <branch|save...>
--
#,compress=  set compression level to # (0-9, 9 is highest) [6]
r,remote=    remote repository path
v,verbose    increase verbosity (can be specified multiple times)
unsafe       use the command even though it may be DANGEROUS
"""

def main(argv):
    o = Options(optspec)
    opt, flags_, extra = o.parse_bytes(argv[1:])

    if not opt.unsafe:
        o.fatal('refusing to run dangerous, experimental command without --unsafe')

    if len(extra) < 1:
        o.fatal('no paths specified')

    loc = main_repo_location(argv_bytes(opt.remote) if opt.remote else None,
                             o.fatal)
    if isinstance(loc, URL) and loc.scheme == b'file':
        check_repo_or_die(loc.path)

    with repo_for_location(loc, compression_level=opt.compress) as repo:
        bup_rm(repo, [argv_bytes(x) for x in extra],
               verbosity=opt.verbose)
