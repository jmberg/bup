

import sys

from bup import options
from bup.compat import argv_bytes
from bup.helpers import linereader, log
from bup.io import byte_stream
from bup.repo import from_opts


optspec = """
bup join [-r host:path] [refs or hashes...]
--
r,remote=  remote repository path
o=         output filename
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    stdin = byte_stream(sys.stdin)

    if not extra:
        extra = linereader(stdin)

    ret = 0
    with from_opts(opt) as repo:

        if opt.o:
            outfile = open(opt.o, 'wb')
        else:
            sys.stdout.flush()
            outfile = byte_stream(sys.stdout)

        for ref in [argv_bytes(x) for x in extra]:
            try:
                for blob in repo.join(ref):
                    outfile.write(blob)
            except KeyError as e:
                outfile.flush()
                log('error: %s\n' % e)
                ret = 1

    sys.exit(ret)
