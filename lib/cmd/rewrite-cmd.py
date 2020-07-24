#!/bin/sh
"""": # -*-python-*-
# https://sourceware.org/bugzilla/show_bug.cgi?id=26034
export "BUP_ARGV_0"="$0"
arg_i=1
for arg in "$@"; do
    export "BUP_ARGV_${arg_i}"="$arg"
    shift
    arg_i=$((arg_i + 1))
done
# Here to end of preamble replaced during install
bup_python="$(dirname "$0")/../../config/bin/python" || exit $?
exec "$bup_python" "$0"
"""
# end of bup preamble

from __future__ import absolute_import, print_function
from binascii import hexlify
from errno import EACCES
import math, os, stat, sys, time

sys.path[:0] = [os.path.dirname(os.path.realpath(__file__)) + '/..']

from bup import compat, hashsplit, git, options, client, repo, metadata, vfs
from bup.compat import argv_bytes, environ
from bup.hashsplit import GIT_MODE_TREE, GIT_MODE_FILE, GIT_MODE_SYMLINK
from bup.helpers import (add_error, grafted_path_components, handle_ctrl_c,
                         hostname, istty2, log, parse_date_or_fatal, parse_num,
                         path_components, progress, qprogress, resolve_parent,
                         saved_errors, stripped_path_components,
                         valid_save_name)
from bup.io import byte_stream, path_msg
from bup.pwdgrp import userfullname, username
from bup.tree import Stack


optspec = """
bup rewrite -s srcrepo <branch-name>
--
s,source=  source repository
r,remote=  remote destination repository
"""
o = options.Options(optspec)
opt, flags, extra = o.parse(compat.argv[1:])

if len(extra) != 1:
    o.fatal("no branch name given")

name = argv_bytes(extra[0])
if name and not valid_save_name(name):
    o.fatal("'%s' is not a valid branch name" % path_msg(name))

refname = b'refs/heads/%s' % name

dstrepo = repo.from_opts(opt)
use_treesplit = dstrepo.config(b'bup.treesplit', opttype='bool')
blobbits = dstrepo.config(b'bup.blobbits', opttype='int')

# FIXME: support remote source repos ... probably after we
# unify the handling?
srcrepo = repo.LocalRepo(argv_bytes(opt.source))

oldref = dstrepo.read_ref(refname)
if oldref is not None:
    o.fatal("branch '%s' already exists in the destination repo" % path_msg(name))

handle_ctrl_c()

# Maintain a stack of information representing the current location in
# the archive being constructed.

vfs_branch = vfs.resolve(srcrepo, name)
item = vfs_branch[-1][1]
contents = vfs.contents(srcrepo, item)
contents = list(contents)
commits = [c for c in contents if isinstance(c[1], vfs.Commit)]

def vfs_walk_recursively(srcrepo, vfs_item, fullname=b''):
    for name, item in vfs.contents(srcrepo, vfs_item):
        if name in (b'.', b'..'):
            continue
        if stat.S_ISDIR(vfs.item_mode(item)):
            # yield from
            for n, i in vfs_walk_recursively(srcrepo, item, fullname + b'/' + name):
                yield n, i
            # and the dir itself
            yield fullname + b'/' + name + b'/', item
        else:
            yield fullname + b'/' + name, item

oldref = None

for commit_vfs_name, commit in commits:
    stack = Stack()

    print("Rewriting %s ..." % path_msg(commit_vfs_name))

    for fullname, item in vfs_walk_recursively(srcrepo, commit):
        (dirn, file) = os.path.split(fullname)
        assert(dirn.startswith(b'/'))
        dirp = path_components(dirn)

        # If switching to a new sub-tree, finish the current sub-tree.
        while list(stack.namestack) > [x[0] for x in dirp]:
            stack, _ = stack.pop(dstrepo, use_treesplit=use_treesplit)

        # If switching to a new sub-tree, start a new sub-tree.
        for path_component in dirp[len(stack):]:
            dir_name, fs_path = path_component

            dir_item = vfs.resolve(srcrepo, name + b'/' + commit_vfs_name + b'/' + fs_path)
            stack = stack.push(dir_name, dir_item[-1][1].meta)

        if not file:
            if len(stack) == 1:
                continue # We're at the top level -- keep the current root dir
            # Since there's no filename, this is a subdir -- finish it.
            stack, newtree = stack.pop(dstrepo, use_treesplit=use_treesplit)
            continue

        id = None
        vfs_mode = vfs.item_mode(item)
        if stat.S_ISREG(vfs_mode):
            with vfs.tree_data_reader(srcrepo, item.oid) as f:
                (mode, id) = hashsplit.split_to_blob_or_tree(
                                        dstrepo.write_data,
                                        dstrepo.write_tree, [f],
                                        keep_boundaries=False,
                                        #progress=progress_report,
                                        blobbits=blobbits)
        elif stat.S_ISDIR(vfs_mode):
            assert(0)  # handled above
        elif stat.S_ISLNK(vfs_mode):
            (mode, id) = (GIT_MODE_SYMLINK, dstrepo.write_symlink(item.meta.symlink_target))
        else:
            # Everything else should be fully described by its
            # metadata, so just record an empty blob, so the paths
            # in the tree and .bupm will match up.
            (mode, id) = (GIT_MODE_FILE, dstrepo.write_data(b''))

        if id:
            stack.append(file, vfs_mode, mode, id, item.meta)

    # pop all parts above the root folder
    while not stack.parent.nothing:
        stack, _ = stack.pop(dstrepo, use_treesplit=use_treesplit)

    stack, tree = stack.pop(dstrepo, override_meta=commit.meta,
                            use_treesplit=use_treesplit)

    cat = srcrepo.cat(hexlify(commit.coid))
    info = next(cat)
    data = b''.join(cat)
    ci = git.parse_commit(data)
    oldref = dstrepo.write_commit(tree, oldref,
                                  ci.author_name + b' <' + ci.author_mail + b'>',
                                  ci.author_sec, ci.author_offset,
                                  ci.committer_name + b' <' + ci.committer_mail + b'>',
                                  ci.committer_sec, ci.committer_offset,
                                  ci.message)

dstrepo.update_ref(refname, oldref, None)

srcrepo.close()
dstrepo.close()
