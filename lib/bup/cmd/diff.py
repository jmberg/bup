
from binascii import hexlify
import stat

from bup import options, repo, vfs
from bup.compat import argv_bytes
from bup.io import path_msg


optspec = """
bup diff ref1 ref2
--
r,remote=    hostname:/path/to/repo of remote repository
R,recursive  recursively show the differences
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    if len(extra) != 2:
        o.fatal('must give two references to compare')

    ref1 = argv_bytes(extra[0])
    ref2 = argv_bytes(extra[1])

    if opt.remote:
        opt.remote = argv_bytes(opt.remote)

    class NamedItem:
        def __init__(self, name, item):
            self.name = name
            self.item = item

        def isdir(self):
            return stat.S_ISDIR(vfs.item_mode(self.item))

        def __repr__(self):
            return '<NamedItem(%r|%s)>' % (self.name, hexlify(self.item.oid).decode('ascii'))

    def next_item(iter):
        try:
            name, item = next(iter)
            if name == b'.': # always skip this
                name, item = next(iter)
            return NamedItem(name, item)
        except StopIteration:
            return None

    def empty():
        # empty iterator - yield statement makes it one
        return
        yield

    def show_diff(opt, r, left, right, pfx=b''):
        if left is not None:
            l_contents = vfs.contents(r, left, want_meta=False)
        else:
            l_contents = empty()

        if right is not None:
            r_contents = vfs.contents(r, right, want_meta=False)
        else:
            r_contents = empty()

        l_cur = next_item(l_contents)
        r_cur = next_item(r_contents)

        while l_cur is not None or r_cur is not None:
            if l_cur is None or (r_cur is not None and l_cur.name > r_cur.name):
                print(' A %s%s' % (path_msg(pfx + r_cur.name), '/' if r_cur.isdir() else ''))
                if opt.recursive and r_cur.isdir():
                    show_diff(opt, r, None, r_cur.item, pfx=pfx + r_cur.name + b'/')
                r_cur = next_item(r_contents)
            elif r_cur is None or l_cur.name < r_cur.name:
                print(' D %s%s' % (path_msg(pfx + l_cur.name), '/' if l_cur.isdir() else ''))
                if opt.recursive and l_cur.isdir():
                    show_diff(opt, r, l_cur.item, None, pfx=pfx + l_cur.name + b'/')
                l_cur = next_item(l_contents)
            elif l_cur.name == r_cur.name:
                if l_cur.item.oid != r_cur.item.oid:
                    if opt.recursive:
                        if l_cur.isdir() and r_cur.isdir():
                            print(' M %s/' % path_msg(pfx + l_cur.name))
                            show_diff(opt, r, l_cur.item, r_cur.item, pfx=pfx + l_cur.name + b'/')
                        elif l_cur.isdir():
                            show_diff(opt, r, l_cur.item, None, pfx=pfx + l_cur.name + b'/')
                            print(' M %s/' % path_msg(pfx + l_cur.name))
                        elif r_cur.isdir():
                            print(' M %s/' % path_msg(pfx + l_cur.name))
                            show_diff(opt, r, None, r_cur.item, pfx=pfx + l_cur.name + b'/')
                        else:
                            print(' M %s' % path_msg(pfx + l_cur.name))
                    else:
                        print(' M %s%s' % (path_msg(pfx + l_cur.name), '/' if l_cur.isdir() else ''))
                l_cur = next_item(l_contents)
                r_cur = next_item(r_contents)

    with repo.from_opts(opt) as r:
        res1 = vfs.resolve(r, ref1, want_meta=False)
        res2 = vfs.resolve(r, ref2, want_meta=False)
        i1 = res1[-1][1]
        i2 = res2[-1][1]

        show_diff(opt, r, i1, i2)
