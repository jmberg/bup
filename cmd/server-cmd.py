#!/bin/sh
"""": # -*-python-*-
bup_python="$(dirname "$0")/bup-python" || exit $?
exec "$bup_python" "$0" ${1+"$@"}
"""
# end of bup preamble

from __future__ import absolute_import
import sys

from bup import options, git
from bup.io import byte_stream
from bup.protocol import BupProtocolServer
from bup.repo import LocalRepo
from bup.helpers import (Conn, debug2)


optspec = """
bup server
--
 Options:
force-repo force the configured (environment, --bup-dir) repository to be used
mode=      server mode (unrestricted, append, read-append, read)
"""
o = options.Options(optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

if extra:
    o.fatal('no arguments expected')

debug2('bup server: reading from stdin.\n')

class ServerRepo(LocalRepo):
    def __init__(self, repo_dir):
        if opt.force_repo:
            repo_dir = None
        git.check_repo_or_die(repo_dir)
        LocalRepo.__init__(self, repo_dir)

def _restrict(server, commands):
    for fn in dir(server):
        if getattr(fn, 'bup_server_command', False):
            if not fn in commands:
                del cls.fn

# always allow these - even if set-dir may actually be
# a no-op (if --force-repo is given)
permitted = set([b'quit', b'help', b'set-dir', b'list-indexes',
                 b'send-index', b'config'])

read_cmds = set([b'read-ref', b'join', b'cat-batch',
                 b'refs', b'rev-list', b'resolve'])
append_cmds = set([b'receive-objects-v2', b'read-ref', b'update-ref',
                   b'init-dir'])

if opt.mode is None or opt.mode == 'unrestricted':
    permitted = None # all commands permitted
elif opt.mode == 'append':
    permitted.update(append_cmds)
elif opt.mode == 'read-append':
    permitted.update(read_cmds)
    permitted.update(append_cmds)
elif opt.mode == 'read':
    permitted.update(read_cmds)
else:
    o.fatal("server: invalid mode")

BupProtocolServer(Conn(byte_stream(sys.stdin), byte_stream(sys.stdout)),
                  ServerRepo, permitted_commands=permitted).handle()
