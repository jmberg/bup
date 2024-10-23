
from __future__ import absolute_import, print_function

from bup import options, repo
from bup.compat import argv_bytes

optspec = """
bup config [--type=<path,int,str,bool>] <name> [<value>]
--
r,remote=  proto://hostname/path/to/repo of remote repository
t,type=    what type to interpret the value as
"""

def main(argv):
    o = options.Options(optspec)
    (opt, flags, extra) = o.parse_bytes(argv[1:])

    if len(extra) not in (1, 2):
        o.fatal("must give exactly one name and optional value")

    name = argv_bytes(extra[0])

    r = repo.from_opts(opt)

    if opt.type == 'str':
        opt.type = None
    if len(extra) == 2:
        r.config_write(name, argv_bytes(extra[1]))
    else:
        print("%s = %r" % (name.decode('utf-8'), r.config_get(name, opttype=opt.type)))

    r.close()
