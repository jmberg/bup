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

from __future__ import absolute_import
import os, sys

sys.path[:0] = [os.path.dirname(os.path.realpath(__file__)) + '/..']

from bup import compat, git, options
from bup.compat import argv_bytes, argv
from bup.helpers import debug1, handle_ctrl_c, log
from bup.io import byte_stream, path_msg
from bup.repo import from_opts

# FIXME: review for safe writes.

handle_ctrl_c()

if argv[0].find('tag') >= 0:
    refpfx = b'refs/tags/'
    desc = 'tag'
else:
    refpfx = b'refs/heads/'
    desc = 'branch'

optspec = """
bup %(cmd)s
bup %(cmd)s [-f] <%(cmd)s name> <commit>
bup %(cmd)s [-f] -d <%(cmd)s name>
--
r,remote=   hostname:/path/to/repo of remote repository
d,delete=   Delete a %(cmd)s
f,force     Overwrite existing %(cmd)s, or ignore missing %(cmd)s when deleting
""" % { 'cmd': desc }

o = options.Options(optspec)
opt, flags, extra = o.parse(compat.argv[1:])

r = from_opts(opt)

refs = { r[0]: r[1] for r in r.refs() if r[0].startswith(refpfx) }

if opt.delete:
    # git.delete_ref() doesn't complain if a ref doesn't exist.  We
    # could implement this verification but we'd need to read in the
    # contents of the ref file and pass the hash, and we already know
    # about the ref's existence via "refs".
    ref_name = argv_bytes(opt.delete)
    ref_full = refpfx + ref_name
    if not opt.force and ref_full not in refs:
        log("error: %s '%s' doesn't exist\n" % (desc, path_msg(ref_name)))
        sys.exit(1)
    r.delete_ref(refpfx + ref_name)
    sys.exit(0)

if not extra:
    for t in refs:
        sys.stdout.flush()
        out = byte_stream(sys.stdout)
        out.write(t[len(refpfx):])
        out.write(b'\n')
    sys.exit(0)
elif len(extra) != 2:
    o.fatal('expected commit ref and hash')

ref_name, commit = map(argv_bytes, extra[:2])
if not ref_name:
    o.fatal("%s name must not be empty." % desc)
debug1("args: %s name = %s; commit = %s\n"
       % (desc, path_msg(ref_name), commit.decode('ascii')))
ref_full = refpfx + ref_name

if ref_full in refs and not opt.force:
    log("bup: error: %s '%s' already exists\n" % (desc, path_msg(ref_name)))
    sys.exit(1)

if ref_name.startswith(b'.'):
    o.fatal("'%s' is not a valid %s name." % (desc, path_msg(ref_name)))

try:
    hash = r.rev_parse(commit)
except git.GitError as e:
    log("bup: error: %s" % e)
    sys.exit(2)

if not r.exists(hash):
    log("bup: error: commit %s not found.\n" % commit.decode('ascii'))
    sys.exit(2)

try:
    oldval = refs.get(ref_full, None)
    r.update_ref(ref_full, hash, oldval)
except git.GitError as e:
    log("bup: error: could not create %s '%s': %s" % (desc, path_msg(ref_name), e))
    sys.exit(3)
