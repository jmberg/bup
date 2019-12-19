#!/bin/sh
"""": # -*-python-*-
# https://sourceware.org/bugzilla/show_bug.cgi?id=26034
export "BUP_ARGV_0"="$0"
arg_i=1
for arg in "$@"; do
    export "BUP_ARGV_${arg_i}"="$arg"
    shift
    : $((arg_i+=1))
done
# Here to end of preamble replaced during install
bup_python="$(dirname "$0")/../../config/bin/python" || exit $?
exec "$bup_python" "$0"
"""
# end of bup preamble

from __future__ import absolute_import
import sys

try:
    import libnacl.public
    import libnacl.secret
except ImportError:
    print("libnacl not found, cannot generate keys")
    sys.exit(2)

pair = libnacl.public.SecretKey()
box = libnacl.secret.SecretBox()

print('[bup]')
print('  type = Encrypted')
print('  storage = TBD')
print('  cachedir = TBD')
print('  repokey = ' + box.hex_sk().decode('ascii'))
print('  readkey = ' + pair.hex_sk().decode('ascii'))
print('  writekey = ' + pair.hex_pk().decode('ascii'))
