% bup-server(1) Bup %BUP_VERSION%
% Avery Pennarun <apenwarr@gmail.com>
% %BUP_DATE%

# NAME

bup-server - the server side of the bup client-server relationship

# SYNOPSIS

bup server

# DESCRIPTION

`bup server` is the server side of a remote bup session. 
If you use `bup-split`(1) or `bup-save`(1) with the `-r`
option, they will ssh to the remote server and run `bup
server` to receive the transmitted objects.

There is normally no reason to run `bup server` yourself.

# OPTIONS

\--force-repo
:   Force using the bup repository given in the environment or the
    global *-d*/*\--bup-dir* option. This can be useful for ssh forced
    commands (*command="..."* in an authorized_keys file) as it forces the
    connection to use a given bup repository; it cannot read from
    or write to any other location on the filesystem.

\--mode=*mode*
:   Set the server mode, the following modes are accepted:

    *unrestricted*: No restrictions, this is the default if this option
    is not given.

    *append*: Data can only be written to this repository, and refs can
    be updated. (Obviously, object existence can be proven since indexes
    are needed to save data to a repository.)

    *read-append*: Data can be written to and read back.

    *read*: Data can only be read.

    **NOTE**: Currently, the server doesn't support any destructive
    operations, so *unrestricted* is really identical to *read-append*,
    but as this may change in the future there's a difference already
    to avoid breaking setups.

# MODES

smart
:   In this mode, the server checks each incoming object
    against the idx files in its repository.  If any object
    already exists, it tells the client about the idx file
    it was found in, allowing the client to download that
    idx and avoid sending duplicate data.  This is
    `bup-server`'s default mode.

dumb
:   In this mode, the server will not check its local index
    before writing an object.  To avoid writing duplicate
    objects, the server will tell the client to download all
    of its `.idx` files at the start of the session.  This
    mode is useful on low powered server hardware (ie
    router/slow NAS).

# FILES

$BUP_DIR/bup-dumb-server
:   Activate dumb server mode, as discussed above.  This file is not created by
    default in new repositories. Alternatively, set bup.dumb-server in the
    config, see `bup-config`(1) and `bup-settings`(7).

# SEE ALSO

`bup-save`(1), `bup-split`(1), `bup-config`(1), `bup-settings`(7)

# BUP

Part of the `bup`(1) suite.
