from subprocess import PIPE
import os
import shutil
import pytest

from buptest import exc as ex, exo
from bup.compat import environ
import bup.path


bup_cmd = bup.path.exe()


FIXED_MTIME = 1000000000


def make_repo(tmpdir, monkeypatch):
    repo = tmpdir + b'/repo'
    monkeypatch.setitem(environ, b'BUP_DIR', repo)
    ex((bup_cmd, b'init'))
    return repo


def save_tree(tmpdir, name, files, date, modes=None):
    src = tmpdir + b'/src'
    shutil.rmtree(src, ignore_errors=True)
    for relpath, data in files.items():
        path = src + b'/' + relpath
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
    for relpath, mode in (modes or {}).items():
        os.chmod(src + b'/' + relpath, mode)
    for dirpath, dirnames, filenames in os.walk(src):
        for entry in dirnames + filenames:
            os.utime(dirpath + b'/' + entry, (FIXED_MTIME, FIXED_MTIME))
    ex((bup_cmd, b'index', b'--clear'))
    ex((bup_cmd, b'index', b'-u', src))
    ex((bup_cmd, b'save', b'-n', name, b'-d', date, b'--strip', src))


def diff(repo, access, *args, check=True):
    if access == 'remote':
        argv = (bup_cmd, b'diff', b'-r', b'-:' + repo) + args
    else:
        argv = (bup_cmd, b'diff') + args
    return exo(argv, check=check, stderr=PIPE)


BEFORE = {
    b'a': b'old\n',
    b'gone/g': b'g\n',
    b'same': b'same\n',
    b'sub/b': b'b\n',
}

AFTER = {
    b'a': b'new, longer\n',
    b'c': b'added\n',
    b'same': b'same\n',
    b'sub/b': b'b changed\n',
    b'sub/d': b'd\n',
}


