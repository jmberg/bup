% bup-config(5) Bup %BUP_VERSION%
% Rob Browning <rlb@defaultvalue.org>
% %BUP_DATE%

# NAME

bup-config - bup configuration options

# DESCRIPTION

The following options may be set in the relevant `git` config
(`git-config(1)`).

# OPTIONS

bup.split-trees
:   When this boolean option is set to true, `bup` will attempt to
    split trees (directories) when writing to the repository during,
    for example `bup save ...`, `bup gc ..`, etc.  This can notably
    decrease the size of the new data added to the repository when
    large directories have changed (e.g. large active Maildirs).  See
    "Handling large directories" in the DESIGN in the `bup` source for
    additional information.

    *NOTE:*
    It has some performance impact to use the same index for saving to
    different repositories with different values of this setting. The
    index stores object hashes, including for directories, whenever it
    is used for saving. This is used to not read a directory again if
    the index otherwise indicates that it didn't change and the object
    already exists in the destination repository. Since the hash isn't
    the same with and without this setting, the object won't exist in
    a repository with a different value. As a result, any (usually big)
    directory using tree splitting will be re-read and its object(s)
    recalculated whenever the previous save was to a repository with a
    different value of this setting.

pack.packSizeLimit
:   Respected when writing pack files (e.g. via `bup save ...`).
    Note that bup will honor this value from the repository written to
    (which may be remote) and also from the local repository (where the
    index is) if different.
    The default value is 1e9 bytes, i.e. about 0.93 GiB.
    Note that bup may run over this limit by a chunk. However, setting it
    to e.g. "2g" (2 GiB) would still mean that all objects in the pack can
    be addressed by a 31-bit offset, and thus need no large offset in the
    idx file.

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
