
from __future__ import absolute_import
import sys

from bup import options, git
import binascii

optspec = """
bup scan
--
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    if extra:
        o.fatal("no arguments expected")

    git.check_repo_or_die()

    cp = git.cp()
    ret = 0
    with git.PackIdxList(git.repo(b'objects/pack')) as mi:
        for pack in mi.packs:
            for oid in pack:
                oidx = binascii.hexlify(oid)
                it = cp.get(oidx, include_data=False)
                _, tp, _ = next(it)
                # bup doesn't generate tag objects
                if tp in (b'blob', b'tag'):
                    continue
                it = cp.get(oidx, include_data=True)
                next(it)
                if tp == b'tree':
                    shalist = (s for _, _, s in git.tree_decode(b''.join(it)))
                elif tp == b'commit':
                    commit = git.parse_commit(b''.join(it))
                    shalist = map(binascii.unhexlify, commit.parents + [commit.tree])
                else:
                    assert False, tp
                for suboid in shalist:
                    if not mi.exists(suboid):
                        print(f"MISSING: object {binascii.hexlify(suboid).decode('ascii')} referenced from {tp.decode('ascii')} {oidx.decode('ascii')}")
                        ret = 1
    sys.exit(ret)
