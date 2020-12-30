
from __future__ import absolute_import
import sys

from bup import git, options
from bup.compat import argv_bytes
from bup.helpers import debug1, log
from bup.io import byte_stream, path_msg
from bup.repo import from_opts


# FIXME: review for safe writes.

optspec = """
bup %(cmd)s
bup %(cmd)s [-f] <%(cmd)s name> <commit>
bup %(cmd)s [-f] -d <%(cmd)s name>
--
r,remote=   hostname:/path/to/repo of remote repository
d,delete=   Delete a %(cmd)s
f,force     Overwrite existing %(cmd)s, or ignore missing %(cmd)s when deleting
"""

def main(argv, branch=False):
    if branch:
        refpfx = b'refs/heads/'
        desc = 'branch'
    else:
        refpfx = b'refs/tags/'
        desc = 'tag'

    o = options.Options(optspec % { 'cmd': desc })
    opt, flags, extra = o.parse_bytes(argv[1:])

    with from_opts(opt) as repo:
        refs = { r[0]: r[1] for r in repo.refs() if r[0].startswith(refpfx) }

        if opt.delete:
            # git.delete_ref() doesn't complain if a ref doesn't exist.  We
            # could implement this verification but we'd need to read in the
            # contents of the ref file and pass the hash, and we already know
            # about the ref's existance via "refs".
            ref_name = argv_bytes(opt.delete)
            refname = refpfx + ref_name
            if not opt.force and refname not in refs:
                log("error: %s '%s' doesn't exist\n" % (desc, path_msg(ref_name)))
                sys.exit(1)
            repo.delete_ref(refpfx + ref_name)
            sys.exit(0)

        if not extra:
            for t in refs:
                sys.stdout.flush()
                out = byte_stream(sys.stdout)
                out.write(t[len(refpfx):])
                out.write(b'\n')
            sys.exit(0)
        elif len(extra) != 2:
            o.fatal('expected commit ref and hash')

        ref_name, commit = map(argv_bytes, extra[:2])
        if not ref_name:
            o.fatal("%s name must not be empty." % desc)
        debug1("args: %s name = %s; commit = %s\n"
               % (desc, path_msg(ref_name), commit.decode('ascii')))
        refname = refpfx + ref_name

        if refname in refs and not opt.force:
            log("bup: error: %s '%s' already exists\n" % (desc, path_msg(ref_name)))
            sys.exit(1)

        if ref_name.startswith(b'.'):
            o.fatal("'%s' is not a valid %s name." % (path_msg(ref_name), desc))

        try:
            hash = repo.rev_parse(commit)
        except git.GitError as e:
            log("bup: error: %s" % e)
            sys.exit(2)

        if not repo.exists(hash):
            log("bup: error: commit %s not found.\n" % commit.decode('ascii'))
            sys.exit(2)

        try:
            oldref = refs.get(refname, None)
            repo.update_ref(refname, hash, oldref)
        except git.GitError as e:
            log("bup: error: could not create %s '%s': %s" % (desc, path_msg(ref_name), e))
            sys.exit(3)
