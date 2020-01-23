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

# BUP

Part of the `bup`(1) suite.
