from subprocess import PIPE
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


def config(repo, access, *args, check=True):
    if access == 'remote':
        argv = (bup_cmd, b'config', b'-r', b'-:' + repo) + args
    else:
        argv = (bup_cmd, b'config') + args
    return exo(argv, check=check, stderr=PIPE)


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_config_get_set_unset(tmpdir, access):
    repo = make_repo(tmpdir)

    cp = config(repo, access, b'bup.split.trees', check=False)
    assert cp.rc == 1
    assert cp.out == b''

    config(repo, access, b'bup.split.trees', b'true')
    assert config(repo, access, b'bup.split.trees').out \
        == b'bup.split.trees=true\n'
    assert config(repo, access, b'--type=bool', b'bup.split.trees').out \
        == b'bup.split.trees=true\n'

    config(repo, access, b'bup.split.trees', b'false')
    assert config(repo, access, b'--type=bool', b'bup.split.trees').out \
        == b'bup.split.trees=false\n'

    config(repo, access, b'pack.compression', b'3')
    assert config(repo, access, b'--type=int', b'pack.compression').out \
        == b'pack.compression=3\n'

    config(repo, access, b'--unset', b'pack.compression')
    cp = config(repo, access, b'pack.compression', check=False)
    assert cp.rc == 1
    keys = config(repo, access, b'--list-keys').out.split(b'\n')
    assert b'pack.compression' not in keys


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_config_list(tmpdir, access):
    repo = make_repo(tmpdir)
    config(repo, access, b'bup.split.trees', b'true')
    config(repo, access, b'core.compression', b'5')

    keys = config(repo, access, b'--list-keys').out.split(b'\n')
    assert b'bup.split.trees' in keys
    assert b'core.compression' in keys

    pairs = config(repo, access, b'--list-with-value').out.split(b'\n')
    assert b'bup.split.trees=true' in pairs
    assert b'core.compression=5' in pairs

    config(repo, access, b'--unset', b'bup.split.trees')
    keys = config(repo, access, b'--list-keys').out.split(b'\n')
    assert b'bup.split.trees' not in keys


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_config_rejects_invalid_value(tmpdir, access):
    repo = make_repo(tmpdir)

    for name, value in ((b'pack.compression', b'asdf'),
                        (b'bup.split.trees', b'maybe'),
                        (b'bup.split.files', b'bogus')):
        cp = config(repo, access, name, value, check=False)
        assert cp.rc
        assert b'invalid value' in cp.err

    keys = config(repo, access, b'--list-keys').out.split(b'\n')
    assert b'pack.compression' not in keys
    assert b'bup.split.trees' not in keys
    assert b'bup.split.files' not in keys

    config(repo, access, b'bup.split.files', b'legacy:13')
    assert config(repo, access, b'bup.split.files').out \
        == b'bup.split.files=legacy:13\n'


@pytest.mark.parametrize('access', ['local', 'remote'])
def test_config_type_not_allowed_when_writing(tmpdir, access):
    repo = make_repo(tmpdir)
    cp = config(repo, access, b'--type=int', b'pack.compression', b'3',
                check=False)
    assert cp.rc
    assert b'--type must not be used when writing' in cp.err
    cp = config(repo, access, b'--type=bool', b'--unset', b'pack.compression',
                check=False)
    assert cp.rc
    assert b'--type must not be used when writing' in cp.err
    keys = config(repo, access, b'--list-keys').out.split(b'\n')
    assert b'pack.compression' not in keys


def test_config_remote_refuses_disallowed_key(tmpdir):
    repo = make_repo(tmpdir)
    config(repo, 'local', b'user.name', b'someone')

    for args in ((b'user.name', b'other'),
                 (b'user.name',),
                 (b'--unset', b'user.name')):
        cp = config(repo, 'remote', *args, check=False)
        assert cp.rc
        assert b'does not allow remote access to' in cp.err
        assert b'user.name' in cp.err

    assert config(repo, 'local', b'user.name').out == b'user.name=someone\n'


def test_config_save_after_setting_trees(tmpdir):
    repo = make_repo(tmpdir)
    src = tmpdir + b'/src'
    ex((b'mkdir', src))
    with open(src + b'/file', 'wb') as f:
        f.write(b'data\n')

    config(repo, 'local', b'bup.split.trees', b'true')
    ex((bup_cmd, b'index', src))
    ex((bup_cmd, b'save', b'-n', b'main', b'--strip', src))
    listing = exo((bup_cmd, b'ls', b'main/latest/')).out
    assert b'file' in listing.split()
