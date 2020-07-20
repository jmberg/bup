% bup-diff(1) Bup %BUP_VERSION%
% %BUP_DATE%

# NAME

bup-diff - show differences between two paths in a bup repository

# SYNOPSIS

bup diff [-r host:path] [-R] [-m] \<path1\> \<path2\>

# DESCRIPTION

`bup diff` compares two paths in the bup repository and prints the
differences between them.  Paths are interpreted as in `bup-ls`(1),
so they can name a branch, a save, or a directory inside a save, for
example `src/latest/home/`.

Each line of output starts with a status character:

` A`
:   the name exists only in *path2*

` D`
:   the name exists only in *path1*

` M`
:   the name exists in both paths, but its type or content differs

` m`
:   the name exists in both paths with identical content, but its
    metadata differs.  Only reported with `-m`.  Metadata means the
    mode, owner, group, mtime, device numbers, extended attributes,
    and ACLs.  Access and change times are not compared, since they
    change all the time.

An item whose type changes, for example from a file to a directory, is
reported as ` D` for the old type followed by ` A` for the new type.
This is reported with or without `-m`.

Without `-m`, a directory is reported as ` M` only if something inside
it is added, deleted, changed in type, or changed in content.  Its own
metadata is not compared, even though a change to it changes the tree.
With `-m`, a directory is reported as ` M` if anything inside it is
added, deleted, changed in type, or changed in content.  Otherwise it
is reported as ` m` if its own metadata or the metadata of something
inside it differs.

Names that are directories are followed by a `/`.  Entries that exist
identically on both sides (subject to `-m`) are not printed.

Without `-R`, only the immediate children of the two paths are
compared.  With `-R`, directories matching on both sides are
compared recursively.

# OPTIONS

-r, \--remote=[*user*@]*host*:[*path*], \--remote=URL
:   operate on the specified remote repository, by default via SSH.
    See `bup`(1) REMOTE OPTIONS for further information.

-R, \--recursive
:   compare the contents of directories recursively

-m, \--metadata
:   also compare metadata.  This is slower, since the metadata of every
    item being compared must be read.  Without it, only content and
    type are compared.

# EXAMPLES

    $ bup diff src/latest src/2024-01-02-120000
     M config
     A new-file
     D old-file
    
    $ bup diff -R src/latest src/2024-01-02-120000
     M config
     A new-dir/
     D old-dir/

# SEE ALSO

`bup-ls`(1), `bup-save`(1)

# BUP

Part of the `bup`(1) suite.
