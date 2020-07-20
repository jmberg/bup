import stat
import sys

from bup import git, options, vfs
from bup.compat import argv_bytes
from bup.helpers import EXIT_FAILURE, EXIT_SUCCESS, log
from bup.io import byte_stream, path_msg
from bup.repo import main_repo_location, repo_for_location
from bup.url import URL


optspec = """
bup diff [-r remote] [-R] [-m] <path1> <path2>
--
r,remote=    remote repository path
R,recursive  recursively show the differences
m,metadata   also compare metadata (slower)
"""


def main(argv):
    o = options.Options(optspec)
    opt, flags_, extra = o.parse_bytes(argv[1:])

    if len(extra) != 2:
        o.fatal('must give two paths to compare')
    paths = [argv_bytes(p) for p in extra]

    loc = main_repo_location(argv_bytes(opt.remote) if opt.remote else None,
                             o.fatal)
    if isinstance(loc, URL) and loc.scheme == b'file':
        git.check_repo_or_die(loc.path)

    with repo_for_location(loc) as repo:
        return _run(opt, repo, paths)


def _run(opt, repo, paths):
    dirs = []
    for path in paths:
        children = _directory_children(repo, path, opt.metadata)
        if children is None:
            return EXIT_FAILURE
        dirs.append(children)

    out = byte_stream(sys.stdout)
    _diff_children(out, repo, opt.recursive, opt.metadata, dirs[0], dirs[1])
    out.flush()
    return EXIT_SUCCESS


def _directory_children(repo, path, meta):
    try:
        resolved = vfs.resolve(repo, path, want_meta=False)
    except vfs.IOError as ex:
        log(f'bup: error: {path_msg(path)}: {ex}\n')
        return None
    item = resolved[-1][1]
    if item is None:
        log(f'bup: error: {path_msg(path)}: not found\n')
        return None
    if not _isdir(item):
        log(f'bup: error: {path_msg(path)}: not a directory\n')
        return None
    return _children(repo, item, meta)


def _children(repo, item, meta):
    return {name: child for name, child in
            vfs.contents(repo, item, want_meta=meta) if name != b'.'}


def _isdir(item):
    return stat.S_ISDIR(vfs.item_mode(item))


def _kind(item):
    return stat.S_IFMT(vfs.item_mode(item))


# no atime/ctime: change when reading/updating metadata
# no path/hardlink_target: don't describe the file, just where it is
_META_FIELDS = ('mode', 'uid', 'gid', 'user', 'group', 'rdev', 'mtime',
                'linux_attr', 'linux_xattr', 'posix1e_acl')


def _metadata(repo, item):
    return vfs.ensure_item_has_metadata(repo, item).meta


def _meta_differs(l_meta, r_meta):
    return any(getattr(l_meta, f) != getattr(r_meta, f) for f in _META_FIELDS)


def _status(repo, meta, l_item, r_item):
    if _kind(l_item) != _kind(r_item):
        return b'M'
    if _isdir(l_item):
        if l_item.oid == r_item.oid:
            return None
        return _dir_status(repo, meta, l_item, r_item)
    if l_item.oid != r_item.oid:
        return b'M'
    if not meta:
        return None
    if _meta_differs(_metadata(repo, l_item), _metadata(repo, r_item)):
        return b'm'
    return None


def _dir_status(repo, meta, l_item, r_item):
    status = None
    if meta and _meta_differs(_metadata(repo, l_item),
                              _metadata(repo, r_item)):
        status = b'm'
    left = _children(repo, l_item, meta)
    right = _children(repo, r_item, meta)
    for name in set(left) | set(right):
        l_child = left.get(name)
        r_child = right.get(name)
        if l_child is None or r_child is None:
            return b'M'
        child = _status(repo, meta, l_child, r_child)
        if child == b'M':
            return b'M'
        if child:
            status = child
    return status


def _emit(out, code, path, isdir):
    out.write(b' %s %s%s\n' % (code, path, b'/' if isdir else b''))


def _report(out, repo, recursive, meta, code, path, item):
    isdir = _isdir(item)
    _emit(out, code, path, isdir)
    if recursive and isdir:
        entries = _children(repo, item, meta)
        left, right = ({}, entries) if code == b'A' else (entries, {})
        _diff_children(out, repo, recursive, meta, left, right,
                       path + b'/')


def _diff_common(out, repo, recursive, meta, path, l_item, r_item):
    status = _status(repo, meta, l_item, r_item)
    if status is None:
        return
    isdir = _isdir(r_item)
    _emit(out, status, path, isdir)
    if recursive and isdir:
        _diff_children(out, repo, recursive, meta,
                       _children(repo, l_item, meta),
                       _children(repo, r_item, meta),
                       path + b'/')


def _diff_children(out, repo, recursive, meta, left, right, pfx=b''):
    for name in sorted(set(left) | set(right)):
        path = pfx + name
        l_item = left.get(name)
        r_item = right.get(name)
        if l_item is None:
            _report(out, repo, recursive, meta, b'A', path, r_item)
        elif r_item is None:
            _report(out, repo, recursive, meta, b'D', path, l_item)
        elif _kind(l_item) != _kind(r_item):
            _report(out, repo, recursive, meta, b'D', path, l_item)
            _report(out, repo, recursive, meta, b'A', path, r_item)
        else:
            _diff_common(out, repo, recursive, meta, path, l_item, r_item)
