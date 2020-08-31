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
import os, sys, time

sys.path[:0] = [os.path.dirname(os.path.realpath(__file__)) + '/..']

from bup import compat, options
from bup.helpers import ProgressBar, log


optspec = """
bup tick
"""
o = options.Options(optspec)
opt, flags, extra = o.parse(compat.argv[1:])

if extra:
    o.fatal("no arguments expected")

t = time.time()
tleft = 1 - (t - int(t))
time.sleep(tleft)

with ProgressBar(1000) as p:
    for i in range(1001):
        time.sleep(0.01)
        if i % 100 == 10:
            log("i is now %d\n" % i)
        p.update(i, "Receiving %d/%d." % (i, 1000))
