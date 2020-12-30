
import sys

from bup import options, protocol
from bup.io import byte_stream
from bup.repo import LocalRepo
from bup.helpers import Conn, debug2


optspec = """
bup server
--
Options:
force-repo force the configured (environment, --bup-dir) repository to be used
mode=      server mode (unrestricted, append, read-append, read)
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])
    if extra:
        o.fatal('no arguments expected')

    debug2('bup server: reading from stdin.\n')

    class ServerRepo(LocalRepo):
        def __init__(self, repo_dir, server):
            self.closed = True
            if opt.force_repo:
                repo_dir = None
            LocalRepo.__init__(self, repo_dir, server=server)

    def _restrict(server, commands):
        for fn in dir(server):
            if getattr(fn, 'bup_server_command', False):
                if not fn in commands:
                    del server.fn

    modes = ['unrestricted', 'append', 'read-append', 'read']
    if opt.mode is not None and opt.mode not in modes:
        o.fatal("server: invalid mode")

    with Conn(byte_stream(sys.stdin), byte_stream(sys.stdout)) as conn, \
         protocol.Server(conn, ServerRepo, mode=opt.mode) as server:
        server.handle()
