
from __future__ import absolute_import
import sys

from bup import git, options, repo
from bup.helpers import log
from bup.compat import argv_bytes


optspec = """
[BUP_DIR=...] bup init [-r host:path]
--
r,remote=  remote repository path
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    if extra:
        o.fatal("no arguments expected")

    if opt.remote:
        with repo.make_repo(argv_bytes(opt.remote), create=True):
            pass
    else:
        try:
            repo.LocalRepo.create()
        except git.GitError as e:
            log("bup: error: could not init repository: %s" % e)
            sys.exit(1)
