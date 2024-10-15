
import re, sys

from bup import options
from bup.compat import argv_bytes
from bup.helpers import EXIT_FAILURE, log
from bup.io import byte_stream

# REVIEW: default rs to \0 or \n?
# REVIEW: select-output (or is more specific command better?)

optspec = """
bup select-fields [--ifs SEP] [--irs SEP] [--ofs SEP] [--ors SEP] <N>...
--
ifs=  change the within-record input field separator
irs=  change the inter-record input separator
ofs=  change the within-record output field separator
ors=  change the inter-record output separator
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    def default_fs(x): return b' ' if x is None else argv_bytes(x)
    def default_rs(x): return b'\n' if x is None else argv_bytes(x)
    opt.ifs = default_fs(opt.ifs)
    opt.ofs = default_fs(opt.ofs)
    opt.irs = default_rs(opt.irs)
    opt.ors = default_rs(opt.ors)

    if opt.ifs == opt.irs:
        o.fatal('--irs and --ofs are the same')

    # We can relax some of this later if we like...
    # FIXME: finish escapes
    def parse_sep(name, sep):
        sep = {b'\\0' : b'\0',
               b'\\t' : b'\t',
               b'\\n' : b'\n'}.get(sep, sep)
        if not len(sep): o.fatal(f'{name} can not be empty')
        if not len(sep) == 1: o.fatal(f'{name} must be one char')
        return sep
    opt.ifs = parse_sep('--ifs', opt.ifs)
    opt.ofs = parse_sep('--ofs', opt.ofs)
    opt.irs = parse_sep('--irs', opt.irs)
    opt.ors = parse_sep('--ors', opt.ors)

    int_rx = re.compile(r'[0-9]+')
    selected_fields = []
    for field in extra:
        if not int_rx.fullmatch(field):
            o.fatal(f'field {field} is not just integer digits')
        selected_fields.append(int(field))
    if not selected_fields:
        o.fatal('no fields selected')

    sys.stdout.flush()
    out = byte_stream(sys.stdout)

    # For now, all in RAM
    for record in byte_stream(sys.stdin).read().split(opt.irs):
        if not record: # Also for now
            continue
        fields = record.split(opt.ifs)
        first = True
        for selected in selected_fields:
            if selected > len(fields):
                log(f'error: not enough fields in {record}\n')
                sys.exit(EXIT_FAILURE)
            if first:
                first = False
            else:
                out.write(opt.ofs)
            out.write(fields[selected - 1])
        out.write(opt.ors)
        out.flush()
