
from io import BytesIO
from binascii import unhexlify
import itertools
import os
import random

from wvpytest import *

from bup import hashsplit, _helpers
from bup._helpers import HashSplitter, RecordHashSplitter
from bup.hashsplit import BUP_BLOBBITS

# These test objects generate a number of least significant bits set
# to one according to their key.  Note that these were generated with
# a *fixed* algorithm (there's a bug in the level decision), so we
# need to adjust for that, but on the plus side we can use them to
# test different bits settings
split_test_objs = {
    13: unhexlify('ded8f1fcf2f45dfadf3458'),
    14: unhexlify('f287ffeeffe1f0e1fa77b1de1837'),
    15: unhexlify('878eb7a2e5baf7fcc8fcc58060ccdad5849b6e89'),
    16: unhexlify('e0e7fdb3e7a579'),
    17: unhexlify('e2ecf2fdb49f01'),
    18: unhexlify('caeadffeb1e68b'),
    19: unhexlify('f1fca9d9bfc9c0'),
    20: unhexlify('d6e8dbb8d9fec7'),
    21: unhexlify('7987fcbda4f27759'),
    22: unhexlify('f4dc926c62617af4'),
    23: unhexlify('edaef4e7f49fb6'),
    24: unhexlify('f1f6fef642ae74'),
    25: unhexlify('2b6470a19fe3d7ad8dd99e95b87b2c71afe47e09b698278771c095a2dfebe8'),
    26: unhexlify('f0aa9fa696bc0707'),
    27: unhexlify('94b053be6c02e1b93c7503e0e87d893495b3da9bc4f8e049140859e8270a8f6d'),
    29: unhexlify('e8985d2c9e26eb2b5a963cd27dc5dbd1bad8b681f43707f40feba5bfc5ef76984f74e1b800b6425be63993'),
}

def test_samples():
    for k, obj in split_test_objs.items():

        # Verify that the k least significant bits are 1 and that
        # the next most significant bit is zero (i.e. that the
        # rollsum of the data matched what we expect for this k).
        rsum = _helpers.rollsum(obj)
        ones = (1 << k) - 1
        mask = (ones << 1) | 1
        WVPASSEQ(rsum & mask, ones)

        # Now check that for the test object a HashSplitter returns
        # the right level and split blob (which by construction should
        # be the input blob).
        #
        # The level should be the count of one bits more significant
        # than the "bits" value...
        exp_level = k - BUP_BLOBBITS
        # ...after ignoring the bit immediately "above" the 13'th bit
        # (quirk in the original algorithm -- see ./DESIGN).
        if exp_level > 0:
            exp_level -= 1
        hs = HashSplitter([BytesIO(obj)], bits=BUP_BLOBBITS, fanbits=1)
        blob, level = next(hs)
        WVPASSEQ(blob, obj)
        WVPASSEQ(level, exp_level)

def test_rolling_sums():
    WVPASS(_helpers.selftest())

def test_fanout_behaviour():
    for hashbits in 13, 14, 15:
        def sb(pfx, level):
            """Return tuple of split content and expected level (from table)."""
            needed = hashbits + hashsplit.fanbits(fanout) * level
            # internal algorithm ignores one bit after the split bits,
            # adjust for that (if n > 0):
            if level:
                needed += 1
            return b'\x00' * pfx + split_test_objs[needed], level
        def end(n):
            return b'\x00' * n, 0
        # check a given sequence is handled correctly
        def check(chunks_and_levels):
            data = b''.join([x[0] for x in chunks_and_levels])
            split = list(HashSplitter([BytesIO(data)], bits=hashbits,
                                      fanbits=hashsplit.fanbits(fanout)))
            WVPASSEQ(chunks_and_levels, split)

        # cf. max_blob in _hashsplit.c
        max_blob = b'\x00' * (1 << hashbits + 2), 0
        for fanout in 2, 4:
            # never split - just max blobs
            check([max_blob] * 4)
            check([sb(0, 0)])
            check([max_blob, sb(1, 3), max_blob])
            check([sb(13, 1)])
            check([sb(13, 1), end(200)])
        fanout = 2
        check([sb(0, 1), sb(30, 2), sb(20, 0), sb(10, 5)])
        check([sb(0, 1), sb(30, 2), sb(20, 0), sb(10, 5), end(10)])

