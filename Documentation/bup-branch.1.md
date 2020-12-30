% bup-branch(1) Bup %BUP_VERSION%
% %BUP_DATE%

# NAME

bup-branch - list, create or delete a branch in the bup repository

# SYNOPSIS

bup branch [-r host:path]

bup branch [-r host:path] [-f] \<branch name\> \<committish\>

bup branch [-r host:path] -d [-f] \<branch name\>

# DESCRIPTION

`bup branch` lists, creates or deletes a branch in the bup repository.

A (git) branch is the name given to the save by `bup save -n` and
similar, and is what `bup ls` shows at the top level.

Without arguments, list all branches.  With a branch name and a ref
or commit ID, create a new branch.  To delete a branch, use `-d`.

Branch names follow the same rules as tag names, see `bup-tag`(1).

# OPTIONS

-d, \--delete
:   delete a branch

-f, \--force
:   Overwrite the named branch even if it already exists. With -f, don't
    report a missing branch as an error when deleting.

-r, \--remote=[*user*@]*host*:[*path*], \--remote=URL
:   operate on the specified remote repository, by default via SSH.
    See `bup`(1) REMOTE OPTIONS for further information.

# EXAMPLES

    $ bup branch -f work-copy src
    
    $ bup branch
    src
    work-copy
    
    $ bup ls /work-copy
    ...

    $ bup branch -d work-copy

# SEE ALSO

`bup-save`(1), `bup-tag`(1), `bup-ls`(1)

# BUP

Part of the `bup`(1) suite.
