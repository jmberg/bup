
from binascii import hexlify
import os, stat, sys
import sqlite3

from bup import hashsplit, git, options, repo, metadata, vfs
from bup.compat import argv_bytes
from bup.hashsplit import GIT_MODE_FILE, GIT_MODE_SYMLINK
from bup.helpers import (handle_ctrl_c, path_components,
                         valid_save_name)
from bup.io import path_msg
from bup.tree import Stack


optspec = """
bup rewrite -s srcrepo <branch-name>
--
s,source=    source repository
r,remote=    remote destination repository
work-db=     work database filename (required, can be deleted after running)
"""
def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    if len(extra) != 1:
        o.fatal("no branch name given")

    src = argv_bytes(extra[0])
    if b':' in src:
        src, dst = src.split(b':', 1)
    else:
        dst = src
    if not valid_save_name(src):
        o.fatal("'%s' is not a valid branch name" % path_msg(src))
    if not valid_save_name(dst):
        o.fatal("'%s' is not a valid branch name" % path_msg(dst))

    srcref = b'refs/heads/%s' % src
    dstref = b'refs/heads/%s' % dst

    if opt.remote:
        opt.remote = argv_bytes(opt.remote)

    if not opt.work_db:
        o.fatal('--work-db argument is required')

    workdb_conn = sqlite3.connect(opt.work_db)
    workdb_conn.text_factory = bytes
    wdbc = workdb_conn.cursor()

    # FIXME: support remote source repos ... probably after we
    # unify the handling?
    with repo.from_opts(opt) as dstrepo, \
         repo.LocalRepo(argv_bytes(opt.source)) as srcrepo:
        blobbits = dstrepo.config_get(b'bup.split.files', opttype='int') or 13
        treesplit = dstrepo.config_get(b'bup.split.trees', opttype='bool') or False

        tablename = 'mapping_to_bits_%d_treesplit_%d' % (blobbits, treesplit)
        wdbc.execute('CREATE TABLE IF NOT EXISTS %s (src BLOB PRIMARY KEY, dst BLOB NOT NULL, mode INTEGER, size INTEGER) WITHOUT ROWID' % tablename)


        oldref = dstrepo.read_ref(dstref)
        if oldref is not None:
            o.fatal("branch '%s' already exists in the destination repo" % path_msg(dst))

        handle_ctrl_c()

        # Maintain a stack of information representing the current location in
        # the archive being constructed.

        vfs_branch = vfs.resolve(srcrepo, src)
        item = vfs_branch[-1][1]
        contents = vfs.contents(srcrepo, item)
        contents = list(contents)
        commits = [c for c in contents if isinstance(c[1], vfs.Commit)]

        def converted_already(dstrepo, item, vfs_dir):
            size = -1 # irrelevant
            mode = item.meta
            if isinstance(item.meta, metadata.Metadata):
                size = item.meta.size
                mode = item.meta.mode
            # if we know the size, and the oid exists already
            # (small file w/o hashsplit) then simply return it
            # can't do that if it's a directory, since it might exist
            # but in the non-augmented version, so dirs always go
            # through the database lookup
            if not vfs_dir and size is not None and dstrepo.exists(item.oid):
                return item.oid, mode
            wdbc.execute('SELECT dst, mode, size FROM %s WHERE src = ?' % tablename,
                         (item.oid, ))
            data = wdbc.fetchone()
            # if it's not found, then we don't know anything
            if not data:
                return None, None
            dst, mode, size = data
            # augment the size if appropriate
            if size is not None and isinstance(item.meta, metadata.Metadata):
                assert item.meta.size is None or item.meta.size == size
                item.meta.size = size
            # if we have it in the DB and in the destination repo, return it
            if dstrepo.exists(dst):
                return dst, mode
            # this only happens if you reuse a database
            return None, None

        def vfs_walk_recursively(srcrepo, dstrepo, vfs_item, fullname=b''):
            for name, item in vfs.contents(srcrepo, vfs_item):
                if name in (b'.', b'..'):
                    continue
                itemname = fullname + b'/' + name
                if stat.S_ISDIR(vfs.item_mode(item)):
                    if converted_already(dstrepo, item, True)[0] is None:
                        yield from vfs_walk_recursively(srcrepo, dstrepo, item,
                                                        fullname=itemname)
                    # and the dir itself
                    yield itemname + b'/', item
                else:
                    yield itemname, item

        try:
            for commit_vfs_name, commit in commits:
                stack = Stack(dstrepo)

                print("Rewriting %s ..." % path_msg(commit_vfs_name))
                sys.stdout.flush()

                for fullname, item in vfs_walk_recursively(srcrepo, dstrepo, commit):
                    (dirn, file) = os.path.split(fullname)
                    assert(dirn.startswith(b'/'))
                    dirp = path_components(dirn)

                    # If switching to a new sub-tree, finish the current sub-tree.
                    while list(stack.path()) > [x[0] for x in dirp]:
                        stack.pop()

                    # If switching to a new sub-tree, start a new sub-tree.
                    for path_component in dirp[len(stack):]:
                        dir_name, fs_path = path_component

                        dir_item = vfs.resolve(srcrepo, src + b'/' + commit_vfs_name + b'/' + fs_path)
                        stack.push(dir_name, dir_item[-1][1].meta)

                    # check if we already handled this item
                    id, mode = converted_already(dstrepo, item, not file)

                    if not file:
                        if len(stack) == 1:
                            continue # We're at the top level -- keep the current root dir
                        # Since there's no filename, this is a subdir -- finish it.
                        newtree = stack.pop(override_tree=id)
                        if id is None:
                            wdbc.execute('INSERT INTO %s (src, dst) VALUES (?, ?)' % tablename,
                                         (item.oid, newtree ))
                        continue

                    vfs_mode = vfs.item_mode(item)

                    # already converted - id is known, item.meta was updated if needed
                    # (in converted_already()), and the proper new mode was returned
                    if id is not None:
                        assert mode is not None, id
                        stack.append_to_current(file, vfs_mode, mode, id, item.meta)
                        continue

                    item_size = None
                    size_augmented = False
                    if stat.S_ISREG(vfs_mode):
                        item_size = 0
                        def write_data(data):
                            nonlocal item_size
                            item_size += len(data)
                            return dstrepo.write_data(data)
                        with vfs.tree_data_reader(srcrepo, item.oid) as f:
                            (mode, id) = hashsplit.split_to_blob_or_tree(
                                                    write_data, dstrepo.write_tree, [f],
                                                    keep_boundaries=False,
                                                    #progress=progress_report,
                                                    blobbits=blobbits)
                        if isinstance(item.meta, metadata.Metadata):
                            if item.meta.size is None:
                                item.meta.size = item_size
                                size_augmented = True
                            else:
                                assert item.meta.size == item_size
                    elif stat.S_ISDIR(vfs_mode):
                        assert(0)  # handled above
                    elif stat.S_ISLNK(vfs_mode):
                        (mode, id) = (GIT_MODE_SYMLINK, dstrepo.write_symlink(item.meta.symlink_target))
                        if item.meta.size is None:
                            item.meta.size = len(item.meta.symlink_target)
                            size_augmented = True
                        else:
                            assert item.meta.size == len(item.meta.symlink_target)
                        item_size = len(item.meta.symlink_target)
                    else:
                        # Everything else should be fully described by its
                        # metadata, so just record an empty blob, so the paths
                        # in the tree and .bupm will match up.
                        (mode, id) = (GIT_MODE_FILE, dstrepo.write_data(b''))

                    if id:
                        if size_augmented or id != item.oid:
                            wdbc.execute('INSERT INTO %s (src, dst, mode, size) VALUES (?, ?, ?, ?)' % tablename,
                                         (item.oid, id, mode, item_size))
                        stack.append_to_current(file, vfs_mode, mode, id, item.meta)

                # pop all parts above the root folder
                while len(stack) > 1:
                    stack.pop()

                # and the root - separately to get the tree
                tree = stack.pop()

                cat = srcrepo.cat(hexlify(commit.coid))
                info = next(cat)
                data = b''.join(cat)
                ci = git.parse_commit(data)
                newref = dstrepo.write_commit(tree, oldref,
                                              ci.author_name + b' <' + ci.author_mail + b'>',
                                              ci.author_sec, ci.author_offset,
                                              ci.committer_name + b' <' + ci.committer_mail + b'>',
                                              ci.committer_sec, ci.committer_offset,
                                              ci.message)

                dstrepo.update_ref(dstref, newref, oldref)
                oldref = newref
        finally:
            # we can always commit since those are the things we did OK
            workdb_conn.commit()
            workdb_conn.close()
