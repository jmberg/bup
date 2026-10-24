
from binascii import hexlify
from contextlib import nullcontext
from stat import S_IFBLK, S_IFCHR, S_IFDIR, S_IFIFO, S_IFLNK, S_IFMT, S_IFREG
import os, sys, tarfile, time

from bup import hashsplit, metadata, options, tree
from bup.commit import commit_message
from bup.compat import argv_bytes, get_argvb
from bup.config import ConfigError
from bup.hashsplit import GIT_MODE_FILE, GIT_MODE_SYMLINK, split_to_blob_or_tree
from bup.helpers import hostname, parse_date_arg, valid_save_name
from bup.io import byte_stream, log, path_msg
from bup.pwdgrp import userfullname, username
from bup.repo import main_repo_location, repo_for_location


optspec = """
bup import-tar [-t] [-c] [-n name] OPTIONS [tarfile]
--
 Modes:
t,tree     output a tree hash
c,commit   output a commit hash
n,name=    save the result under the given name
 Options:
r,remote=  remote repository path
d,date=    date for the commit (seconds since the epoch)
"""

NS_PER_SEC = 1000000000


class _Node:
    __slots__ = 'meta', 'children', 'mode', 'gitmode', 'oid'

    def __init__(self, *, meta=None, children=None, mode=None, gitmode=None,
                 oid=None):
        self.meta = meta
        # children is None for a file, dict for a dir
        self.children = children
        self.mode = mode
        self.gitmode = gitmode
        self.oid = oid

    def is_dir(self):
        return self.children is not None


def _parse_ns(text):
    """Parse a tar PAX header time such as '-1.5' or '1700000000.123456789'."""
    neg = text.startswith('-')
    secs, _, frac = text.lstrip('-').partition('.')
    ns = int(secs or 0) * NS_PER_SEC + int((frac + '0' * 9)[:9])
    return -ns if neg else ns


# we open the file with utf-8/surrogatescape,
# so we can get the bytes back this way later
def _to_bytes(s):
    return s.encode('utf-8', 'surrogateescape')


def _name_parts(name):
    return [p for p in _to_bytes(name).split(b'/') if p not in (b'', b'.')]


def _tar_meta(ti, file_type):
    pax = ti.pax_headers
    meta = metadata.Metadata(frozen=False)
    meta.mode = file_type | (ti.mode & 0o7777)
    meta.uid = ti.uid
    meta.gid = ti.gid
    meta.user = _to_bytes(ti.uname)
    meta.group = _to_bytes(ti.gname)
    meta.rdev = 0
    # PAX headers have these times, TarInfo doesn't
    meta.mtime = _parse_ns(pax['mtime']) if 'mtime' in pax else int(ti.mtime) * NS_PER_SEC
    meta.atime = _parse_ns(pax['atime']) if 'atime' in pax else meta.mtime
    meta.ctime = _parse_ns(pax['ctime']) if 'ctime' in pax else meta.mtime
    if ti.ischr() or ti.isblk():
        meta.rdev = os.makedev(ti.devmajor, ti.devminor)

    xattr_prefix = 'SCHILY.xattr.'
    xattrs = [(_to_bytes(k[len(xattr_prefix):]), _to_bytes(v))
              for k, v in sorted(pax.items()) if k.startswith(xattr_prefix)]
    if xattrs:
        meta.linux_xattr = xattrs
    return meta


