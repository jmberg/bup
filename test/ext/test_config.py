
from os import chdir, mkdir, symlink, unlink
from subprocess import PIPE
from time import localtime, strftime, tzset
import re

from bup.compat import environ
from bup.helpers import unlink as unlink_if_exists
from buptest import ex, exo
from wvpytest import wvfail, wvpass, wvpasseq, wvpassne, wvstart
import pytest
import bup.path

bup_cmd = bup.path.exe()

def _bup(*args, **kwargs):
    if 'stdout' not in kwargs:
        return exo((bup_cmd,) + args, **kwargs)
    return ex((bup_cmd,) + args, **kwargs)

@pytest.mark.parametrize("access", ['remote', 'local'])
def test_config(tmpdir, access):
    environ[b'BUP_DIR'] = tmpdir + b'/repo'

    if access == 'remote':
        bup = lambda *args, **kw: _bup(b'on', b'-', *args, **kw)
    else:
        bup = lambda *args, **kw: _bup(*args, **kw)

    chdir(tmpdir)
    mkdir(b'repo')
    _bup(b'init')
    assert bup(b'config', b'bup.repo-id').out.startswith(b'bup.repo-id=')

    added = set([b'bup.repo-id'])
    for k, vs in ((b'bup.split-trees', (b'true', b'false')),
                  (b'bup.blobbits', (b'13', b'16')),
                  (b'pack.compression', (b'1', b'2', b'3')),
                  (b'core.compression', (b'1', b'2', b'9')),
                  (b'pack.packsizelimit', (b'1g', b'1000000'))):
        added.add(k)
        for v in vs:
            bup(b'config', k, v)
            expected = k + b'=' + v + b'\n'
            assert bup(b'config', k).out == expected

    found = bup(b'config', b'--list-keys').out.split(b'\n')
    print(found)
    for k in added:
        assert k in found

    added.remove(b'bup.repo-id')
    for k in added:
        bup(b'config', b'--unset', k)
    found = bup(b'config', b'--list-keys').out.split(b'\n')
    for k in added:
        assert k not in found
