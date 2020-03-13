% bup-server(1) Bup %BUP_VERSION%
% Avery Pennarun <apenwarr@gmail.com>
% %BUP_DATE%

# NAME

bup-server - the server side of the bup client-server relationship

# SYNOPSIS

bup server [--mode=MODE]

# DESCRIPTION

`bup server` is the server side of a remote bup session. 
If you use `bup-split`(1) or `bup-save`(1) with the `-r`
option, they will ssh to the remote server and run `bup
server` to receive the transmitted objects.

There is normally no reason to run `bup server` yourself.

The server's resource usage can be limited by setting
`bup.server.deduplicate-writes` to `false`.  See the description in
`bup-config(5)` for additional information.

# OPTIONS

--mode=MODE
:   Restrict the commands the server will accept from the client.
    MODE may be one of:

    `unrestricted`
    :   Allow all commands.  This is the default.

    `read`
    :   Allow only commands that read references and objects, such
        as reading refs and listing or fetching objects.

    `append`
    :   Allow reading refs, receiving new objects, and updating
        refs.  An existing ref may only be moved to a commit that
        has its current value as a parent, so history cannot be
        rewritten.  New refs can be created, but none deleted.

    `read-append`
    :   Allow both `read` and `append` per above.

# FILES

$BUP_DIR/bup-dumb-server
:   When this file exists, `bup` will act as if
    `bup.server.deduplicate-writes` is set to `false` in the
    configuration, unless the configuration already specifies a value.

# SEE ALSO

`bup-save`(1), `bup-split`(1), `bup-config`(1)

# BUP

Part of the `bup`(1) suite.
