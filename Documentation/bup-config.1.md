% bup-config(1) Bup %BUP_VERSION%
% Johannes Berg <johannes@sipsolutions.net>
% %BUP_DATE%

# NAME

bup-config - show the value of a bup config option

# SYNOPSIS

bup config [-r *host*:*path*] [\--type=\<string,bool,int,path>] \<name>

# DESCRIPTION

`bup config` shows the setting of the item \<name>, this may be useful
e.g. for checking that the parameters are properly transported over to
a remote repository (`bup on ... config ...`) or just to check that the
setting of a given parameter is valid for the given type
(`bup config --type=bool ...`).

It may also be used to check that all bup versions involved with a given
remote connection understand the config command and can communicate about
configuration settings.

# OPTIONS

-r, \--remote=*host*:*path*
:   Query the configuration from the given remote repository.  If
    *path* is omitted, uses the default path on the remote
    server (you still need to include the ':').  The connection to the
    remote server is made with SSH.  If you'd like to specify which port,
    user or private key to use for the SSH connection, we recommend you
    use the `~/.ssh/config` file.
    Note that if the remote server's bup version is older, all values
    will read as None (i.e. not set) regardless of their actual value.

-t, \--type=*type*
:   Interpret the given configuration option using the type, valid types
    are *string* (the default, no real interpretation), *bool* to interpret
    the value as a boolean option, *int* to interpret as an integer and
    *path* to interpret as a path (which just results in ~ expansion).
    Note that this is passed down to `git config` which is used internally,
    so certain suffixes (like k, m, g) will be interpreted for int values.

# SEE ALSO

`bup-save`(1) which uses the configuration option `bup.split.trees`,
`bup-on`(1), `ssh_config`(5)

# BUP

Part of the `bup`(1) suite.