class _Importer:
    def __init__(self, repo, split_cfg):
        self._repo = repo
        self._split_cfg = split_cfg
        self._root = _Node(children={})

    def _lookup(self, parts):
        node = self._root
        for part in parts:
            if not node.is_dir():
                return None
            node = node.children.get(part)
            if node is None:
                return None
        return node

    def _parent(self, parts):
        parent = self._root
        for part in parts[:-1]:
            child = parent.children.get(part)
            if child is None:
                child = parent.children[part] = _Node(children={})
            elif not child.is_dir():
                return None
            parent = child
        return parent

    @staticmethod
    def _skip_duplicate(ti):
        log(f'warning: skipping duplicate path {path_msg(ti.name)}\n')

    def add(self, ti, tf):
        parts = _name_parts(ti.name)
        if b'..' in parts:
            log(f'warning: skipping unsupported tar path {path_msg(ti.name)}\n')
            return
        if not parts:
            if ti.isdir():
                if self._root.meta is None:
                    self._root.meta = _tar_meta(ti, S_IFDIR)
                else:
                    self._skip_duplicate(ti)
            return

        parent = self._parent(parts)
        if parent is None:
            log(f'warning: skipping {path_msg(ti.name)}: file inside file?!\n')
            return
        name = parts[-1]
        existing = parent.children.get(name)
        if ti.isdir():
            if existing is None:
                parent.children[name] = _Node(children={},
                                              meta=_tar_meta(ti, S_IFDIR))
            elif existing.is_dir() and existing.meta is None:
                existing.meta = _tar_meta(ti, S_IFDIR)
            else:
                self._skip_duplicate(ti)
            return
        if existing is not None:
            self._skip_duplicate(ti)
            return
        if ti.isreg():
            parent.children[name] = self._file(ti, tf)
        elif ti.issym():
            target = _to_bytes(ti.linkname)
            oid = self._repo.write_symlink(target)
            meta = _tar_meta(ti, S_IFLNK)
            meta.symlink_target = target
            parent.children[name] = _Node(meta=meta, mode=meta.mode,
                                          gitmode=GIT_MODE_SYMLINK, oid=oid)
        elif ti.islnk():
            self._hardlink(parent, name, ti)
        elif ti.ischr() or ti.isblk() or ti.isfifo():
            file_type = S_IFCHR if ti.ischr() else \
                S_IFBLK if ti.isblk() else S_IFIFO
            meta = _tar_meta(ti, file_type)
            oid = self._repo.write_data(b'')
            parent.children[name] = _Node(meta=meta, mode=meta.mode,
                                          gitmode=GIT_MODE_FILE, oid=oid)
        else:
            log(f'warning: skipping unsupported tar member {path_msg(ti.name)}\n')

    def _file(self, ti, tf):
        mode, oid = split_to_blob_or_tree(
            self._repo.write_data, self._repo.write_tree,
            hashsplit.from_config([tf.extractfile(ti)], self._split_cfg))
        meta = _tar_meta(ti, S_IFREG)
        meta.size = ti.size
        return _Node(meta=meta, mode=meta.mode, gitmode=mode, oid=oid)

    def _hardlink(self, parent, name, ti):
        target_parts = _name_parts(ti.linkname)
        target = self._lookup(target_parts) if target_parts else None
        if target is None or target.is_dir():
            log(f'warning: missing hardlink target for {ti.name!r}\n')
            return
        if target.meta.hardlink_target is None:
            target.meta.hardlink_target = b'/' + b'/'.join(target_parts)
        meta = _tar_meta(ti, S_IFMT(target.mode))
        meta.hardlink_target = target.meta.hardlink_target
        meta.size = target.meta.size
        meta.symlink_target = target.meta.symlink_target
        meta.rdev = target.meta.rdev
        parent.children[name] = _Node(meta=meta, mode=target.mode,
                                      gitmode=target.gitmode, oid=target.oid)

    def _write_dir(self, stack, name, node):
        if node.meta is not None:
            node.meta.freeze()
        stack.push(name, node.meta)
        for child_name in sorted(node.children):
            child = node.children[child_name]
            if child.is_dir():
                self._write_dir(stack, child_name, child)
            else:
                child.meta.freeze()
                stack.append_to_current(child_name, child.mode, child.gitmode,
                                        child.oid, child.meta)
        return stack.pop()

    def write(self):
        """Write the accumulated tree and return the root tree id."""
        stack = tree.Stack(self._repo, self._split_cfg)
        return self._write_dir(stack, b'', self._root)


def _commit_tree(repo, tree_oid, parent, date, argv):
    userline = b'%s <%s@%s>' % (userfullname(), username(), hostname())
    return repo.write_commit(tree_oid, parent, userline, date, None,
                             userline, date, None,
                             commit_message(b'bup import-tar', argv))


def main(argv):
    o = options.Options(optspec)
    opt, _, extra = o.parse_bytes(argv[1:])
    if len(extra) > 1:
        o.fatal('at most one tar file may be given')
    if not (opt.tree or opt.commit or opt.name):
        o.fatal('use one or more of -t, -c, -n')
    if opt.name:
        opt.name = argv_bytes(opt.name)
        if not valid_save_name(opt.name):
            o.fatal(f"'{path_msg(opt.name)}' is not a valid branch name.")
    if opt.date:
        try:
            opt.date = int(parse_date_arg(b'--date', opt.date))
        except ValueError as ex:
            o.fatal(str(ex))
    else:
        opt.date = int(time.time())

    loc = main_repo_location(argv_bytes(opt.remote) if opt.remote else None,
                             o.fatal)

    with repo_for_location(loc) as dest:
        try:
            split_cfg = hashsplit.configuration(dest.config_get)
        except ConfigError as ex:
            o.fatal(ex)

        # pylint: disable-next=consider-using-with
        src = open(argv_bytes(extra[0]), 'rb') if extra \
            else nullcontext(byte_stream(sys.stdin))
        with src as f, tarfile.open(fileobj=f, mode='r|*',
                                    encoding='utf-8',
                                    errors='surrogateescape') as tf:
            importer = _Importer(dest, split_cfg)
            for ti in tf:
                importer.add(ti, tf)
            tree_oid = importer.write()

        sys.stdout.flush()
        out = byte_stream(sys.stdout)
        if opt.tree:
            out.write(hexlify(tree_oid))
            out.write(b'\n')
        if opt.commit or opt.name:
            if opt.name:
                refname = b'refs/heads/%s' % opt.name
                parent = dest.read_ref(refname)
            else:
                refname = parent = None
            commit = _commit_tree(dest, tree_oid, parent, opt.date,
                                  get_argvb())
            if opt.commit:
                out.write(hexlify(commit))
                out.write(b'\n')
            if opt.name:
                dest.update_ref(refname, commit, parent)
