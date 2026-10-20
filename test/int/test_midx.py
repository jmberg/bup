
from glob import glob
from os import environb, unlink
from subprocess import CalledProcessError, run
from sys import stderr

import pytest

from bup import git, midx, path

bup_exe = path.exe()

sha256 = environb.get(b'BUP_TEST_SHA256', b'') == b'1'
hashsize = 32 if sha256 else 20

def _midx_expect(version):
    """The header version for a midx of the given base version."""
    return version | (256 if sha256 else 0)

def runc(*args, **kwargs):
    assert 'check' not in kwargs
    run(*args, check='True', **kwargs)

def bupc(*args, **kwargs):
    cmd = [bup_exe] + list(args[0])
    print(cmd, file=stderr)
    run(cmd, *args[1:], check=True, **kwargs)

@pytest.mark.skipif(sha256, reason='need sha256 index')
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


def _midx_version(filename):
    with open(filename, 'rb') as f:
        hdr = f.read(8)
    assert hdr[:4] == b'MIDX'
    return int.from_bytes(hdr[4:8], 'big')


def _check_midx_offsets(midx_name, idx_names):
    """Check that every offset the midx reports matches its idx file."""
    m = midx.open_midx(midx_name)
    try:
        for idx_name in idx_names:
            ix = git.open_idx(idx_name, hashsize)
            try:
                for sha in ix:
                    mloc = m.exists(sha, want_source=True, want_offset=True)
                    assert mloc is not None
                    assert mloc.offset is not None
                    assert mloc.offset == ix.exists(sha, want_offset=True).offset
            finally:
                ix.close()
    finally:
        m.close()


def _check_midx_crc_bytes(midx_name, idx_names):
    """Check that every CRC byte the midx reports is the low byte of
    the idx CRC (0 in the midx means unknown).
    """
    m = midx.open_midx(midx_name)
    try:
        for idx_name in idx_names:
            ix = git.open_idx(idx_name, hashsize)
            try:
                for sha in ix:
                    mloc = m.exists(sha, want_crc=True)
                    assert mloc is not None
                    crc = ix.exists(sha, want_crc=True).crc
                    assert (mloc.crc or 0) == crc & 0xff
            finally:
                ix.close()
    finally:
        m.close()


def _setup_two_packs(bup_dir):
    # Use split rather than index/save, which doesn't yet work with
    # sha256 repositories, so these tests also cover BUP_TEST_SHA256=1.
    environb[b'GIT_DIR'] = bup_dir
    environb[b'BUP_DIR'] = bup_dir
    bupc(('init',))
    bupc(('split', '-n', 'one', 'lib/bup/midx.py'))
    bupc(('split', '-n', 'two', 'lib/bup/git.py'))
    return sorted(glob(bup_dir + b'/objects/pack/*.idx'))


def test_midx_default_is_version4(tmpdir):
    bup_dir = tmpdir + b'/bup'
    _setup_two_packs(bup_dir)
    bupc(('midx', '-f'))
    midxs = glob(bup_dir + b'/objects/pack/*.midx')
    assert len(midxs) == 1
    assert _midx_version(midxs[0]) == _midx_expect(4)


def test_midx_offsets_version5(tmpdir):
    bup_dir = tmpdir + b'/bup'
    idxs = _setup_two_packs(bup_dir)
    bupc(('midx', '-f', '--offsets'))
    midxs = glob(bup_dir + b'/objects/pack/*.midx')
    assert len(midxs) == 1
    assert _midx_version(midxs[0]) == _midx_expect(5)
    bupc(('midx', '--check', '-a'))
    _check_midx_offsets(midxs[0], idxs)


def test_midx_offsets_expands_v4_input(tmpdir):
    bup_dir = tmpdir + b'/bup'
    idxs = _setup_two_packs(bup_dir)
    bupc(('midx', '-f'))
    midxs = glob(bup_dir + b'/objects/pack/*.midx')
    assert len(midxs) == 1
    assert _midx_version(midxs[0]) == _midx_expect(4)
    bupc(('midx', '-f', '--offsets'))
    midxs = glob(bup_dir + b'/objects/pack/*.midx')
    assert len(midxs) == 1
    assert _midx_version(midxs[0]) == _midx_expect(5)
    _check_midx_offsets(midxs[0], idxs)


def test_midx_zero_offsets_fall_back_to_idx(tmpdir):
    bup_dir = tmpdir + b'/bup'
    idxs = _setup_two_packs(bup_dir)
    bupc(('midx', '-f', '--offsets'))
    midxs = glob(bup_dir + b'/objects/pack/*.midx')
    assert len(midxs) == 1
    midx_name = midxs[0]
    m = midx.open_midx(midx_name)
    offsets_ofs = m.offsets_ofs
    count = len(m)
    m.close()
    # 0 means unknown; readers must then get the offset from the idx
    with open(midx_name, 'r+b') as f:
        f.seek(offsets_ofs)
        f.write(bytes(4 * count))
    m = midx.open_midx(midx_name)
    try:
        for idx_name in idxs:
            ix = git.open_idx(idx_name, hashsize)
            try:
                for sha in ix:
                    assert m.exists(sha, want_offset=True).offset is None
            finally:
                ix.close()
    finally:
        m.close()
    pl = git.PackIdxList(bup_dir + b'/objects/pack', hashsize)
    try:
        for idx_name in idxs:
            ix = git.open_idx(idx_name, hashsize)
            try:
                for sha in ix:
                    loc = pl.exists(sha, want_offset=True)
                    assert loc is not None
                    assert loc.offset == ix.exists(sha, want_offset=True).offset
            finally:
                ix.close()
    finally:
        pl.close()


