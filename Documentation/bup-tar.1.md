% bup-tar(1) Bup %BUP_VERSION%
% %BUP_DATE%

# NAME

bup-tar - export a backup set or path as a tar archive

# SYNOPSIS

bup tar [-r \<remote\>] \<vfs-path\>

# DESCRIPTION

`bup tar` writes a tar archive of the item at `vfs-path` to standard
output.  The output contains relative paths, starting at `./` for the
VFS path specified, even if that is a whole save.  A single file can
be specified and is just saved under its own base name.

The archive is in PAX format and includes:

- file type, permissions, owner and group (numeric ids and names),
- access, modification and change times with nanosecond precision,
- symbolic links, hard links, device nodes, and FIFOs,
- extended attributes (stored as `SCHILY.xattr.*` records).  Non UTF-8
  names/values are written as `BINARY` header charset.

Sockets and extended attributes with names that aren't UTF-8 cannot
be exported and are skipped with a warning.

ACLs and Linux file attributes are not supported and are not emitted.

# OPTIONS

-r, \--remote=\<remote\>
:   remote repository path

# EXAMPLES

    $ bup tar /laptop/latest > laptop.tar
    $ bup tar /laptop/latest/home/user/src | tar -t

# SEE ALSO

`bup-import-tar`(1)

# BUP

Part of the `bup`(1) suite.
