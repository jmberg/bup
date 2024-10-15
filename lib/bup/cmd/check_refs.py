
from binascii import hexlify, unhexlify
from contextlib import nullcontext
from itertools import chain
import sys

from bup import git, options, vfs
from bup.compat import argv_bytes
from bup.git import walk_object
from bup.helpers import EXIT_TRUE, EXIT_FALSE, log
from bup.io import byte_stream, path_msg
from bup.repo import LocalRepo

optspec = """
bup check-refs [VFS_PATH ...]
--
write-missing-info=   write missing object info (VFS paths; use '' for stdout)
write-missing-info+=  write missing object info (full paths; use '' for stdout)
fully                don't shift to next ref after first problem [True]
"""

def resolve_ref(repo, ref, fatal):
    res = vfs.try_resolve(repo, ref, want_meta=True)
    # FIXME: if symlink, error(dangling)
    # FIXME: IOError ENOTDIR ELOOP
    _, leaf = res[-1]
    assert leaf # FIXME: foo/latest (i.e. symlink)
    kind = type(leaf)
    # FIXME: Root Tags FakeLink
    if kind in (vfs.Item, vfs.Chunky, vfs.RevList):
        return leaf.oid
    if kind == vfs.Commit:
        return leaf.coid
    fatal(f"can't currently handle VFS {kind} for {ref}")
    return None # should be unreachable


# FIXME: likely not set()s

def find_missing_objs(name, oid, repo, idx_list, good, bad, *,
                      missing_dest=None, full_paths=True, fully=True):
    def break_parents(oid_path):
        for parent in oid_path:
            log(f'  breaks parent {hexlify(parent)}\n')
            good.discard(parent)
            bad.add(parent)
    skip = lambda oidx: unhexlify(oidx) in good
    oid_exists = lambda oid: idx_list.exists(oid)
    missing = False
    oidx = hexlify(oid)
    for item in walk_object(repo.cat, oid_exists, oidx, stop_at=skip,
                            include_data=False):
        if item.oid in bad:
            log(f'skipping known broken path {hexlify(item.oid)}\n')
            break_parents(item.oid_path)
            break
        if item.data is False:
            missing = True
            oidxstr = oidx.decode('ascii')
            full_path = b'/'.join(chain(item.path, item.chunk_path))
            log(f"missing {oidxstr} {path_msg(full_path)}\n")
            if missing_dest and item.oid not in bad:
                missing_dest.write(b'missing-oid %s\0' % oidx)
                if full_paths or not item.chunk_path:
                    missing_dest.write(b'missing-path %s %s\0' % (oidx, full_path))
            bad.add(item.oid)
            break_parents(item.oid_path)
            if not fully:
                break
        else:
            good.add(item.oid)
    return missing


# Not sure this is what we want, but...
#
#   bup check-refs --write-missing-info missing ...
#   # "all" greps appear to support null
#   grep -Z -E '^missing-path' missing | bup select-fields 3 \
#     | xargs -0 bup ...fake-invalid
#
#   bup check-refs --write-missing-info missing ...
#   grep -Z -E '^missing-oid' missing | bup select-fields --ofs '\n' 2 \
#     | sed -e 's/^/--unnamed\ngit:/' | xargs bup get --ignore-missing ...
#
# Still doesn't resolve (side steps) the more general (and trivial
# with sed -z) question of how to portably handle rewriting with
# nulls.

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    # For now...
    if opt.write_missing_info is not None \
       and opt['write-missing-info+'] is not None:
        o.fatal('--write-missing-info and --write-missing-info+ specified')

    full_paths = opt['write-missing-info+'] is not None
    missing_path = opt.write_missing_info or opt['write-missing-info+']
    if missing_path == '':
        sys.stdout.flush()
        missing_ctx = nullcontext(byte_stream(sys.stdout))
    elif missing_path:
        missing_ctx = open(missing_path, 'wb')
    else:
        missing_ctx = nullcontext(None)

    git.check_repo_or_die()
    with missing_ctx as missing:
        rc = EXIT_TRUE
        with LocalRepo() as repo, \
             git.PackIdxList(git.repo(b'objects/pack')) as idxl:
            good_oids = set()
            bad_oids = set()
            for ref in [argv_bytes(x) for x in extra]:
                oid = resolve_ref(repo, ref, o.fatal)
                oidx = hexlify(oid)
                oidxstr = oidx.decode('ascii')
                log(f'checking {ref} {oidxstr}\n')
                bad = find_missing_objs(ref, oid, repo, idxl,
                                        good_oids, bad_oids,
                                        missing_dest=missing,
                                        full_paths=full_paths,
                                        fully=opt.fully)
                if bad:
                    rc = EXIT_FALSE
                    log(f"incomplete-ref {path_msg(ref)} {oidxstr}")
                else:
                    log(f"complete-ref {path_msg(ref)} {oidxstr}")
    sys.exit(rc)