def populate(tmpdir, monkeypatch):
    repo = make_repo(tmpdir, monkeypatch)
    save_tree(tmpdir, b'one', BEFORE, b'1000000000')
    save_tree(tmpdir, b'two', AFTER, b'1000000100')
    return repo


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_top_level(tmpdir, monkeypatch, access):
    repo = populate(tmpdir, monkeypatch)
    out = diff(repo, access, b'/one/latest', b'/two/latest').out
    assert out == (b' M a\n'
                   b' A c\n'
                   b' D gone/\n'
                   b' M sub/\n')


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_recursive(tmpdir, monkeypatch, access):
    repo = populate(tmpdir, monkeypatch)
    out = diff(repo, access, b'-R', b'/one/latest', b'/two/latest').out
    assert out == (b' M a\n'
                   b' A c\n'
                   b' D gone/\n'
                   b' D gone/g\n'
                   b' M sub/\n'
                   b' M sub/b\n'
                   b' A sub/d\n')


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_reversed_swaps_added_and_deleted(tmpdir, monkeypatch, access):
    repo = populate(tmpdir, monkeypatch)
    out = diff(repo, access, b'-R', b'/two/latest', b'/one/latest').out
    assert out == (b' M a\n'
                   b' D c\n'
                   b' A gone/\n'
                   b' A gone/g\n'
                   b' M sub/\n'
                   b' M sub/b\n'
                   b' D sub/d\n')


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_subdirectory_paths(tmpdir, monkeypatch, access):
    repo = populate(tmpdir, monkeypatch)
    out = diff(repo, access, b'/one/latest/sub', b'/two/latest/sub').out
    assert out == (b' M b\n'
                   b' A d\n')


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_identical_is_empty(tmpdir, monkeypatch, access):
    repo = populate(tmpdir, monkeypatch)
    assert diff(repo, access, b'/one/latest', b'/one/latest').out == b''
    assert diff(repo, access, b'-R', b'/two/latest',
                b'/two/latest').out == b''


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_rejects_invalid_arguments(tmpdir, monkeypatch, access):
    repo = populate(tmpdir, monkeypatch)

    cp = diff(repo, access, b'/one/latest', check=False)
    assert cp.rc
    assert b'must give two paths' in cp.err

    cp = diff(repo, access, b'/one/latest', b'/two/latest', b'/extra',
              check=False)
    assert cp.rc
    assert b'must give two paths' in cp.err

    cp = diff(repo, access, b'/one/latest', b'/two/nosuch', check=False)
    assert cp.rc
    assert b'not found' in cp.err

    cp = diff(repo, access, b'/one/latest/a', b'/two/latest', check=False)
    assert cp.rc
    assert b'not a directory' in cp.err


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_resaved_tree_is_identical(tmpdir, monkeypatch, access):
    repo = make_repo(tmpdir, monkeypatch)
    save_tree(tmpdir, b'one', BEFORE, b'1000000000')
    save_tree(tmpdir, b'two', BEFORE, b'1000000100')
    assert diff(repo, access, b'-m', b'-R', b'/one/latest',
                b'/two/latest').out == b''


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_metadata_ignored_without_option(tmpdir, monkeypatch, access):
    repo = make_repo(tmpdir, monkeypatch)
    save_tree(tmpdir, b'one', BEFORE, b'1000000000')
    save_tree(tmpdir, b'two', BEFORE, b'1000000100', modes={b'a': 0o400})
    assert diff(repo, access, b'-R', b'/one/latest', b'/two/latest').out == b''


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_file_metadata_only(tmpdir, monkeypatch, access):
    repo = make_repo(tmpdir, monkeypatch)
    save_tree(tmpdir, b'one', BEFORE, b'1000000000')
    save_tree(tmpdir, b'two', BEFORE, b'1000000100', modes={b'a': 0o400})
    out = diff(repo, access, b'-m', b'-R', b'/one/latest',
               b'/two/latest').out
    assert out == b' m a\n'


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_directory_own_metadata(tmpdir, monkeypatch, access):
    repo = make_repo(tmpdir, monkeypatch)
    save_tree(tmpdir, b'one', BEFORE, b'1000000000')
    save_tree(tmpdir, b'two', BEFORE, b'1000000100', modes={b'sub': 0o711})
    # Without -m a directory's own metadata is not compared, so only
    # the metadata option reports it.
    assert diff(repo, access, b'/one/latest', b'/two/latest').out == b''
    assert diff(repo, access, b'-m', b'/one/latest', b'/two/latest').out == (
        b' m sub/\n')
    assert diff(repo, access, b'-m', b'-R', b'/one/latest',
                b'/two/latest').out == (
        b' m sub/\n')


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_metadata_inside_directory(tmpdir, monkeypatch, access):
    repo = make_repo(tmpdir, monkeypatch)
    save_tree(tmpdir, b'one', BEFORE, b'1000000000')
    save_tree(tmpdir, b'two', BEFORE, b'1000000100', modes={b'sub/b': 0o400})
    assert diff(repo, access, b'-m', b'/one/latest', b'/two/latest').out == (
        b' m sub/\n')
    assert diff(repo, access, b'-m', b'-R', b'/one/latest',
                b'/two/latest').out == (
        b' m sub/\n'
        b' m sub/b\n')


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_diff_file_replaced_by_directory(tmpdir, monkeypatch, access):
    repo = make_repo(tmpdir, monkeypatch)
    tree = dict(BEFORE)
    del tree[b'a']
    tree[b'a/x'] = b'x\n'
    save_tree(tmpdir, b'one', BEFORE, b'1000000000')
    save_tree(tmpdir, b'two', tree, b'1000000100')
    assert diff(repo, access, b'/one/latest', b'/two/latest').out == (
        b' D a\n'
        b' A a/\n')
    assert diff(repo, access, b'-R', b'/one/latest', b'/two/latest').out == (
        b' D a\n'
        b' A a/\n'
        b' A a/x\n')
    assert diff(repo, access, b'-R', b'/two/latest', b'/one/latest').out == (
        b' D a/\n'
        b' D a/x\n'
        b' A a\n')
