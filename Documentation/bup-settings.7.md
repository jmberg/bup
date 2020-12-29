% bup-settings(7) Bup %BUP_VERSION%
% Johannes Berg <johannes@sipsolutions.net>
% %BUP_DATE%

# NAME

bup-settings - config file settings used by bup

# DESCRIPTION

When bup writes to a repository, it uses some settings (that may be shared
with git) from the repository's `config` file. This page documents the
settings that are used.

# SETTINGS

pack.compression
: A git setting, bup will honor this setting for the compression level
  used inside pack files. If not given, fall back to `core.compression`,
  and if that isn't given either will default to 1.
  A compression level given on the command-line overrides this.

core.compression
: Also a git setting; like git, bup will use this if `pack.compression`
  doesn't exist. See the documentation there.

pack.packSizeLimit
: Another git setting, used to limit pack file size. Note that bup will
  honor this value from the repository written to (which may be remote)
  and also from the local repository (where the index is) if different.
  The default value is 1e9 bytes, i.e. about 0.93 GiB.
  Note that bup may run over this limit by a chunk. However, setting it
  to e.g. "2g" (2 GiB) would still mean that all objects in the pack can
  be addressed by a 31-bit offset, and thus need no large offset in the
  idx file.

bup.dumb-server
: This setting determines the "dumb server mode", see `bup-server`(1).

bup.blobbits
: This setting determines the number of fixed bits in the hash-split
  algorithm that lead to a chunk boundary, and thus the average size of
  objects. This represents a trade-off between the efficiency of the
  deduplication (fewer bits means better deduplication) and the amount
  of metadata to keep on disk and RAM usage during repo operations
  (more bits means fewer objects, means less metadata space and RAM use).
  The expected average block size is expected to be 2^bits (1 << bits),
  a sufficiently small change in a file would lead to that much new data
  to be saved (plus tree metadata). The maximum blob size is 4x that.
: The default of this setting is 13 for backward compatibility, but it
  is recommended to change this to a higher value (e.g. 16) on all but
  very small repos.
: NOTE: Changing this value in an existing repository is *strongly
  discouraged*. It would cause a subsequent store of anything but files
  that were not split to store all data (and to some extent metadata) in
  the repository again, rather than deduplicating. Consider the disk
  usage of this to be mostly equivalent to starting a new repository.

# BUP

Part of the `bup`(1) suite.
