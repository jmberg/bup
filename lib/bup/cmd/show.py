import sys
from binascii import hexlify
from stat import S_ISDIR

from bup import git, options
from bup.commit import parse_commit
from bup.compat import argv_bytes
from bup.helpers import EXIT_FAILURE, EXIT_SUCCESS, handle_ctrl_c, log
from bup.io import byte_stream, path_msg
from bup.repo import main_repo_location, repo_for_location
from bup.url import URL


optspec = """
bup show [-r remote] [-R] [-e] <ref>
--
r,remote=   remote repository path
R,recurse   recurse into tree objects
e,exists    mark tree entries that are missing from the repository with !
"""


def main(argv):
    o = options.Options(optspec)
    opt, flags_, extra = o.parse_bytes(argv[1:])

    if len(extra) != 1:
        o.fatal('must give exactly one <ref>')
    if opt.exists and opt.remote:
        o.fatal('--exists is only supported for local repositories')
    ref = argv_bytes(extra[0])

    handle_ctrl_c()

    loc = main_repo_location(argv_bytes(opt.remote) if opt.remote else None,
                             o.fatal)
    if isinstance(loc, URL) and loc.scheme == b'file':
        git.check_repo_or_die(loc.path)

    with repo_for_location(loc) as repo:
        return _run(opt, repo, ref)


def _run(opt, repo, ref):
    oid = repo.rev_parse(ref)
    if oid is None:
        log(f'bup: error: {path_msg(ref)} is not a valid ref\n')
        return EXIT_FAILURE

    out = byte_stream(sys.stdout)
    rc = _show_object(out, opt, repo, oid, ref)
    out.flush()
    return rc


def _show_object(out, opt, repo, oid, ref):
    oidx, otype, _, data = repo.cat(hexlify(oid))
    if oidx is None:
        log(f'bup: error: {path_msg(ref)}: object is missing\n')
        return EXIT_FAILURE
    if otype == b'commit':
        _show_commit(out, oid, b''.join(data))
    elif otype == b'blob':
        out.write(b''.join(data))
    elif otype == b'tree':
        return _show_tree(out, opt, repo, b''.join(data))
    else:
        log(f'bup: error: {path_msg(ref)}: unsupported object type {path_msg(otype)}\n')
        return EXIT_FAILURE
    return EXIT_SUCCESS


def _format_tz(offset):
    sign = b'-' if offset < 0 else b'+'
    minutes = abs(offset) // 60
    return b'%s%02d%02d' % (sign, minutes // 60, minutes % 60)


def _show_commit(out, oid, content):
    # similar to "git show --format=raw"
    c = parse_commit(content)
    out.write(b'commit %s\n' % hexlify(oid))
    out.write(b'tree %s\n' % c.tree)
    for p in c.parents:
        out.write(b'parent %s\n' % p)
    out.write(b'author %s <%s> %d %s\n' % (c.author_name, c.author_mail,
                                           c.author_sec,
                                           _format_tz(c.author_offset)))
    out.write(b'committer %s <%s> %d %s\n' % (c.committer_name,
                                              c.committer_mail,
                                              c.committer_sec,
                                              _format_tz(c.committer_offset)))
    out.write(b'\n')
    for line in c.message.split(b'\n'):
        out.write(b'    %s\n' % line)


def _show_tree(out, opt, repo, content, indent=b''):
    # similar to "git ls-tree"
    rc = EXIT_SUCCESS
    for mode, name, oid in git.tree_iter(content, repo.hashsize):
        if opt.exists:
            exists = b'  ' if repo.exists(oid) else b'! '
        else:
            exists = b''
        kind = b'tree' if S_ISDIR(mode) else b'blob'
        out.write(b'%s%06o %s %s %s  %s\n' % (exists, mode, kind,
                                              hexlify(oid), indent, name))
        if opt.recurse and S_ISDIR(mode):
            oidx, _, _, data = repo.cat(hexlify(oid))
            if oidx is None:
                if not opt.exists:
                    log(f'bup: error: {path_msg(name)}: object is missing\n')
                    rc = EXIT_FAILURE
                continue
            sub_rc = _show_tree(out, opt, repo, b''.join(data),
                                indent + b'  |')
            if sub_rc != EXIT_SUCCESS:
                rc = sub_rc
    return rc
