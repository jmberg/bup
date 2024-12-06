
import sys

from bup import git, options
from bup.compat import argv_bytes
from bup.io import byte_stream
from bup.repo import from_opts
from bup.helpers import handle_ctrl_c
from binascii import hexlify
from stat import S_ISDIR

optspec = """
bup show [options] <ref>
--
r,remote=   hostname:/path/to/repo of remote repository
R,recurse   recurse into tree objects
e,exists    check if printed objects exist (in trees, prefix line with ! if not)
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    handle_ctrl_c()

    with from_opts(opt) as r:
        check_existence = opt.exists
        recurse = opt.recurse

        if not extra or len(extra) != 1:
            o.fatal("Missing or wrong <ref>")

        ref_name = argv_bytes(extra[0])

        sys.stdout.flush()
        out = byte_stream(sys.stdout)

        hash = r.rev_parse(ref_name)
        assert hash is not None, "%s is not a valid ref" % extra[0]
        cat = r.cat(hexlify(hash))
        oid, otype, osize = next(cat)
        if otype == b'commit':
            # similar to "git show --format=raw"
            c = git.parse_commit(b''.join(cat))
            out.write(b'commit %s\n' % oid)
            out.write(b'tree %s\n' % c.tree)
            for p in c.parents:
                out.write(b'parent %s\n' % p)
            out.write(b'author %s <%s> %d +%ds\n' % (c.author_name, c.author_mail,
                                                     c.author_sec, c.author_offset))
            out.write(b'committer %s <%s> %d +%ds\n' % (c.committer_name, c.committer_mail,
                                                        c.committer_sec, c.committer_offset))
            out.write(b'\n')
            for line in c.message.split(b'\n'):
                out.write(b'    %s\n' % line)
        elif otype == b'blob':
            out.write(b''.join(cat))
        elif otype == b'tree':
            # similar to "git ls-tree"
            def print_tree_contents(contents, indent=b''):
                shalist = git.tree_decode(contents)
                for mode, name, hash in shalist:
                    if check_existence:
                        exists = b'  ' if r.exists(hash) else b'! '
                    else:
                        exists = b''
                    out.write(b'%s%06o %s %s %s  %s\n' % (
                               exists, mode, b'tree' if S_ISDIR(mode) else b'blob',
                               hexlify(hash), indent, name))
                    if recurse and S_ISDIR(mode):
                        data = r.get_data(hexlify(hash), b'tree')
                        print_tree_contents(data, indent + b'  |')
            print_tree_contents(b''.join(cat))
