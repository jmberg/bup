% bup-show(1) Bup %BUP_VERSION%
% %BUP_DATE%

# NAME

bup-show - show a commit, tree, or blob in a bup repository

# SYNOPSIS

bup show [-r host:path] [-R] [-e] \<ref\>

# DESCRIPTION

`bup show` prints the object named by *ref* from the bup repository.
The output depends on the type of the object:

commit
:   the commit in the style of `git show --format=raw`, including its
    tree, parents, author, committer, and message etc.

tree
:   the immediate entries of the tree, one per line, in the style of
    `git ls-tree`: mode, type, object ID, and name

blob
:   the raw contents of the blob, written to standard output unchanged

*ref* may be a branch name, as listed by `bup-branch`(1), or a full
hexadecimal object ID.  Save names such as `src/latest` are not
branch names and are not accepted; use `bup-ls`(1) to find the tree
of a save and pass its object ID instead.

Each tree entry in the output is shown as `mode type oid  name`.  With
`-R`, the entries of subtrees are printed beneath their parent, indented
with `|`.

# OPTIONS

-r, \--remote=[*user*@]*host*:[*path*], \--remote=URL
:   operate on the specified remote repository, by default via SSH.
    See `bup`(1) REMOTE OPTIONS for further information.

-R, \--recurse
:   print the entries of tree objects recursively

-e, \--exists
:   prefix each tree entry with `!` if the object it names is missing
    from the repository, instead of reporting an error.  Entries that
    are present are prefixed with two spaces.  This option is only
    supported for local repositories and cannot be combined with
    `-r`.

# EXAMPLES

    $ bup show src
    commit 3f2c1d0c5a7e0a8c0b8d9e2f4a6b1c7d8e9f0a1b
    tree 9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b
    author Example <user@example.com> 1700000000 +0000
    committer Example <user@example.com> 1700000000 +0000

        bup save

    $ bup show 9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b
    100644 blob 4b825dc642cb6eb9a060e54bf8d69288fbee4904   a.txt
    040000 tree 1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d   sub

    $ bup show -R -r backup.example.com:/srv/bup 9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b

# SEE ALSO

`bup-ls`(1), `bup-branch`(1), `bup-cat-file`(1)

# BUP

Part of the `bup`(1) suite.
