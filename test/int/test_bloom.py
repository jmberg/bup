
import errno, os, sys

import pytest

from bup.bloom import BloomReader, BloomWriter
from bup.compat import dataclass


@pytest.mark.parametrize('oidsize', (20, 32))
@pytest.mark.parametrize('k', (4, 5))
def test_bloom(tmpdir, oidsize, k):
    hashes = [os.urandom(oidsize) for i in range(100)]
    @dataclass(slots=True)
    class Idx:
        name: bytes
        shatable: bytes
        sha256: bool
    ix = Idx(name=b'dummy.idx', shatable=b''.join(hashes), sha256=oidsize==32)
    with BloomWriter(tmpdir + b'/pybuptest.bloom', 'w+b', expected=100, k=k) as b:
        b.add_idx(ix)
        assert b.pfalse_positive() < .1
    with BloomReader(tmpdir + b'/pybuptest.bloom') as b:
        all_present = True
        for h in hashes:
            all_present &= (b.exists(h) or False)
        assert all_present
        false_positives = 0
        for h in [os.urandom(oidsize) for i in range(1000)]:
            if b.exists(h):
                false_positives += 1
        assert false_positives < 10
    os.unlink(tmpdir + b'/pybuptest.bloom')

def test_bloom_default_size(tmpdir):
    os.chdir(tmpdir)
    with BloomWriter(b'bup.bloom', 'w+b', expected=100) as b:
        assert b.path == b'bup.bloom'
        assert b.k == 5


# pylint: disable-next=unused-argument
def test_large_bloom(tmpdir):
    # Test large (~1GiB) filter.  This may fail on s390 (31-bit
    # architecture), and anywhere else where the address space is
    # sufficiently limited.
    try:
        with BloomWriter(tmpdir + b'/bup.bloom', 'w+b', expected=2**28,
                          delaywrite=False) as b:
            assert b.k == 4
    except EnvironmentError as ex:
        if sys.maxsize > 2**32 or ex.errno != errno.ENOMEM:
            raise
        pytest.skip(f'{ex} (maybe mmap failed on 32-bit system)')
