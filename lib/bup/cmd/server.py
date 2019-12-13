
from __future__ import absolute_import
import sys
from bup import compat, options
from bup.io import byte_stream
from bup.server import BupServer
from bup.helpers import (Conn, debug2)

optspec = """
bup server
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    if extra:
        o.fatal('no arguments expected')

    debug2('bup server: reading from stdin.\n')

    BupServer(Conn(byte_stream(sys.stdin), byte_stream(sys.stdout))).handle()
