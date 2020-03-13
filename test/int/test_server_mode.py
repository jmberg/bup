from contextlib import contextmanager
from subprocess import CalledProcessError, DEVNULL, PIPE, run
import os

from buptest import exc as ex
from pytest import raises

from bup import client, git
from bup.compat import environ
from bup.url import URL
import bup.path


bup_exe = bup.path.exe()
refname = b'refs/heads/main'


@contextmanager
def server_mode(tmpdir, mode):
    wrapper = tmpdir + b'/bup-server-' + mode
    with open(wrapper, 'wb') as f:
        f.write(b'#!/bin/sh\nexec %s "$@" --mode=%s\n' % (bup_exe, mode))
    os.chmod(wrapper, 0o755)
    saved = environ.get(b'BUP_TEST_SSH_BUP_PATH')
    environ[b'BUP_TEST_SSH_BUP_PATH'] = wrapper
    try:
        yield
    finally:
        if saved is None:
            del environ[b'BUP_TEST_SSH_BUP_PATH']
        else:
            environ[b'BUP_TEST_SSH_BUP_PATH'] = saved


def remote_client(path):
    return client.Client(URL(scheme=b'ssh', path=path))


def setup_repos(tmpdir):
    local = tmpdir + b'/local'
    remote = tmpdir + b'/remote'
    src = tmpdir + b'/src'
    environ[b'BUP_DIR'] = environ[b'GIT_DIR'] = local
    git.init_repo(local)
    git.init_repo(remote)
    os.mkdir(src)
    return local, remote, src


def save_to_remote(src, remote, content, name=b'main'):
    with open(src + b'/data', 'wb') as f:
        f.write(content)
    ex((bup_exe, b'index', src))
    ex((bup_exe, b'save', b'-r', b'-:' + remote, b'-n', name, b'--strip', src))
    return git.read_ref(b'refs/heads/' + name, repo_dir=remote)


def test_server_mode_append(tmpdir):
    _, remote, src = setup_repos(tmpdir)
    with server_mode(tmpdir, b'append'):
        first = save_to_remote(src, remote, b'one\n')
        second = save_to_remote(src, remote, b'two\n')
        assert first != second
        assert git.read_ref(refname, repo_dir=remote) == second

        # force push fails
        with raises(client.ClientError):
            with remote_client(remote) as c:
                c.update_ref(refname, first, second)
        # 'delete-ref' not allowed
        with raises(client.ClientError):
            with remote_client(remote) as c:
                c.delete_ref(refname, second)
        # can create new refs
        with remote_client(remote) as c:
            c.update_ref(b'refs/heads/other', first, None)
        # but trying to create it again is force push
        with raises(client.ClientError):
            with remote_client(remote) as c:
                c.update_ref(b'refs/heads/other', second, None)
        assert git.read_ref(b'refs/heads/other', repo_dir=remote) == first
        assert git.read_ref(refname, repo_dir=remote) == second


def test_server_mode_read(tmpdir):
    _, remote, src = setup_repos(tmpdir)
    with server_mode(tmpdir, b'unrestricted'):
        oid = save_to_remote(src, remote, b'one\n')
    with server_mode(tmpdir, b'read'):
        with remote_client(remote) as c:
            assert c.read_ref(refname) == oid
            assert list(c.refs()) == [(refname, oid)]
        with raises(client.ClientError):
            with remote_client(remote) as c:
                c.update_ref(b'refs/heads/other', oid, None)
        with raises(client.ClientError):
            with remote_client(remote) as c:
                c.delete_ref(refname, oid)
        with raises(CalledProcessError):
            save_to_remote(src, remote, b'two\n')
    assert git.read_ref(refname, repo_dir=remote) == oid


def test_server_mode_read_append(tmpdir):
    _, remote, src = setup_repos(tmpdir)
    with server_mode(tmpdir, b'read-append'):
        first = save_to_remote(src, remote, b'one\n')
        second = save_to_remote(src, remote, b'two\n')
        with remote_client(remote) as c:
            assert c.read_ref(refname) == second
        with raises(client.ClientError):
            with remote_client(remote) as c:
                c.delete_ref(refname, second)
        with raises(client.ClientError):
            with remote_client(remote) as c:
                c.update_ref(refname, first, second)
    assert git.read_ref(refname, repo_dir=remote) == second


def test_server_mode_unrestricted(tmpdir):
    _, remote, src = setup_repos(tmpdir)
    with server_mode(tmpdir, b'unrestricted'):
        first = save_to_remote(src, remote, b'one\n')
        second = save_to_remote(src, remote, b'two\n')
        with remote_client(remote) as c:
            c.update_ref(refname, first, second)
            assert c.read_ref(refname) == first
            c.delete_ref(refname, first)
            assert c.read_ref(refname) is None


def test_server_mode_invalid():
    cp = run((bup_exe, b'server', b'--mode=bogus'),
             stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
    assert cp.returncode != 0
    assert b'mode' in cp.stderr
