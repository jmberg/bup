
import sys
from bup import options, repo
from bup.compat import argv_bytes
from bup.io import byte_stream

optspec = """
bup config [--type=<path,int,str,bool>] [--list] <name> [<value>]
--
r,remote=       proto://hostname/path/to/repo of remote repository
t,type=         what type to interpret the value as
list-keys       list all keys instead of reading/setting
list-with-value list keys and values
unset           remove the option <name>
"""

def main(argv):
    o = options.Options(optspec)
    (opt, flags, extra) = o.parse_bytes(argv[1:])

    if len(extra) not in (0, 1, 2):
        o.fatal("must give exactly one name and optional value")

    out = byte_stream(sys.stdout)

    with repo.from_opts(opt) as r:
        if opt.list_keys or opt.list_with_value:
            if opt.unset:
                o.fatal("--unset cannot be used with --list-keys/--list-with-value")
            if opt.list_keys and opt.list_with_value:
                o.fatal("--list-keys and --list-with-value are mutually exclusive")
            if len(extra):
                o.fatal("name/value cannot be given with --list-keys/--list-with-value")
            if opt.list_keys:
                for k in r.config_list():
                    out.write(k)
                    out.write(b'\n')
            if opt.list_with_value:
                for k, v in r.config_list(True):
                    out.write(k)
                    out.write(b'=')
                    out.write(v)
                    out.write(b'\n')
        elif len(extra) == 2:
            name = argv_bytes(extra[0])
            if opt.type is not None:
                o.fatal("--type must not be used when writing")
            r.config_write(name, argv_bytes(extra[1]))
        elif len(extra) != 1:
            o.fatal("must give a name")
        elif opt.unset:
            name = argv_bytes(extra[0])
            r.config_write(name, None)
        else:
            name = argv_bytes(extra[0])
            if opt.type == 'str':
                opt.type = None
            v = r.config_get(name, opttype=opt.type)
            if v is None:
                sys.exit(1)
            out.write(name)
            out.write(b'=')
            out.write(v)
            out.write(b'\n')
