
from binascii import hexlify, unhexlify

from bup import git, vfs
from bup.compat import hexstr, pending_raise
from bup.helpers import add_error, die_if_errors, log, saved_errors
from bup.io import path_msg

def get_commit_items(repo, hash):
    data = repo.get_data(hash, b'commit')
    return git.parse_commit(data)

def append_commit(repo, hash, parent):
    ci = get_commit_items(repo, hash)
    tree = unhexlify(ci.tree)
    author = b'%s <%s>' % (ci.author_name, ci.author_mail)
    committer = b'%s <%s>' % (ci.committer_name, ci.committer_mail)
    c = repo.write_commit(tree, parent,
                          author, ci.author_sec, ci.author_offset,
                          committer, ci.committer_sec, ci.committer_offset,
                          ci.message)
    return c, tree


def filter_branch(repo, tip_commit_hex, exclude):
    # May return None if everything is excluded.
    commits = [unhexlify(x) for x in repo.rev_list(tip_commit_hex)]
    commits.reverse()
    last_c, tree = None, None
    # Rather than assert that we always find an exclusion here, we'll
    # just let the StopIteration signal the error.
    first_exclusion = next(i for i, c in enumerate(commits) if exclude(c))
    if first_exclusion != 0:
        last_c = commits[first_exclusion - 1]
        tree = unhexlify(get_commit_items(repo, hexlify(last_c)).tree)
        commits = commits[first_exclusion:]
    for c in commits:
        if exclude(c):
            continue
        last_c, tree = append_commit(repo, hexlify(c), last_c)
    return last_c

def commit_oid(item):
    if isinstance(item, vfs.Commit):
        return item.coid
    assert isinstance(item, vfs.RevList)
    return item.oid

def rm_saves(repo, saves):
    assert(saves)
    first_branch_item = saves[0][1]
    for save, branch in saves: # Be certain they're all on the same branch
        assert(branch == first_branch_item)
    rm_commits = frozenset([commit_oid(save) for save, branch in saves])
    orig_tip = commit_oid(first_branch_item)
    new_tip = filter_branch(repo, hexlify(orig_tip),
                            lambda x: x in rm_commits)
    assert(orig_tip)
    assert(new_tip != orig_tip)
    return orig_tip, new_tip


def dead_items(repo, paths):
    """Return an optimized set of removals, reporting errors via
    add_error, and if there are any errors, return None, None."""
    dead_branches = {}
    dead_saves = {}
    # Scan for bad requests, and opportunities to optimize
    for path in paths:
        try:
            resolved = vfs.resolve(repo, path, follow=False)
        except vfs.IOError as e:
            add_error(e)
            continue
        else:
            leaf_name, leaf_item = resolved[-1]
            if not leaf_item:
                add_error('error: cannot access %s in %s'
                          % (path_msg(b'/'.join(name for name, item in resolved)),
                             path_msg(path)))
                continue
            if isinstance(leaf_item, vfs.RevList):  # rm /foo
                branchname = leaf_name
                dead_branches[branchname] = leaf_item
                dead_saves.pop(branchname, None)  # rm /foo obviates rm /foo/bar
            elif isinstance(leaf_item, vfs.Commit):  # rm /foo/bar
                if leaf_name == b'latest':
                    add_error("error: cannot delete 'latest' symlink")
                else:
                    branchname, branchitem = resolved[-2]
                    if branchname not in dead_branches:
                        dead = leaf_item, branchitem
                        dead_saves.setdefault(branchname, []).append(dead)
            else:
                add_error("don't know how to remove %s yet" % path_msg(path))
    if saved_errors:
        return None, None
    return dead_branches, dead_saves


def bup_rm(repo, paths, verbosity=None):
    dead_branches, dead_saves = dead_items(repo, paths)
    die_if_errors('not proceeding with any removals\n')

    updated_refs = {}  # ref_name -> (original_ref, tip_commit(bin))

    for branchname, branchitem in dead_branches.items():
        ref = b'refs/heads/' + branchname
        assert(not ref in updated_refs)
        updated_refs[ref] = (branchitem.oid, None)

    if dead_saves:
        try:
            for branch, saves in dead_saves.items():
                assert(saves)
                updated_refs[b'refs/heads/' + branch] = rm_saves(repo, saves)
        except BaseException as ex:
            with pending_raise(ex):
                repo.abort_writing()
        finally:
            repo.close()

    # Only update the refs here, at the very end, so that if something
    # goes wrong above, the old refs will be undisturbed.  Make an attempt
    # to update each ref.
    for ref_name, info in updated_refs.items():
        orig_ref, new_ref = info
        try:
            if not new_ref:
                repo.delete_ref(ref_name, orig_ref)
            else:
                repo.update_ref(ref_name, new_ref, orig_ref)
                if verbosity:
                    log('updated %s (%s%s)\n'
                        % (path_msg(ref_name),
                           hexstr(orig_ref) + ' -> ' if orig_ref else '',
                           hexstr(new_ref)))
        except git.GitError as ex:
            if new_ref:
                add_error('while trying to update %s (%s%s): %s'
                          % (path_msg(ref_name),
                             hexstr(orig_ref) + ' -> ' if orig_ref else '',
                             hexstr(new_ref),
                             ex))
            else:
                add_error('while trying to delete %r (%s): %s'
                          % (ref_name, hexstr(orig_ref), ex))
