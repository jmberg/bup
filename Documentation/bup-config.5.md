% bup-config(5) Bup %BUP_VERSION%
% Rob Browning <rlb@defaultvalue.org>
% %BUP_DATE%

# NAME

bup-config - bup configuration options

# DESCRIPTION

`bup` specific options may be set in the relevant `git` config
(`git-config(1)`), and `bup` also respects some existing `git`
options.

# OPTIONS

bup.split-trees
:   When this boolean option is set to true, `bup` will attempt to
    split trees (directories) when writing to the repository during,
    for example `bup save ...`, `bup gc ..`, etc.  This can notably
    decrease the size of the new data added to the repository when
    large directories have changed (e.g. large active Maildirs).  See
    "Handling large directories" in the DESIGN in the `bup` source for
    additional information.

git.packSizeLimit
:   Respected when writing pack files (e.g. via `bup save ...`).

pack.compression
:   A git setting, bup will honor this setting for the compression level
    used inside pack files. If not given, fall back to `core.compression`,
    and if that isn't given either will default to 1.
    A compression level given on the command-line overrides this.

core.compression
:   Also a git setting; like git, bup will use this if `pack.compression`
    doesn't exist. See the documentation there.

# SEE ALSO

`git-config`(1)

# BUP

Part of the `bup`(1) suite.
