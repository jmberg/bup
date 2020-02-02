#!/bin/sh
"""": # -*-python-*-
# https://sourceware.org/bugzilla/show_bug.cgi?id=26034
export "BUP_ARGV_0"="$0"
arg_i=1
for arg in "$@"; do
    export "BUP_ARGV_${arg_i}"="$arg"
    shift
    arg_i=$((arg_i + 1))
done
# Here to end of preamble replaced during install
bup_python="$(dirname "$0")/../../config/bin/python" || exit $?
exec "$bup_python" "$0"
"""
# end of bup preamble

from __future__ import absolute_import, print_function
import sys, os

sys.path[:0] = [os.path.dirname(os.path.realpath(__file__)) + '/..']

from bup import hashsplit, git, options, index, client, repo, metadata, hlinkdb
from bup.compat import argv_bytes, environ, argv

optspec = """
bup config [--type=<path,int,str,bool>] <name>
--
r,remote=  proto://hostname/path/to/repo of remote repository
t,type=    what type to interpret the value as
"""
o = options.Options(optspec)
(opt, flags, extra) = o.parse(argv[1:])

git.check_repo_or_die()
if len(extra) != 1:
    o.fatal("must give exactly one name")

name = argv_bytes(extra[0])

is_reverse = environ.get(b'BUP_SERVER_REVERSE')
if is_reverse and opt.remote:
    o.fatal("don't use -r in reverse mode; it's automatic")

if opt.remote:
    opt.remote = argv_bytes(opt.remote)

try:
    if opt.remote:
        repo = repo.make_repo(opt.remote)
    elif is_reverse:
        repo = repo.make_repo(b'reverse://%s' % is_reverse)
    else:
        repo = repo.LocalRepo()
except client.ClientError as e:
    log('error: %s' % e)
    sys.exit(1)

if opt.type == 'str':
    opt.type = None
print("%s = %r" % (name.decode('utf-8'), repo.config(name, opttype=opt.type)))

repo.close()