def test_hashsplit_files(tmpdir):
    # See HashSplitter_init for source of sizes
    max_blob_size = 1 << (BUP_BLOBBITS + 2)
    null_path = os.path.join(tmpdir, b'nulls')
    blobs_in_file = 10
    max_blob = bytearray(max_blob_size)
    with open(null_path, 'wb') as f:
        for _ in range(blobs_in_file):
            f.write(max_blob)
    max_blob = None

    with open(null_path, 'rb') as z0, \
         open(null_path, 'rb') as z1, \
         open(null_path, 'rb') as z2:
        res = [(len(b), lvl) for b, lvl in HashSplitter([z0, z1, z2],
                                                        bits=BUP_BLOBBITS)]
    WVPASSEQ(res, [(max_blob_size, 0)] * (blobs_in_file * 3))

    def split_bytes(n):
        return BytesIO(split_test_objs[n])
    def ex(n):
        fanout_bits = hashsplit.fanbits()
        # Add 1 because bup has always ignored one bit between the
        # blob bits and the fanout bits.
        return [(len(split_test_objs[n]),
                 (n - (BUP_BLOBBITS + 1)) // fanout_bits)]

    with open(null_path, 'rb') as z0, \
         open(null_path, 'rb') as z1:
        res = [(len(b), lvl) for b, lvl in HashSplitter([z0, split_bytes(14), z1],
                                                        bits=BUP_BLOBBITS)]
    WVPASSEQ(res, (blobs_in_file * [(max_blob_size, 0)]
                   + ex(14)
                   + blobs_in_file * [(max_blob_size, 0)]))

    res = [(len(b), lvl)
           for b, lvl in HashSplitter([split_bytes(14), split_bytes(15)],
                                      bits=BUP_BLOBBITS)]
    WVPASSEQ(res, ex(14) + ex(15))

    res = [(len(b), lvl)
           for b, lvl in HashSplitter([split_bytes(14), split_bytes(27)],
                                      bits=BUP_BLOBBITS)]
    WVPASSEQ(res, ex(14) + ex(27))
    os.remove(null_path)

def test_hashsplit_boundaries():
    # See HashSplitter_init for source of sizes
    blob_size = 1 << BUP_BLOBBITS
    max_blob_size = 1 << (BUP_BLOBBITS + 2)

    # Check keep_boundaries when the data has no split points
    def bio(s):
        return BytesIO(s)
    nulls = bytearray(blob_size)
    hs = HashSplitter([bio(nulls), bio(nulls), bio(nulls), bio(nulls)],
                      bits=BUP_BLOBBITS, keep_boundaries=False)
    res = [(len(b), lvl) for b, lvl in hs]
    WVPASSEQ(res, [(max_blob_size, 0)])

    hs = HashSplitter([bio(nulls), bio(nulls), bio(nulls), bio(nulls)],
                      bits=BUP_BLOBBITS)
    res = [(len(b), lvl) for b, lvl in hs]
    WVPASSEQ(res, 4 * [(blob_size, 0)])
    nulls = None

    # Check keep_boundaries when the data has internal split points
    def split_bytes(n):
        return BytesIO(split_test_objs[n])
    def ex(n):
        p = n
        # Subtract 1 because bup has always ignored one bit between
        # the blob bits and the fanout bits.
        if p > BUP_BLOBBITS: p -= 1
        fanout_bits = hashsplit.fanbits()
        return (len(split_test_objs[n]), (p - BUP_BLOBBITS) // fanout_bits)
    exp = [ex(13), ex(14), ex(15)]
    hs = HashSplitter([split_bytes(13), split_bytes(14), split_bytes(15)],
                      bits=BUP_BLOBBITS)
    res = [(len(b), lvl) for b, lvl in hs]
    WVPASSEQ(res, exp)

    hs = HashSplitter([split_bytes(13), split_bytes(14), split_bytes(15)],
                      bits=BUP_BLOBBITS, keep_boundaries=False)
    res = [(len(b), lvl) for b, lvl in hs]
    WVPASSEQ(res, exp)

    # Check keep_boundaries when the data has no internal split points
    data = split_test_objs[27]
    d1, d2 = data[:len(data) // 2], data[len(data) // 2:]

    hs = HashSplitter([bio(d1), bio(d2)], bits=BUP_BLOBBITS)
    res = [(len(b), lvl) for b, lvl in hs]
    WVPASSEQ(res, [(len(d1), 0), (len(d2), 0)])

    hs = HashSplitter([bio(d1), bio(d2)],
                      bits=BUP_BLOBBITS, keep_boundaries=False, fanbits=1)
    res = [(len(b), lvl) for b, lvl in hs]
    # Subtract 1 because bup has always ignored one bit between the
    # blob bits and the fanout bits.
    WVPASSEQ(res, [(len(data), 27 - BUP_BLOBBITS - 1)])

def test_hashsplitter_object():
    def _splitbuf(data):
        data = data[:]
        hs = HashSplitter([BytesIO(data)], bits=BUP_BLOBBITS, fanbits=1)
        for blob, lvl in hs:
            yield len(blob), 13 + lvl
    def _splitbuf_rhs(data):
        offs = None
        fed = 0
        data = data[:]
        s = RecordHashSplitter(bits=BUP_BLOBBITS)
        while offs != 0:
            while data:
                offs, bits = s.feed(data[:1])
                fed += 1
                # The recordhashsplitter doesn't return bits when it
                # hits the limit, so paper over the difference.
                if offs:
                    if bits is None:
                        assert fed == 1 << (BUP_BLOBBITS + 2)
                        bits = 13
                    yield fed, bits
                    fed = 0
                data = data[1:]
        yield fed, 13
    data = b''.join([b'%d\n' % x for x in range(10000)])
    WVPASSEQ(list(_splitbuf(data)),
             list(x for x in _splitbuf_rhs(data)))
    data = b''.join([b'%.10x\n' % x for x in range(10000)])
    WVPASSEQ(list(_splitbuf(data)),
             list(_splitbuf_rhs(data)))


def _record_cuts(records, mode):
    """Return the cumulative offsets where RecordHashSplitter cut."""
    s = RecordHashSplitter(bits=BUP_BLOBBITS, mode=mode)
    cuts = []
    ofs = 0
    for rec in records:
        ofs += len(rec)
        offs, bits = s.feed(rec)
        if offs:
            cuts.append((ofs, bits))
    return cuts

def test_recordhashsplitter_cdc_modes():
    rng = random.Random(1)
    data = bytes(rng.getrandbits(8) for _ in range(200000))
    bytes_1 = [data[i:i+1] for i in range(len(data))]

    # Determinism for a given record sequence, plus bounded chunks and
    # valid bits for record-sized feeds.  Forced splits report no bits.
    lines = data.split(b'\n')
    lines = [l + b'\n' for l in lines[:-1]] + [lines[-1]]
    max_chunk = 1 << (BUP_BLOBBITS + 2)
    max_rec = max(len(l) for l in lines)

    for mode in ('fastcdc', 'ae'):
        file_chunks = [len(b) for b, _ in HashSplitter([BytesIO(data)],
                                                       bits=BUP_BLOBBITS,
                                                       fanbits=1,
                                                       mode=mode)]
        file_cuts = list(itertools.accumulate(file_chunks))[:-1]

        # The fastcdc finder does not test the last buffer position, so
        # its record cuts land one byte after the file splitter's.  AE
        # matches exactly.
        shift = 1 if mode == 'fastcdc' else 0
        cuts = _record_cuts(bytes_1, mode)
        WVPASSEQ([ofs for ofs, _ in cuts],
                 [ofs + shift for ofs in file_cuts[:len(cuts)]])
        WVPASS(len(cuts) >= len(file_cuts) - 1)

        a = _record_cuts(lines, mode)
        WVPASSEQ(a, _record_cuts(lines, mode))
        prev = 0
        for ofs, bits in a:
            WVPASS(ofs - prev <= max_chunk + max_rec)
            if bits is not None:
                WVPASS(bits >= BUP_BLOBBITS)
            prev = ofs
        WVPASS(len(a) > 1)

def test_recordhashsplitter_ae_matches_file_cuts():
    # AE cuts after the byte the file splitter cuts at, so a record
    # stream ends the chunk at the first record end at or after each
    # file cut.
    rng = random.Random(2)
    data = bytes(rng.getrandbits(8) for _ in range(200000))
    recs, i = [], 0
    while i < len(data):
        n = rng.randint(1, 50)
        recs.append(data[i:i+n])
        i += n
    file_chunks = [len(b) for b, _ in HashSplitter([BytesIO(data)],
                                                   bits=BUP_BLOBBITS,
                                                   fanbits=1,
                                                   mode='ae')]
    file_cuts = list(itertools.accumulate(file_chunks))[:-1]
    rec_ends = list(itertools.accumulate(len(r) for r in recs))
    expected = [next(e for e in rec_ends if e >= c) for c in file_cuts]
    WVPASSEQ([ofs for ofs, _ in _record_cuts(recs, 'ae')], expected)

def test_recordhashsplitter_ae_level_from_chunk_length():
    rng = random.Random(8)
    data = bytes(rng.getrandbits(8) for _ in range(200000))
    recs, i = [], 0
    while i < len(data):
        n = rng.randint(1, 50)
        recs.append(data[i:i+n])
        i += n
    cuts = _record_cuts(recs, 'ae')
    WVPASS(len(cuts) > 1)
    prev = 0
    for ofs, bits in cuts:
        WVPASSEQ(bits - BUP_BLOBBITS, _trailing_zeros(ofs - prev))
        prev = ofs

_chunk_modes = ('legacy', 'ae')

class _TrickleIO:
    """Return at most step bytes per read."""
    def __init__(self, data, step):
        self._f = BytesIO(data)
        self._step = step
    def read(self, size=None):
        if size is None or size > self._step:
            size = self._step
        return self._f.read(size)

def _split_items(files, mode):
    hs = HashSplitter(files, bits=BUP_BLOBBITS, fanbits=1, mode=mode)
    return list(hs)

def _chunk_test_data():
    rnd = random.Random(1234)
    return [rnd.randbytes(1 << 20),
            bytes(1 << 19),
            b'\xaa' * (1 << 18) + rnd.randbytes(1 << 18)]

def test_hashsplit_modes_trickle_reads():
    for data in _chunk_test_data():
        for mode in _chunk_modes:
            whole = _split_items([BytesIO(data)], mode)
            for step in (7, 37):
                WVPASSEQ(_split_items([_TrickleIO(data, step)], mode), whole)

def test_hashsplit_modes_differ():
    data = _chunk_test_data()[0]
    items = {mode: _split_items([BytesIO(data)], mode) for mode in _chunk_modes}
    for a in _chunk_modes:
        for b in _chunk_modes:
            if a < b:
                WVPASS(items[a] != items[b])

def _ae_reference_lengths(data, bits):
    """Chunk lengths from a direct transcription of AE's Algorithm 1."""
    w = (1 << bits) - 256
    max_blob = 1 << (bits + 2)
    lengths = []
    start = 0
    while start < len(data):
        buf = data[start:start + max_blob]
        cut = None
        max_val, max_pos = buf[0], 0
        for i in range(1, len(buf)):
            if buf[i] > max_val:
                max_val, max_pos = buf[i], i
            elif i == max_pos + w:
                cut = i + 1
                break
        if cut is None:
            cut = len(buf)
        lengths.append(cut)
        start += cut
    return lengths

def test_hashsplit_ae_matches_reference():
    for data in _chunk_test_data():
        expected = _ae_reference_lengths(data, BUP_BLOBBITS)
        items = _split_items([BytesIO(data)], 'ae')
        WVPASSEQ([len(blob) for blob, _level in items], expected)

def test_hashsplit_ae_mean_chunk_size():
    data = random.Random(5).randbytes(4 << 20)
    leaves = [len(blob) for blob, level in _split_items([BytesIO(data)], 'ae')
              if level == 0]
    mean = sum(leaves) / len(leaves)
    WVPASS(abs(mean / (1 << BUP_BLOBBITS) - 1) < 0.1)

def _trailing_zeros(n):
    t = 0
    while not (n >> t) & 1:
        t += 1
    return t

def test_hashsplit_ae_level_from_chunk_length():
    data = random.Random(6).randbytes(1 << 22)
    items = _split_items([BytesIO(data)], 'ae')
    # The final chunk is not a cut, so it isn't given a level from its length
    for blob, level in items[:-1]:
        WVPASSEQ(level, _trailing_zeros(len(blob)))
    WVPASS(any(level for _blob, level in items))

def test_hashsplitter_short_read():
    class DataObj:
        """
        An instance of this returns 16 chunks of 2048 bytes for a total
        of 32k, the maximum blob size. This checks that the HashSplitter
        correctly combines all those reads into a single blob rather
        than creating a new chunk for each of the (short) reads.
        """
        def __init__(self):
            self._count = 16
        def read(self, size=None):
            assert size is None or size >= 2048
            if self._count > 0:
                self._count -= 1
                return b'x' * 2048
            return b''
    hs = HashSplitter([DataObj()], bits=13)
    count = 0
    for _ in hs: count += 1
    assert count == 1
