
from binascii import hexlify, unhexlify
import sys

from bup import git, options
from bup.compat import argv_bytes
from bup.helpers import add_error, handle_ctrl_c, qprogress
from bup.io import byte_stream

optspec = """
bup list-idx [--sha256] [--find=<prefix>] <idxfilenames...>
--
sha256  the idx files are sha256 format
find=   display only objects that start with <prefix>
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags_, extra = o.parse_bytes(argv[1:])

    handle_ctrl_c()

    opt.find = argv_bytes(opt.find) if opt.find else b''

    if not extra:
        o.fatal('you must provide at least one filename')

    if len(opt.find) > 64:
        o.fatal('--find parameter must be <= 64 chars long')
    else:
        if len(opt.find) % 2:
            s = opt.find + b'0'
        else:
            s = opt.find
        try:
            bin = unhexlify(s)
        except TypeError:
            o.fatal('--find parameter is not a valid hex string')

    sys.stdout.flush()
    out = byte_stream(sys.stdout)
    find = opt.find.lower()
    count = 0
    idxfiles = [argv_bytes(x) for x in extra]
    hashsize = 32 if opt.sha256 else 20
    for name in idxfiles:
        ix = None
        try:
            ix = git.open_object_idx(name, hashsize)
        except git.GitError as e:
            add_error('%r: %s' % (name, e))
        if not ix:
            continue
        with ix:
            if len(opt.find) == 2 * ix.hashsize:
                if ix.exists(bin):
                    out.write(b'%s %s\n' % (name, find))
            else:
                # slow, exhaustive search
                for _i in ix:
                    i = hexlify(_i)
                    if i.startswith(find):
                        out.write(b'%s %s\n' % (name, i))
                    qprogress('Searching: %d\r' % count)
                    count += 1
