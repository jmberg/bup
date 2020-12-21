
from __future__ import absolute_import, print_function

from io import BytesIO

from bup.hashsplit import GIT_MODE_TREE, GIT_MODE_FILE
from bup.hashsplit import split_to_blob_or_tree
from bup.hashsplit import BUP_TREE_BLOBBITS
from bup.helpers import add_error
from bup.metadata import Metadata
from bup.io import path_msg
from bup.git import shalist_item_sort_key, mangle_name
from bup._helpers import RecordHashSplitter


def _write_tree(repo, dir_meta, items, omit_meta=False):
    if not omit_meta:
        if dir_meta is None:
            dir_meta = Metadata()
        metalist = [(b'', dir_meta)]
        metalist += [(shalist_item_sort_key((entry.mode, entry.name, None)),
                      entry.meta)
                     for entry in items if entry.mode != GIT_MODE_TREE]
        metalist.sort(key = lambda x: x[0])
        metadata = BytesIO(b''.join(m[1].encode() for m in metalist))
        mode, oid = split_to_blob_or_tree(repo.write_bupm, repo.write_tree,
                                         [metadata],
                                         keep_boundaries=False)
        shalist = [(mode, b'.bupm', oid)]
    else:
        shalist = []
    shalist += [(entry.gitmode, entry.mangled_name, entry.oid)
                for entry in items]
    return repo.write_tree(shalist)

class TreeItem:
    __slots__ = 'name', 'mode', 'gitmode', 'oid', '_meta'

    def __init__(self, name, mode, gitmode, oid, meta):
        self.name = name
        self.mode = mode
        self.gitmode = gitmode
        self.oid = oid
        self._meta = meta

    @property
    def meta(self):
        if self._meta is not None:
            return self._meta
        return Metadata()

    @property
    def mangled_name(self):
        return mangle_name(self.name, self.mode, self.gitmode)

class RawTreeItem(TreeItem):
    @property
    def mangled_name(self):
        return self.name

def _tree_names_abbreviate(names):
    abbrev = {}
    # build a trie (using dicts) out of all the names
    for name in names:
        level = abbrev
        for c in name:
            if not c in level:
                # use None as the back-pointer, not a valid char
                # (fun for the GC to detect the cycles :-) )
                level[c] = {None: level}
            level = level[c]
    outnames = []
    # and abbreviate all the names
    for name in names:
        out = name
        level = abbrev
        for n in range(len(name)):
            level = level[name[n]]
        while True:
            # backtrack a level
            level = level[None]
            # if it has more than a single char & None,
            # we cannot abbreviate any further
            if len(level) > 2:
                break
            candidate = out[:-1]
            # of course we must not have an invalid name
            if candidate in (b'', b'.', b'..'):
                break;
            out = candidate
        outnames.append(out)
    return outnames


def _tree_entries_abbreviate(items):
    if not items:
        return []
    names = [x.name for x in items]
    outnames = _tree_names_abbreviate(names)
    for idx in range(len(names)):
        items[idx].name = outnames[idx]
    return names


