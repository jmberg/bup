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

from bup import compat, options
from bup.io import byte_stream
from bup.server import BupServer
from bup.helpers import (Conn, debug2)


optspec = """
bup server
"""
o = options.Options(optspec)
(opt, flags, extra) = o.parse(compat.argv[1:])

if extra:
    o.fatal('no arguments expected')

debug2('bup server: reading from stdin.\n')
BupServer(Conn(byte_stream(sys.stdin), byte_stream(sys.stdout))).handle()
