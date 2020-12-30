import sys

from bup import git, options
from bup.compat import argv_bytes
from bup.helpers import EXIT_FAILURE, EXIT_SUCCESS, debug1, log, valid_save_name
from bup.io import byte_stream, path_msg
from bup.repo import main_repo_location, repo_for_location
from bup.url import URL


optspec = """
bup branch
bup branch [-f] <branch name> <commit>
bup branch [-f] -d <branch name>
--
d,delete=   Delete a branch
f,force     Overwrite existing branch, ignore a missing branch when deleting
r,remote=   remote repository path
"""

BRANCH_PREFIX = b'refs/heads/'


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
    branch_refs = dict(repo.refs(limit_to_heads=True))
    branches = [name[len(BRANCH_PREFIX):] for name in branch_refs]

    if opt.delete:
        if extra:
            o.fatal('no commit is allowed with --delete')
        branch_name = argv_bytes(opt.delete)
        if not valid_save_name(branch_name):
            o.fatal(f'{path_msg(branch_name)} is not a valid branch name')
        if branch_name not in branches and not opt.force:
            log(f"bup: error: branch '{path_msg(branch_name)}' doesn't exist\n")
            return EXIT_FAILURE
        repo.delete_ref(BRANCH_PREFIX + branch_name)
        return EXIT_SUCCESS

    if not extra:
        out = byte_stream(sys.stdout)
        for name in sorted(branches):
            out.write(name)
            out.write(b'\n')
        out.flush()
        return EXIT_SUCCESS
    if len(extra) != 2:
        o.fatal('expected branch name and commit')

    branch_name, commit = map(argv_bytes, extra)
    if not branch_name:
        o.fatal('branch name must not be empty')
    debug1(f'args: branch name = {path_msg(branch_name)}; '
           f'commit = {commit.decode("ascii", "replace")}\n')

    if not valid_save_name(branch_name):
        o.fatal(f'{path_msg(branch_name)} is not a valid branch name')

    branch = BRANCH_PREFIX + branch_name
    if branch in branch_refs and not opt.force:
        log(f"bup: error: branch '{path_msg(branch_name)}' already exists\n")
        return EXIT_FAILURE

    try:
        commit_oid = repo.rev_parse(commit)
    except git.GitError as e:
        log(f'bup: error: {e}\n')
        return EXIT_FAILURE
    if not commit_oid:
        log(f'bup: error: commit {commit.decode("ascii", "replace")} not found.\n')
        return EXIT_FAILURE

    # Passing the old value makes update_ref() atomic: it fails if
    # the branch changed (or appeared) since we read branch_refs.
    try:
        repo.update_ref(branch, commit_oid, branch_refs.get(branch))
    except git.GitError as e:
        log(f"bup: error: could not create branch '{path_msg(branch_name)}': {e}\n")
        return EXIT_FAILURE
    return EXIT_SUCCESS
