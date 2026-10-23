
from glob import glob
from os import chmod, environb, unlink
from subprocess import run
from sys import stderr

from bup import path

bup_exe = path.exe()

def runc(*args, **kwargs):
    assert 'check' not in kwargs
    run(*args, check='True', **kwargs)

def bupc(*args, **kwargs):
    cmd = [bup_exe] + list(args[0])
    print(cmd, file=stderr)
    run(cmd, *args[1:], check=True, **kwargs)

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


def _setup_two_packs(bup_dir):
    environb[b'GIT_DIR'] = bup_dir
    environb[b'BUP_DIR'] = bup_dir
    bupc(('init',))
    bupc(('split', '-t', 'lib/bup/midx.py'))
    bupc(('split', '-t', 'lib/bup/git.py'))
    return sorted(glob(bup_dir + b'/objects/pack/*.idx'))


def _swap_first_two_shas(idx_name):
    """Break the hash order of an idx by swapping its first two entries."""
    sha_table_ofs = 8 + 256 * 4  # header plus fanout table
    chmod(idx_name, 0o644)
    with open(idx_name, 'r+b') as f:
        f.seek(sha_table_ofs)
        first = f.read(20)
        second = f.read(20)
        f.seek(sha_table_ofs)
        f.write(second + first)


def test_midx_rejects_unsorted_idx(tmpdir):
    bup_dir = tmpdir + b'/bup'
    idxs = _setup_two_packs(bup_dir)
    _swap_first_two_shas(idxs[0])
    result = run([bup_exe, 'midx', '-f'], capture_output=True)
    assert result.returncode != 0
    assert b'is not sorted by hash' in result.stderr
    assert not glob(bup_dir + b'/objects/pack/*.midx')
