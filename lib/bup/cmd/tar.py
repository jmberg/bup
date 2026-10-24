
from stat import S_ISBLK, S_ISCHR, S_ISDIR, S_ISFIFO, S_ISLNK, S_ISREG, S_ISSOCK
import os, sys, tarfile

from bup import options, vfs
from bup.compat import argv_bytes
from bup.helpers import EXIT_FAILURE
from bup.io import byte_stream, log, path_msg
from bup.repo import main_repo_location, repo_for_location


optspec = """
bup tar OPTIONS <vfs-path>
--
r,remote=  remote repository path
"""

NS_PER_SEC = 1000000000


# We tell python it's UTF-8 with surrogateescape,
# so this way we can write binary into the file.
def _str(b):
    return b.decode('utf-8', 'surrogateescape')


def _pax_time(ns):
    sign = '-' if ns < 0 else ''
    secs, nsec = divmod(abs(ns), NS_PER_SEC)
    return f'{sign}{secs}.{nsec:09d}'


class _Exporter:
    def __init__(self, repo, tar):
        self._repo = repo
        self._tar = tar
        self._hardlinks = {}

    def _tarinfo(self, archive_name, meta, typeflag):
        ti = tarfile.TarInfo(_str(archive_name))
        ti.type = typeflag
        ti.mode = meta.mode & 0o7777
        ti.uid = meta.uid or 0
        ti.gid = meta.gid or 0
        ti.uname = _str(meta.user or b'')
        ti.gname = _str(meta.group or b'')
        ti.mtime = (meta.mtime or 0) // NS_PER_SEC
        pax = {}
        for key, ns in (('mtime', meta.mtime),
                        ('atime', meta.atime),
                        ('ctime', meta.ctime)):
            if ns is not None:
                pax[key] = _pax_time(ns)
        for xname, xvalue in meta.linux_xattr or []:
            try:
                key = 'SCHILY.xattr.' + xname.decode('utf-8')
            except UnicodeDecodeError:
                log(f'warning: skipping xattr with non-UTF-8 name'
                    f' {path_msg(xname)} on {path_msg(archive_name)}\n')
                continue
            pax[key] = _str(xvalue)
        ti.pax_headers = pax
        return ti

    def add(self, item, archive_name):
        if S_ISDIR(vfs.item_mode(item)):
            self._add_dir(item, archive_name)
        else:
            self._add_nondir(item, archive_name)

    def _add_dir(self, item, archive_name):
        dot = item
        entries = []
        for name, child in vfs.contents(self._repo, item):
            if name == b'.':
                dot = child
            elif name != b'..':
                entries.append((name, child))
        dot = vfs.augment_item_meta(self._repo, dot)
        self._tar.addfile(self._tarinfo(archive_name, dot.meta, tarfile.DIRTYPE))
        for name, child in entries:
            child_arc = archive_name + name
            if S_ISDIR(vfs.item_mode(child)):
                child_arc += b'/'
            self.add(child, child_arc)

    def _add_nondir(self, item, archive_name):
        mode = vfs.item_mode(item)
        if S_ISSOCK(mode):
            log(f'warning: skipping socket {path_msg(archive_name)}\n')
            return
        item = vfs.augment_item_meta(self._repo, item, include_size=True)
        meta = item.meta
        key = meta.hardlink_target
        if key is not None and key in self._hardlinks:
            ti = self._tarinfo(archive_name, meta, tarfile.LNKTYPE)
            ti.linkname = _str(self._hardlinks[key])
            self._tar.addfile(ti)
            return
        if S_ISLNK(mode):
            ti = self._tarinfo(archive_name, meta, tarfile.SYMTYPE)
            ti.linkname = _str(vfs.readlink(self._repo, item))
            self._tar.addfile(ti)
        elif S_ISREG(mode):
            ti = self._tarinfo(archive_name, meta, tarfile.REGTYPE)
            ti.size = meta.size
            f = vfs.fopen(self._repo, item)
            try:
                self._tar.addfile(ti, f)
            finally:
                f.close()
        elif S_ISCHR(mode) or S_ISBLK(mode):
            typeflag = tarfile.CHRTYPE if S_ISCHR(mode) else tarfile.BLKTYPE
            ti = self._tarinfo(archive_name, meta, typeflag)
            ti.devmajor = os.major(meta.rdev)
            ti.devminor = os.minor(meta.rdev)
            self._tar.addfile(ti)
        elif S_ISFIFO(mode):
            self._tar.addfile(self._tarinfo(archive_name, meta, tarfile.FIFOTYPE))
        else:
            log(f'warning: skipping unsupported entry {path_msg(archive_name)}\n')
            return
        if key is not None:
            self._hardlinks[key] = archive_name


def main(argv):
    o = options.Options(optspec)
    opt, _, extra = o.parse_bytes(argv[1:])
    if len(extra) != 1:
        o.fatal('must specify exactly one path')
    path = argv_bytes(extra[0])
    loc = main_repo_location(argv_bytes(opt.remote) if opt.remote else None,
                             o.fatal)

    with repo_for_location(loc) as repo:
        try:
            resolved = vfs.resolve(repo, path, want_meta=True)
        except vfs.IOError as e:
            log(f'error: {e}\n')
            sys.exit(EXIT_FAILURE)
        if not resolved or resolved[-1][1] is None:
            log(f'error: {path_msg(path)} not found\n')
            sys.exit(EXIT_FAILURE)
        if len(resolved) < 3:
            o.fatal(f'{path_msg(path)} must include a branch and revision')
        item = resolved[-1][1]

        if S_ISDIR(vfs.item_mode(item)):
            archive_name = b'./'
        else:
            archive_name = os.path.basename(os.path.normpath(path))

        sys.stdout.flush()
        out = byte_stream(sys.stdout)
        with tarfile.open(fileobj=out, mode='w|',
                          format=tarfile.PAX_FORMAT,
                          encoding='utf-8', errors='surrogateescape') as tar:
            _Exporter(repo, tar).add(item, archive_name)
