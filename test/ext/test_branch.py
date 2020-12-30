from subprocess import PIPE
import os
import pytest

from buptest import exc as ex, exo
from bup.compat import environ
import bup.path


bup_cmd = bup.path.exe()


def make_repo(tmpdir):
    repo = tmpdir + b'/repo'
    environ[b'BUP_DIR'] = repo
    ex((bup_cmd, b'init'))
    return repo


def save(tmpdir, name, data, date):
    src = tmpdir + b'/src'
    os.makedirs(src, exist_ok=True)
    with open(src + b'/file', 'wb') as f:
        f.write(data)
    ex((bup_cmd, b'index', src))
    ex((bup_cmd, b'save', b'-n', name, b'-d', date, b'--strip', src))


def branch(repo, access, *args, check=True):
    if access == 'remote':
        argv = (bup_cmd, b'branch', b'-r', b'-:' + repo) + args
    else:
        argv = (bup_cmd, b'branch') + args
    return exo(argv, check=check, stderr=PIPE)


def branches(repo, access):
    return branch(repo, access).out


def listing(path):
    return exo((bup_cmd, b'ls', path)).out


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_branch_create_list_delete(tmpdir, access):
    repo = make_repo(tmpdir)
    save(tmpdir, b'src', b'one\n', b'1000000000')
    assert branches(repo, access) == b'src\n'

    branch(repo, access, b'copy', b'src')
    assert branches(repo, access) == b'copy\nsrc\n'
    assert listing(b'/copy') == listing(b'/src')

    branch(repo, access, b'-d', b'copy')
    assert branches(repo, access) == b'src\n'


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_branch_refuses_existing_unless_forced(tmpdir, access):
    repo = make_repo(tmpdir)
    save(tmpdir, b'src', b'one\n', b'1000000000')
    branch(repo, access, b'alt', b'src')
    save(tmpdir, b'src', b'two, longer\n', b'1000000100')
    before = listing(b'/alt')
    assert before != listing(b'/src')

    cp = branch(repo, access, b'alt', b'src', check=False)
    assert cp.rc
    assert b'already exists' in cp.err
    assert listing(b'/alt') == before

    branch(repo, access, b'-f', b'alt', b'src')
    assert listing(b'/alt') == listing(b'/src')


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_branch_delete_missing(tmpdir, access):
    repo = make_repo(tmpdir)
    save(tmpdir, b'src', b'one\n', b'1000000000')

    cp = branch(repo, access, b'-d', b'nosuch', check=False)
    assert cp.rc
    assert b"doesn't exist" in cp.err

    branch(repo, access, b'-f', b'-d', b'nosuch')
    assert branches(repo, access) == b'src\n'


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_branch_rejects_invalid_arguments(tmpdir, access):
    repo = make_repo(tmpdir)
    save(tmpdir, b'src', b'one\n', b'1000000000')

    for args in ((b'only-a-name',),
                 (b'a', b'src', b'extra'),
                 (b'-d', b'src', b'extra')):
        cp = branch(repo, access, *args, check=False)
        assert cp.rc
    assert branches(repo, access) == b'src\n'

    for name in (b'.hidden', b'bad..name', b'trailing/', b'../escape',
                 b'bad name'):
        cp = branch(repo, access, name, b'src', check=False)
        assert cp.rc
        assert b'not a valid branch name' in cp.err
        cp = branch(repo, access, b'-d', name, check=False)
        assert cp.rc
        assert b'not a valid branch name' in cp.err
    assert branches(repo, access) == b'src\n'


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_branch_unknown_commit(tmpdir, access):
    repo = make_repo(tmpdir)
    save(tmpdir, b'src', b'one\n', b'1000000000')

    cp = branch(repo, access, b'new', b'nosuch', check=False)
    assert cp.rc
    assert b'not found' in cp.err
    assert branches(repo, access) == b'src\n'
