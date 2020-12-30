from subprocess import PIPE
import os
import re
import pytest

from buptest import exc as ex, exo
from bup.compat import environ
import bup.path


bup_cmd = bup.path.exe()


def make_repo(tmpdir, monkeypatch):
    repo = tmpdir + b'/repo'
    monkeypatch.setitem(environ, b'BUP_DIR', repo)
    ex((bup_cmd, b'init'))
    return repo


def populate(tmpdir, name=b'src', date=b'1000000000'):
    src = tmpdir + b'/' + name
    os.makedirs(src + b'/sub', exist_ok=True)
    with open(src + b'/a.txt', 'wb') as f:
        f.write(b'hello\n')
    with open(src + b'/sub/b.txt', 'wb') as f:
        f.write(b'inner\n')
    ex((bup_cmd, b'index', src))
    ex((bup_cmd, b'save', b'-n', name, b'-d', date, b'--strip', src))


def show(repo, access, *args, check=True):
    if access == 'remote':
        argv = (bup_cmd, b'show', b'-r', b'-:' + repo) + args
    else:
        argv = (bup_cmd, b'show') + args
    return exo(argv, check=check, stderr=PIPE)


def tree_of_commit(out):
    m = re.search(rb'^tree ([0-9a-f]{40})$', out, re.M)
    assert m, out
    return m.group(1)


def entry_oid(listing, name):
    for line in listing.splitlines():
        fields = line.split()
        if fields and fields[-1] == name:
            return fields[2]
    raise AssertionError(f'{name!r} not found in {listing!r}')


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_show_commit(tmpdir, monkeypatch, access):
    repo = make_repo(tmpdir, monkeypatch)
    populate(tmpdir)
    out = show(repo, access, b'src').out
    assert out.startswith(b'commit ')
    assert tree_of_commit(out)
    assert re.search(rb'^author .* 1000000000 [-+]\d{4}$', out, re.M), out
    assert b'\n    bup save\n' in out


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_show_commit_by_oid(tmpdir, monkeypatch, access):
    repo = make_repo(tmpdir, monkeypatch)
    populate(tmpdir)
    out = show(repo, 'local', b'src').out
    m = re.search(rb'^commit ([0-9a-f]{40})$', out, re.M)
    assert m, out
    assert show(repo, access, m.group(1)).out == out


def test_show_commit_remote_matches_local(tmpdir, monkeypatch):
    repo = make_repo(tmpdir, monkeypatch)
    populate(tmpdir)
    assert show(repo, 'remote', b'src').out == show(repo, 'local', b'src').out


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_show_tree_lists_entries(tmpdir, monkeypatch, access):
    repo = make_repo(tmpdir, monkeypatch)
    populate(tmpdir)
    tree = tree_of_commit(show(repo, 'local', b'src').out)
    listing = show(repo, access, tree).out.decode()
    names = [line.split()[-1] for line in listing.splitlines()]
    assert 'a.txt' in names
    assert 'sub' in names
    assert 'b.txt' not in names


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_show_tree_recursive(tmpdir, monkeypatch, access):
    repo = make_repo(tmpdir, monkeypatch)
    populate(tmpdir)
    tree = tree_of_commit(show(repo, 'local', b'src').out)
    listing = show(repo, access, b'-R', tree).out.decode()
    names = [line.split()[-1] for line in listing.splitlines()]
    assert 'b.txt' in names
    assert any(line.endswith('|  b.txt') for line in listing.splitlines())


def test_show_blob(tmpdir, monkeypatch):
    repo = make_repo(tmpdir, monkeypatch)
    populate(tmpdir)
    tree = tree_of_commit(show(repo, 'local', b'src').out)
    blob = entry_oid(show(repo, 'local', tree).out.decode(), 'a.txt')
    assert show(repo, 'local', blob.encode()).out == b'hello\n'


@pytest.mark.parametrize('ref', [b'nosuchref', b'0' * 40])
def test_show_invalid_ref(tmpdir, monkeypatch, ref):
    repo = make_repo(tmpdir, monkeypatch)
    populate(tmpdir)
    cp = show(repo, 'local', ref, check=False)
    assert cp.rc
    assert b'not a valid ref' in cp.err


@pytest.mark.parametrize('args, message', [
    ((), b'must give exactly one <ref>'),
    ((b'src', b'src'), b'must give exactly one <ref>'),
    ((b'-e', b'-r', b'-:/nonexistent', b'src'),
     b'--exists is only supported for local repositories'),
])
def test_show_invalid_arguments(tmpdir, monkeypatch, args, message):
    make_repo(tmpdir, monkeypatch)
    populate(tmpdir)
    cp = exo((bup_cmd, b'show') + args, check=False, stderr=PIPE)
    assert cp.rc
    assert cp.out == b''
    assert message in cp.err


def test_show_exists_marks_missing(tmpdir, monkeypatch):
    repo = make_repo(tmpdir, monkeypatch)
    populate(tmpdir)
    tree = tree_of_commit(show(repo, 'local', b'src').out)
    blob = entry_oid(show(repo, 'local', tree).out.decode(), 'a.txt')
    tree = exo((b'git', b'--git-dir', repo, b'mktree', b'--missing'),
               input=b'040000 tree ' + b'1' * 40 + b'\tgone\n'
               b'100644 blob ' + blob.encode() + b'\ta.txt\n').out.strip()

    listing = show(repo, 'local', b'-e', tree).out.decode().splitlines()
    assert any(line.startswith('! ') and line.endswith('gone')
               for line in listing), listing
    assert any(line.startswith('  ') and line.endswith('a.txt')
               for line in listing), listing


def test_show_recursive_missing_fails(tmpdir, monkeypatch):
    repo = make_repo(tmpdir, monkeypatch)
    populate(tmpdir)
    blob = entry_oid(show(repo, 'local', tree_of_commit(
        show(repo, 'local', b'src').out)).out.decode(), 'a.txt')
    tree = exo((b'git', b'--git-dir', repo, b'mktree', b'--missing'),
               input=b'040000 tree ' + b'1' * 40 + b'\tgone\n'
               b'100644 blob ' + blob.encode() + b'\ta.txt\n').out.strip()

    cp = show(repo, 'local', b'-R', tree, check=False)
    assert cp.rc
    assert b'gone: object is missing' in cp.err
    assert b'a.txt' in cp.out
