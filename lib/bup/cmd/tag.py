
import sys

from bup import git, options
from bup.compat import argv_bytes
from bup.helpers import EXIT_FAILURE, EXIT_SUCCESS, debug1, log, valid_save_name
from bup.io import byte_stream, path_msg
from bup.repo import main_repo_location, repo_for_location
from bup.url import URL


# FIXME: review for safe writes.

optspec = """
bup tag
bup tag [-f] <tag name> <commit>
bup tag [-f] -d <tag name>
--
d,delete=   Delete a tag
f,force     Overwrite existing tag, or ignore missing tag when deleting
r,remote=   remote repository path
"""

TAG_PREFIX = b'refs/tags/'

def main(argv):
    o = options.Options(optspec)
    opt, flags_, extra = o.parse_bytes(argv[1:])

    loc = main_repo_location(argv_bytes(opt.remote) if opt.remote else None,
                             o.fatal)
    if isinstance(loc, URL) and loc.scheme == b'file':
        git.check_repo_or_die(loc.path)

    with repo_for_location(loc) as repo:
        return _run(o, opt, extra, repo)


def _run(o, opt, extra, repo):
    tag_refs = dict(repo.refs(limit_to_tags=True))
    tags = [name[len(TAG_PREFIX):] for name in tag_refs]

    if opt.delete:
        # git.delete_ref() (and thus repo.delete_ref()) doesn't complain
        # if a ref doesn't exist.  We could implement this verification
        # but we'd need to read in the contents of the tag file and pass
        # the hash, and we already know about the tag's existence via
        # "tags".
        tag_name = argv_bytes(opt.delete)
        if not opt.force and tag_name not in tags:
            log("error: tag '%s' doesn't exist\n" % path_msg(tag_name))
            sys.exit(EXIT_FAILURE)
        repo.delete_ref(TAG_PREFIX + tag_name)
        return EXIT_SUCCESS

    if not extra:
        for t in tags:
            sys.stdout.flush()
            out = byte_stream(sys.stdout)
            out.write(t)
            out.write(b'\n')
        return EXIT_SUCCESS
    if len(extra) != 2:
        o.fatal('expected commit ref and hash')

    tag_name, commit = map(argv_bytes, extra[:2])
    if not tag_name:
        o.fatal("tag name must not be empty.")
    debug1("args: tag name = %s; commit = %s\n"
           % (path_msg(tag_name), commit.decode('ascii')))

    if tag_name in tags and not opt.force:
        log(f'bup: error: tag {path_msg(tag_name)} already exists\n')
        return EXIT_FAILURE

    if not valid_save_name(tag_name):
        o.fatal(f'{path_msg(tag_name)} is not a valid tag name')

    try:
        commit_oid = repo.rev_parse(commit)
    except git.GitError as e:
        log("bup: error: %s" % e)
        return EXIT_FAILURE

    if not commit_oid:
        log("bup: error: commit %s not found.\n" % commit.decode('ascii'))
        return EXIT_FAILURE

    # Use tag_refs.get() to get the old value, if it's None
    # then update_ref() will reject the update if the tag
    # does in fact already exist.
    tag = TAG_PREFIX + tag_name
    repo.update_ref(tag, commit_oid, tag_refs.get(tag))
    return EXIT_SUCCESS
