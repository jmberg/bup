% bup-config(1) Bup %BUP_VERSION%
% Johannes Berg <johannes@sipsolutions.net>
% %BUP_DATE%

# NAME

bup-config - show or set a bup repository configuration option

# SYNOPSIS

bup config [-r host:path] [-t str|int|bool] \<name>

bup config [-r host:path] \<name> \<value>

bup config [-r host:path] \--unset \<name>

bup config [-r host:path] \--list-keys

bup config [-r host:path] \--list-with-value

# DESCRIPTION

`bup config` shows or sets the value of the configuration option
\<name> in a bup repository.  Options are stored in the repository
(for regular repos in the git config file), see `bup-config`(5)
for the list of options bup reads.

When called with only a name, the current value is shown as
`name=value`, and the command exits with status 1 if the option is not
set.  When called with a name and a value, the option is set to that
value.

# OPTIONS

-r, \--remote=[*user*@]*host*:[*path*], \--remote=URL
:   save the data to the specified remote repository, by default via
    SSH.  See bup(1) REMOTE OPTIONS for further information.

-t, \--type=*type*
:   Interpret the value read for \<name> as *str* (the default, no
    interpretation), *int*, or *bool*.  Cannot be used when setting a
    value.  Note that the interpretation is done by `git config`, so
    certain suffixes (like k, m, g) are accepted for int values.

\--list-keys
:   List the names of all options that are set, instead of reading or
    setting one option.

\--list-with-value
:   List the names and values of all options that are set.

\--unset
:   Remove the option \<name> from the repository configuration.

# EXAMPLES

    bup config bup.split.trees
    bup config --type=bool bup.split.trees
    bup config bup.split.trees true
    bup config -r host:/srv/bup/repo --list-with-value
    bup config --unset core.compression

# SEE ALSO

`bup-config`(5)

# BUP

Part of the `bup`(1) suite.
