
from __future__ import absolute_import, print_function

from io import BytesIO

from bup.hashsplit import GIT_MODE_TREE, GIT_MODE_FILE, GIT_MODE_SYMLINK
from bup.hashsplit import split_to_blob_or_tree
from bup.helpers import add_error
from bup.io import path_msg
from bup.git import shalist_item_sort_key, mangle_name


class StackDir:
    __slots__ = 'name', 'items', 'meta', 'parent'

    def __init__(self, name, meta, parent):
        self.name = name
        self.meta = meta
        self.items = []
        self.parent = parent

    def push(self, name, meta):
        return StackDir(name, meta, self)

    @property
    def nothing(self):
        return isinstance(self, Stack)

    def __len__(self):
        r = 0
        p = self
        while not p.nothing:
            r += 1
            p = p.parent
        return r

    @property
    def namestack(self):
        p = self
        r = []
        while not p.nothing:
            r.insert(0, p.name)
            p = p.parent
        return r

    def _clean(self):
        names_seen = set()
        items = []
        for item in self.items:
            if item.name in names_seen:
                parent_path = b'/'.join(n for n in self.namestack) + b'/'
                add_error('error: ignoring duplicate path %s in %s'
                          % (path_msg(item.name), path_msg(parent_path)))
            else:
                names_seen.add(item.name)
                items.append(item)
        self.items = items

    def _write(self, w):
        self._clean()

        metalist = [(b'', self.meta)]
        metalist += [(shalist_item_sort_key((entry.mode, entry.name, None)),
                      entry.meta)
                     for entry in self.items if entry.mode != GIT_MODE_TREE]
        metalist.sort(key = lambda x: x[0])
        metadata = BytesIO(b''.join(m[1].encode() for m in metalist))
        mode, oid = split_to_blob_or_tree(w.new_blob, w.new_tree,
                                         [metadata],
                                         keep_boundaries=False)
        shalist = [(mode, b'.bupm', oid)]
        shalist += [(entry.gitmode,
                     mangle_name(entry.name, entry.mode, entry.gitmode),
                     entry.oid)
                    for entry in self.items]
        return w.new_tree(shalist)

    def pop(self, w, override_tree=None, override_meta=None):
        assert self.parent is not None
        if override_meta is not None:
            self.meta = override_meta
        if not override_tree: # caution - False happens, not just None
            tree = self._write(w)
        else:
            tree = override_tree
        self.parent.append(self.name, GIT_MODE_TREE, GIT_MODE_TREE, tree, None)
        return self.parent, tree

    def append(self, name, mode, gitmode, oid, meta):
        self.items.append(TreeItem(name, mode, gitmode, oid, meta))

class Stack(StackDir):
    def __init__(self):
        StackDir.__init__(self, b'', None, None)

    def pop(self, *args, **kw):
        assert False

class TreeItem:
    __slots__ = 'name', 'mode', 'gitmode', 'oid', 'meta'

    def __init__(self, name, mode, gitmode, oid, meta):
        self.name = name
        self.mode = mode
        self.gitmode = gitmode
        self.oid = oid
        self.meta = meta
