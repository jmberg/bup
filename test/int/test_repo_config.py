
from contextlib import contextmanager
import os

from pytest import raises

from bup import client, git
from bup.compat import environ
from bup.config import ConfigError
from bup.repo.local import LocalRepo
from bup.url import URL
import bup.path


bup_exe = bup.path.exe()


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


def make_repo(tmpdir, name):
    path = tmpdir + b'/' + name
    git.init_repo(path)
    return path


def without_repo_id(items):
    return [i for i in items
            if (i[0] if isinstance(i, tuple) else i) != b'bup.repo.id']


def test_local_config_write_and_list(tmpdir):
    repo_dir = make_repo(tmpdir, b'repo')
    with LocalRepo(repo_dir=repo_dir) as repo:
        repo.config_write(b'core.compression', b'5')
        assert repo.config_get(b'core.compression', opttype='int') == 5
        assert b'core.compression' in list(repo.config_list())
        assert (b'core.compression', b'5') in list(repo.config_list(values=True))

        # unset
        repo.config_write(b'core.compression', None)
        assert repo.config_get(b'core.compression') is None
        assert b'core.compression' not in list(repo.config_list())


def test_local_config_write_validates_values(tmpdir):
    repo_dir = make_repo(tmpdir, b'repo')
    with LocalRepo(repo_dir=repo_dir) as repo:
        with raises(ConfigError):
            repo.config_write(b'pack.compression', b'fast')
        with raises(ConfigError):
            repo.config_write(b'pack.packsizelimit', b'lots')
        with raises(ConfigError):
            repo.config_write(b'core.compression', b'9x')
        with raises(ConfigError):
            repo.config_write(b'bup.split.trees', b'maybe')
        with raises(ConfigError):
            repo.config_write(b'bup.split.files', b'legacy:12')
        with raises(ConfigError):
            repo.config_write(b'bup.split.files', b'bogus')
        # nothing invalid was written
        assert repo.config_get(b'pack.compression') is None
        assert repo.config_get(b'bup.split.files') is None

        repo.config_write(b'bup.split.trees', b'true')
        assert repo.config_get(b'bup.split.trees', opttype='bool') is True
        repo.config_write(b'bup.split.files', b'legacy:15')
        assert repo.config_get(b'bup.split.files') == b'legacy:15'


def test_local_config_write_unrestricted(tmpdir):
    repo_dir = make_repo(tmpdir, b'repo')
    with LocalRepo(repo_dir=repo_dir) as repo:
        repo.config_write(b'user.name', b'someone')
        assert (b'user.name', b'someone') in list(repo.config_list(values=True))


def test_remote_config_write_and_list(tmpdir):
    remote = make_repo(tmpdir, b'remote')
    with server_mode(tmpdir, b'unrestricted'):
        with remote_client(remote) as c:
            c.config_write(b'core.compression', b'4')
            assert c.config_get(b'core.compression', opttype='int') == 4
            assert without_repo_id(c.config_list()) == [b'core.compression']
            assert without_repo_id(c.config_list(values=True)) == [(b'core.compression', b'4')]

            c.config_write(b'core.compression', None)
            assert c.config_get(b'core.compression') is None
            assert without_repo_id(c.config_list()) == []


def test_remote_config_write_denied_and_invalid(tmpdir):
    remote = make_repo(tmpdir, b'remote')
    with server_mode(tmpdir, b'unrestricted'):
        with remote_client(remote) as c:
            # not in valid_config_opts
            with raises(PermissionError):
                c.config_write(b'user.name', b'someone')
            # bad values
            with raises(ConfigError):
                c.config_write(b'pack.compression', b'fast')
            with raises(ConfigError):
                c.config_write(b'bup.split.files', b'legacy:12')
            # but the connection still works
            c.config_write(b'pack.compression', b'3')
            assert c.config_get(b'pack.compression', opttype='int') == 3


def test_remote_config_cannot_change_repo_id(tmpdir):
    remote = make_repo(tmpdir, b'remote')
    with LocalRepo(repo_dir=remote) as repo:
        repo_id = repo.config_get(b'bup.repo.id')
        assert repo_id is not None
        with server_mode(tmpdir, b'unrestricted'):
            with remote_client(remote) as c:
                with raises(PermissionError):
                    c.config_write(b'bup.repo.id', b'someone-else')
        assert repo.config_get(b'bup.repo.id') == repo_id


def test_remote_config_list_filters_keys(tmpdir):
    remote = make_repo(tmpdir, b'remote')
    with LocalRepo(repo_dir=remote) as repo:
        repo.config_write(b'user.name', b'someone')
        repo.config_write(b'pack.compression', b'2')
    with server_mode(tmpdir, b'unrestricted'):
        with remote_client(remote) as c:
            assert without_repo_id(c.config_list()) == [b'pack.compression']
            assert without_repo_id(c.config_list(values=True)) == [(b'pack.compression', b'2')]


def test_remote_config_read_mode(tmpdir):
    remote = make_repo(tmpdir, b'remote')
    with LocalRepo(repo_dir=remote) as repo:
        repo.config_write(b'pack.compression', b'2')
    with server_mode(tmpdir, b'read'):
        with remote_client(remote) as c:
            assert without_repo_id(c.config_list()) == [b'pack.compression']
            # config-write is not allowed in read mode
            with raises(client.ClientError):
                c.config_write(b'pack.compression', b'3')
