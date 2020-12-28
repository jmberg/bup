
from __future__ import absolute_import
import sys

from bup import git, options
from bup.compat import argv_bytes
from bup.helpers import debug1, log
from bup.io import byte_stream, path_msg
from bup.repo import from_opts


# FIXME: review for safe writes.

optspec = """
bup tag
bup tag [-f] <tag name> <commit>
bup tag [-f] -d <tag name>
--
r,remote=   hostname:/path/to/repo of remote repository
d,delete=   Delete a tag
f,force     Overwrite existing tag, or ignore missing tag when deleting
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    with from_opts(opt) as repo:
        refs = { r[0]: r[1] for r in repo.refs() if r[0].startswith(b'refs/tags/') }

        if opt.delete:
            # git.delete_ref() doesn't complain if a ref doesn't exist.  We
            # could implement this verification but we'd need to read in the
            # contents of the tag file and pass the hash, and we already know
            # about the tag's existance via "tags".
            tag_name = argv_bytes(opt.delete)
            refname = b'refs/tags/' + tag_name
            if not opt.force and refname not in refs:
                log("error: tag '%s' doesn't exist\n" % path_msg(tag_name))
                sys.exit(1)
            repo.delete_ref(b'refs/tags/%s' % tag_name)
            sys.exit(0)

        if not extra:
            for t in refs:
                sys.stdout.flush()
                out = byte_stream(sys.stdout)
                out.write(t[len(b'refs/tags/'):])
                out.write(b'\n')
            sys.exit(0)
        elif len(extra) != 2:
            o.fatal('expected commit ref and hash')

        tag_name, commit = map(argv_bytes, extra[:2])
        if not tag_name:
            o.fatal("tag name must not be empty.")
        debug1("args: tag name = %s; commit = %s\n"
               % (path_msg(tag_name), commit.decode('ascii')))
        refname = b'refs/tags/' + tag_name

        if refname in refs and not opt.force:
            log("bup: error: tag '%s' already exists\n" % path_msg(tag_name))
            sys.exit(1)

        if tag_name.startswith(b'.'):
            o.fatal("'%s' is not a valid tag name." % path_msg(tag_name))

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
            log("bup: error: could not create tag '%s': %s" % (path_msg(tag_name), e))
            sys.exit(3)
