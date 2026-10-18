
from binascii import hexlify
import sys

from bup import options, protocol
from bup.git import parse_commit
from bup.io import byte_stream
from bup.io import path_msg as pm
from bup.protocol import CommandDenied as cd
from bup.repo import LocalRepo
from bup.helpers import Conn, debug2


optspec = """
bup server [--mode=MODE]
--
mode=  optionally restrict the server: read, append, or read-append
"""

def _vet_update_ref(repo, refname, new_oid, prev_oid):
    # don't need to handle new_oid==None (delete-ref) since
    # the command is not listed below as allowed
    _, kind, _, it = repo.cat(hexlify(new_oid))
    if kind != b'commit':
        raise cd(f'update-ref {pm(refname)}: {new_oid.hex()} is not a commit')
    parents = parse_commit(b''.join(it)).parents
    if prev_oid:
        if hexlify(prev_oid) not in parents:
            raise cd(f'update-ref {pm(refname)}: {new_oid.hex()} is not a child of {prev_oid.hex()}')

_READ_COMMANDS = (b'config-get', b'config-list', b'cat-batch', b'join',
                  b'read-ref', b'refs', b'rev-list', b'resolve')
_APPEND_COMMANDS = (b'config-get', b'read-ref', b'receive-objects-v2',
                    b'update-ref')

_MODES = {
    'unrestricted': {},
    'read': {'commands': _READ_COMMANDS},
    'append': {'commands': _APPEND_COMMANDS,
               'vet_update_ref': _vet_update_ref},
    'read-append': {'commands': tuple(sorted(set(_READ_COMMANDS
                                                 + _APPEND_COMMANDS))),
                    'vet_update_ref': _vet_update_ref},
}

def main(argv):
    o = options.Options(optspec)
    opt, _, extra = o.parse_bytes(argv[1:])
    if extra:
        o.fatal('no arguments expected')
    mode = opt.mode or 'unrestricted'
    if mode not in _MODES:
        o.fatal('unknown --mode: %s' % mode)

    debug2('bup server: reading from stdin.\n')

    class ServerRepo(LocalRepo):
        def __init__(self, repo_dir, server):
            LocalRepo.__init__(self, repo_dir, server=server)

    with Conn(byte_stream(sys.stdin), byte_stream(sys.stdout)) as conn, \
         protocol.Server(conn, ServerRepo, **_MODES[mode]) as server:
        server.handle()
