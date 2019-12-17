
from __future__ import absolute_import
import sys
from bup import compat, options, git
from bup.io import byte_stream
from bup.server import BupProtocolServer
from bup.repo import LocalRepo
from bup.helpers import (Conn, debug2)

optspec = """
bup server
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    if extra:
        o.fatal('no arguments expected')

    debug2('bup server: reading from stdin.\n')

    class ServerRepo(LocalRepo):
        def __init__(self, repo_dir):
            git.check_repo_or_die(repo_dir)
            LocalRepo.__init__(self, repo_dir)

    BupProtocolServer(Conn(byte_stream(sys.stdin), byte_stream(sys.stdout)),
                      ServerRepo).handle()
