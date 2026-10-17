
from glob import glob
from os import environb, unlink
from subprocess import run
from sys import stderr

import pytest

from bup import path

bup_exe = path.exe()

def runc(*args, **kwargs):
    assert 'check' not in kwargs
    run(*args, check='True', **kwargs)

def bupc(*args, **kwargs):
    cmd = [bup_exe] + list(args[0])
    print(cmd, file=stderr)
    run(cmd, *args[1:], check=True, **kwargs)

@pytest.mark.skipif(environb.get(b'BUP_TEST_SHA256', b'') == b'1',
                    reason='need sha256 index')
def test_missing_midx(tmpdir):
    bup_dir = tmpdir + b'/bup'
    environb[b'GIT_DIR'] = bup_dir
    environb[b'BUP_DIR'] = bup_dir
    bupc(('init',))
    bupc(('index', 'test/sampledata/var/lib'))
    bupc(('save', '-n', 'save', 'test'))
    bupc(('index', 'test/sampledata/var/doc'))
    bupc(('save', '-n', 'save', 'test'))
    bupc(('midx', '-f'))
    midxs = glob(bup_dir + b'/objects/pack/*.midx')
    assert len(midxs) == 1
    bupc(('midx', '--check', '-a'))
    idxs = glob(bup_dir + b'/objects/pack/*.idx')
    assert len(idxs) > 1
    unlink(idxs[0])
    bupc(('midx', '--check', '-a'))