def _write_split_tree(repo, dir_meta, items, level=0):
    items = list(items)
    if not items:
        return _write_tree(repo, dir_meta, items)
    newtree = [] # new tree for this layer, replacing items
    subtree = [] # collect items here for the sublayer trees
    h = RecordHashSplitter(BUP_TREE_BLOBBITS)
    if level > 0:
        # we do the hashsplitting still on the unabbreviated names
        # (that are returned), so that we have a bit more data to
        # feed to the hash splitter - after abbreviation it's not
        # a lot, so splits a lot less, and we want more data since
        # the trees already have a lot of data we ignore (the mode
        # string and oid for each entry); if we have less data to
        # feed, we split less and each tree becomes bigger, so that
        # we will almost never end up with more than one level but
        # the top-level tree object is large.
        #
        # In a sample folder I had, this makes a difference between
        #  - 1 level and a 4023 entry (~133k) top-level tree, or
        #  - 2 levels and a 51-entry (1.5k) top-level tree,
        # which is far more efficient on incremental backups since
        # at the very least the top-level tree has to be rewritten
        # every time you update the folder in any way. The absolute
        # difference between the two cases was just 1529 bytes and
        # 49 tree objects.
        # (the test case was a ~370k entry maildir).
        itemnames = _tree_entries_abbreviate(items)
    else:
        itemnames = [item.name for item in items]
    for idx in range(len(items)):
        subtree.append(items[idx])
        # We only feed the name into the hashsplitter because otherwise
        # minor changes (changing the content of the file, or changing
        # a dir to a file or vice versa) can have major ripple effects
        # on the layout of the split tree structure, which can result in
        # a lot of extra objects being written.
        # Unfortunately this also means that the trees will (on average) be
        # larger (due to the 64 byte) window, but the expected chunk size is
        # relatively small so that shouldn't really be an issue.
        split, bits = h.feed(itemnames[idx])
        # Note also that we don't create subtrees with just a single entry
        # (unless they're the last entry), since that would not only be
        # wasteful, but also lead to recursion if some filename all by itself
        # contains a split point - since it's propagated to the next layer up.
        # This leads to a worst-case depth of ceil(log2(# of names)), which
        # is somewhat wasteful, but not *that* bad. Other solutions to this
        # could be devised, e.g. applying some bit perturbation to the names
        # depending on the level.
        if (len(subtree) > 1 and split) or idx == len(items) - 1:
            all_in_one_tree = not newtree and idx == len(items) - 1
            if all_in_one_tree and level > 0:
                # insert the sentinel file (empty blob)
                sentinel_sha = repo.write_data(b'')
                subtree.append(RawTreeItem(b'%d.bupd' % level, GIT_MODE_FILE,
                                           GIT_MODE_FILE, sentinel_sha, None))
            meta = None
            if all_in_one_tree:
                meta = dir_meta
            omit_meta = level > 0 and not all_in_one_tree
            treeid = _write_tree(repo, meta, subtree, omit_meta=omit_meta)
            # if we've reached the end with an empty newtree,
            # just return this new tree (which is complete)
            if all_in_one_tree:
                return treeid
            # use the first subtree filename for the new tree,
            # we'll abbreviate later
            newtree.append(RawTreeItem(subtree[0].name, GIT_MODE_TREE,
                                       GIT_MODE_TREE, treeid, None))
            # start over
            subtree = []
    # If we have a real list, just subject it to tree-splitting
    # recursively. We have (above) put the first filename as the
    # next layer's tree, so we can do more targeted lookups when
    # reading the data back.
    # However, we only abbreviate it later to have more input to
    # the hashsplitting algorithm.
    return _write_split_tree(repo, dir_meta, newtree, level + 1)


class StackDir:
    __slots__ = 'name', 'items', 'meta'

    def __init__(self, name, meta):
        self.name = name
        self.meta = meta
        self.items = []

class Stack:
    def __init__(self, repo):
        self.stack = []
        self.repo = repo
        self.use_treesplit = repo.config(b'bup.treesplit', opttype='bool')

    def __len__(self):
        return len(self.stack)

    def namestack(self):
        for p in self.stack:
            yield p.name

    def push(self, name, meta):
        self.stack.append(StackDir(name, meta))

    def _clean(self, tree):
        names_seen = set()
        items = []
        for item in tree.items:
            if item.name in names_seen:
                parent_path = b'/'.join(n for n in self.namestack()) + b'/'
                add_error('error: ignoring duplicate path %s in %s'
                          % (path_msg(item.name), path_msg(parent_path)))
            else:
                names_seen.add(item.name)
                items.append(item)
        tree.items = items

    def _write(self, tree):
        self._clean(tree)

        tree.items.sort(key=lambda x: x.name)

        if not self.use_treesplit:
            return _write_tree(self.repo, tree.meta, tree.items)
        return _write_split_tree(self.repo, tree.meta, tree.items)

    def pop(self, override_tree=None, override_meta=None):
        tree = self.stack.pop()
        if override_meta is not None:
            tree.meta = override_meta
        if not override_tree: # caution - False happens, not just None
            tree_oid = self._write(tree)
        else:
            tree_oid = override_tree
        if len(self):
            self.append_to_current(tree.name, GIT_MODE_TREE, GIT_MODE_TREE,
                                   tree_oid, None)
        return tree_oid

    def append_to_current(self, name, mode, gitmode, oid, meta):
        self.stack[-1].items.append(TreeItem(name, mode, gitmode, oid, meta))
