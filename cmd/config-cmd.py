#!/bin/sh
"""": # -*-python-*-
bup_python="$(dirname "$0")/bup-python" || exit $?
exec "$bup_python" "$0" ${1+"$@"}
"""
# end of bup preamble

from __future__ import absolute_import, print_function
import sys

from bup import hashsplit, git, options, index, client, repo, metadata, hlinkdb
from bup.compat import argv_bytes, environ

optspec = """
bup config [--type=<path,int,str,bool>] <name>
--
r,remote=  proto://hostname/path/to/repo of remote repository
t,type=    what type to interpret the value as
"""
o = options.Options(optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

if len(extra) != 1:
    o.fatal("must give exactly one name")

name = argv_bytes(extra[0])

is_reverse = environ.get(b'BUP_SERVER_REVERSE')
if is_reverse and opt.remote:
    o.fatal("don't use -r in reverse mode; it's automatic")

if opt.remote:
    opt.remote = argv_bytes(opt.remote)

r = repo.from_opts(opt)

if opt.type == 'str':
    opt.type = None
print("%s = %r" % (name.encode('utf-8'), r.config(name, opttype=opt.type)))

r.close()
