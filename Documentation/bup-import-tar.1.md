% bup-import-tar(1) Bup %BUP_VERSION%
% %BUP_DATE%

# NAME

bup-import-tar - import a tar archive into a repository

# SYNOPSIS

bup import-tar [-r \<remote\>] [-t] [-c] [-n \<save-name\>] [-d \<date\>] [\<tarfile\>]

# DESCRIPTION

`bup import-tar` reads a tar archive (from `tarfile`, or from standard
input if none is given) and stores its contents in the repository.
Archives in formats supported by Python (PAX, GNU, or ustar) are accepted.

Entries are added in archive order, on name duplicates later ones are
skipped with a warning, as well as names containing `..`. If `./` or `/`
is stored in the archive, it gives the root metadata.

Hard links are imported as a group sharing one entry (the first member in
archive order is the group's main entry).  Symbolic links, device nodes,
FIFOs, and extended attributes are imported, along with mode, ownership,
and nanosecond times when the archive has them.

# OPTIONS

-t, \--tree
:   Create a tree and output its hash.

-c, \--commit
:   Create a commit and output its hash.

-n, \--name=\<save-name\>
:   Save the result under this save name, see `bup-save`(1).

-r, \--remote=[*user*@]*host*:[*path*], \--remote=URL
:   save the data to the specified remote repository, by default via
    SSH.  See bup(1) REMOTE OPTIONS for further information.

-d, \--date=\<date\>
:   Date for the commit, in seconds since the epoch (defaults to the
    current time)

# EXAMPLES

    $ bup tar /laptop/latest | bup import-tar -n laptop-copy
    $ bup import-tar -t laptop.tar
    $ bup import-tar -c -d 1700000000 -n old-laptop laptop.tar

# SEE ALSO

`bup-tar`(1), `bup-split`(1), `bup-save`(1)

# BUP

Part of the `bup`(1) suite.
