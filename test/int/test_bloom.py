
import os
import errno, platform, tempfile
import logging

from bup import bloom

def test_bloom(tmpdir):
    hashes = [os.urandom(20) for i in range(100)]
    class Idx:
        pass
    ix = Idx()
    ix.name = b'dummy.idx'
    ix.shatable = b''.join(hashes)
    for k in (4, 5):
        with bloom.create(tmpdir + b'/pybuptest.bloom', expected=100, k=k) as b:
            b.add_idx(ix)
            assert b.pfalse_positive() < .1
        with bloom.ShaBloom(tmpdir + b'/pybuptest.bloom') as b:
            all_present = True
            for h in hashes:
                all_present &= (b.exists(h) or False)
            assert all_present
            false_positives = 0
            for h in [os.urandom(20) for i in range(1000)]:
                if b.exists(h):
                    false_positives += 1
            assert false_positives < 10
        os.unlink(tmpdir + b'/pybuptest.bloom')

    tf = tempfile.TemporaryFile(dir=tmpdir)
    with bloom.create(b'bup.bloom', f=tf, expected=100) as b:
        assert b.file == tf
        assert b.k == 5

    # Test large (~1GiB) filter.  This may fail on s390 (31-bit
    # architecture), and anywhere else where the address space is
    # sufficiently limited.
    tf = tempfile.TemporaryFile(dir=tmpdir)
    skip_test = False
    try:
        with bloom.create(b'bup.bloom', f=tf, expected=2**28,
                          delaywrite=False) as b:
            assert b.k == 4
    except EnvironmentError as ex:
        (ptr_width, linkage) = platform.architecture()
        if ptr_width == '32bit' and ex.errno == errno.ENOMEM:
            logging.getLogger().info('skipping large bloom filter test (mmap probably failed) '
                  + str(ex))
        else:
            raise
