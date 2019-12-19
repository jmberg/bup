% bup-genkey(1) Bup %BUP_VERSION%
% Johannes Berg <johannes@sipsolutions.net>
% %BUP_DATE%

# NAME

bup-genkey - generate keys for an encrypted repo

# SYNOPSIS

bup genkey

# DESCRIPTION

`bup genkey` generates keys for creating an encrypted repository.

The generated configuration is printed to standard output, and
should be saved to a file, which is then used with `config://` URLs
(see `bup-encrypted`(7)). It contains the `repokey`, `readkey` and
`writekey` settings, as well as a randomly generated `bup.repo.id`
which identifies the repository on this machine and names its local
cache directory.

Keys are generated randomly and there's no way to influence this.

Encrypted repositories require the python `libnacl` module to be
installed; `bup genkey` fails without it.

# SEE ALSO

See `bup-encrypted`(7) for how to otherwise configure encrypted
repositories.

# BUP

Part of the `bup`(1) suite.