def test_midx_check_detects_bad_offset(tmpdir):
    bup_dir = tmpdir + b'/bup'
    _setup_two_packs(bup_dir)
    bupc(('midx', '-f', '--offsets'))
    midxs = glob(bup_dir + b'/objects/pack/*.midx')
    assert len(midxs) == 1
    m = midx.open_midx(midxs[0])
    offsets_ofs = m.offsets_ofs
    m.close()
    with open(midxs[0], 'r+b') as f:
        f.seek(offsets_ofs)
        value = int.from_bytes(f.read(4), 'big')
        assert value
        f.seek(offsets_ofs)
        f.write((value + 1).to_bytes(4, 'big'))
    with pytest.raises(CalledProcessError):
        bupc(('midx', '--check', '-a'))


def test_midx_mixed_v4_v5_inputs(tmpdir):
    bup_dir = tmpdir + b'/bup'
    idxs = _setup_two_packs(bup_dir)
    assert len(idxs) == 2
    pack_dir = bup_dir + b'/objects/pack'
    v5_name = pack_dir + b'/midx-v5-test.midx'
    v4_name = pack_dir + b'/midx-v4-test.midx'
    out_name = pack_dir + b'/midx-merged-test.midx'
    bupc(('midx', '--offsets', '-o', v5_name, idxs[0]))
    bupc(('midx', '-o', v4_name, idxs[1]))
    assert _midx_version(v5_name) == _midx_expect(5)
    assert _midx_version(v4_name) == _midx_expect(4)
    bupc(('midx', '--offsets', '-o', out_name, v5_name, v4_name))
    assert _midx_version(out_name) == _midx_expect(5)
    _check_midx_offsets(out_name, idxs)
    _check_midx_crc_bytes(out_name, idxs)


def test_midx_crc_bytes_version5(tmpdir):
    bup_dir = tmpdir + b'/bup'
    idxs = _setup_two_packs(bup_dir)
    bupc(('midx', '-f', '--offsets'))
    midxs = glob(bup_dir + b'/objects/pack/*.midx')
    assert len(midxs) == 1
    bupc(('midx', '--check', '-a'))
    _check_midx_crc_bytes(midxs[0], idxs)


def test_midx_zero_crc_bytes_fall_back_to_idx(tmpdir):
    bup_dir = tmpdir + b'/bup'
    idxs = _setup_two_packs(bup_dir)
    bupc(('midx', '-f', '--offsets'))
    midxs = glob(bup_dir + b'/objects/pack/*.midx')
    assert len(midxs) == 1
    midx_name = midxs[0]
    m = midx.open_midx(midx_name)
    crc8_ofs = m.crc8_ofs
    count = len(m)
    m.close()
    # 0 means unknown; readers must then get the full CRC from the idx
    with open(midx_name, 'r+b') as f:
        f.seek(crc8_ofs)
        f.write(bytes(count))
    m = midx.open_midx(midx_name)
    try:
        for idx_name in idxs:
            ix = git.open_idx(idx_name, hashsize)
            try:
                for sha in ix:
                    assert m.exists(sha, want_crc=True).crc is None
            finally:
                ix.close()
    finally:
        m.close()
    pl = git.PackIdxList(bup_dir + b'/objects/pack', hashsize)
    try:
        for idx_name in idxs:
            ix = git.open_idx(idx_name, hashsize)
            try:
                for sha in ix:
                    loc = pl.exists(sha, want_crc=True)
                    assert loc is not None
                    assert loc.crc == ix.exists(sha, want_crc=True).crc
            finally:
                ix.close()
    finally:
        pl.close()


def test_midx_check_detects_bad_crc_byte(tmpdir):
    bup_dir = tmpdir + b'/bup'
    _setup_two_packs(bup_dir)
    bupc(('midx', '-f', '--offsets'))
    midxs = glob(bup_dir + b'/objects/pack/*.midx')
    assert len(midxs) == 1
    m = midx.open_midx(midxs[0])
    crc8_ofs = m.crc8_ofs
    m.close()
    with open(midxs[0], 'r+b') as f:
        f.seek(crc8_ofs)
        value = f.read(1)[0]
        new = 1 if value != 1 else 2
        f.seek(crc8_ofs)
        f.write(bytes([new]))
    with pytest.raises(CalledProcessError):
        bupc(('midx', '--check', '-a'))
