import os
from contextlib import contextmanager

import pytest

from wvpytest import *

from bup.storage import FileAlreadyExists, FileModified, FileNotFound, Kind
from bup.storage.s3 import S3Storage
from bup.compat import environ

boto3 = pytest.importorskip('boto3')
moto = pytest.importorskip('moto')
mock_aws = getattr(moto, 'mock_aws', None)
if mock_aws is None:
    pytest.skip('moto too old (no mock_aws)', allow_module_level=True)


MiB = 1024 * 1024


class FakeRepo:
    def __init__(self, cfg):
        self.cfg = cfg

    def access_config_get(self, k, default=None, opttype=None):
        v = self.cfg.get(k, default)
        if v is None:
            return None
        if opttype == 'int':
            return int(v)
        return v


def read_all(rd):
    out = b''
    while True:
        b = rd.read(MiB)
        if not b:
            break
        out += b
    rd.close()
    return out


def s3_repo():
    return FakeRepo({
        b'bup.repo.id': b'testrepoid',
        b'bup.s3.bucket': b'test-bucket',
        b'bup.s3.region': b'us-east-1',
        b'bup.s3.accessKeyId': b'testing',
        b'bup.s3.secretAccessKey': b'testing',
        b'bup.s3.chunkSize': b'%d' % (5 * MiB),
    })


@contextmanager
def s3_store(cfg, create=True):
    with mock_aws():
        store = S3Storage(FakeRepo(cfg), create=create)
        try:
            yield store
        finally:
            store.close()


@pytest.fixture
def s3_storage():
    with s3_store(s3_repo().cfg) as store:
        yield store


def test_s3_create_existing_bucket():
    with mock_aws():
        # the fixture already created the bucket; creating it again must work
        store = S3Storage(s3_repo(), create=True)
        store.close()


# pylint: disable-next=redefined-outer-name
def test_s3_short_read_at_eof(s3_storage):
    wr = s3_storage.get_writer(b'short', Kind.DATA)
    wr.write(b'hello world')
    wr.close()
    rd = s3_storage.get_reader(b'short', Kind.DATA)
    # asking for more than remains must return exactly what remains
    assert rd.read(MiB) == b'hello world'
    assert rd.read(MiB) == b''
    rd.close()


# pylint: disable-next=redefined-outer-name
def test_s3_round_trip(s3_storage):
    store = s3_storage
    wr = store.get_writer(b'small', Kind.DATA)
    wr.write(b'hello world')
    wr.close()
    rd = store.get_reader(b'small', Kind.DATA)
    assert read_all(rd) == b'hello world'

    # multipart upload across several 5 MiB chunks
    big = os.urandom(12 * MiB)
    wr = store.get_writer(b'big', Kind.DATA)
    for off in range(0, len(big), MiB):
        wr.write(big[off:off + MiB])
    wr.close()
    rd = store.get_reader(b'big', Kind.DATA)
    assert read_all(rd) == big

    names = sorted(name for name, _ in store.list(Kind.DATA))
    assert b'small' in names and b'big' in names


# pylint: disable-next=redefined-outer-name
def test_s3_no_clobber(s3_storage):
    store = s3_storage
    wr = store.get_writer(b'pack-1', Kind.DATA)
    wr.write(b'a')
    wr.close()

    # rejected early, before any upload
    with pytest.raises(FileAlreadyExists):
        store.get_writer(b'pack-1', Kind.DATA)

    # another writer completes first; ours must be rejected at complete
    # time (IfNoneMatch), not silently replace it
    slow = store.get_writer(b'pack-2', Kind.DATA)
    slow.write(b'a' * (6 * MiB))
    fast = store.get_writer(b'pack-2', Kind.DATA)
    fast.write(b'b')
    fast.close()
    with pytest.raises(FileAlreadyExists):
        slow.close()
    assert read_all(store.get_reader(b'pack-2', Kind.DATA)) == b'b'


# pylint: disable-next=redefined-outer-name
def test_s3_overwrite(s3_storage):
    store = s3_storage
    wr = store.get_writer(b'refs', Kind.REFS)
    wr.write(b'one\n')
    wr.close()

    rd1 = store.get_reader(b'refs', Kind.REFS)
    wr = store.get_writer(b'refs', Kind.REFS, overwrite=rd1)
    wr.write(b'two\n')
    wr.close()
    assert read_all(store.get_reader(b'refs', Kind.REFS)) == b'two\n'

    # rd1 is now stale, so replacing based on it must fail
    with pytest.raises(FileModified):
        wr = store.get_writer(b'refs', Kind.REFS, overwrite=rd1)
        wr.write(b'three\n')
        wr.close()

    wr = store.get_writer(b'empty', Kind.CONFIG)
    wr.close()
    assert read_all(store.get_reader(b'empty', Kind.CONFIG)) == b''


# pylint: disable-next=redefined-outer-name
def test_s3_abort(s3_storage):
    store = s3_storage
    wr = store.get_writer(b'aborted', Kind.DATA)
    wr.write(b'x' * (6 * MiB))
    wr.abort()
    with pytest.raises(FileNotFound):
        store.get_reader(b'aborted', Kind.DATA)


# pylint: disable-next=redefined-outer-name
def test_s3_list_pattern(s3_storage):
    store = s3_storage
    for name in (b'pack-1.encidx', b'pack-1.other'):
        wr = store.get_writer(name, Kind.IDX)
        wr.write(b'i')
        wr.close()
    assert sorted(store.list(Kind.IDX, b'*.encidx')) == [(b'pack-1.encidx', 1)]


@pytest.mark.parametrize('keys, msg', [
    ({b'bup.s3.accessKeyId': b'testing'}, 'must be set together'),
    ({b'bup.s3.secretAccessKey': b'testing'}, 'must be set together'),
    ({b'bup.s3.sessionToken': b'tok'}, 'requires'),
])
def test_s3_partial_credentials_rejected(keys, msg):
    cfg = {k: v for k, v in s3_repo().cfg.items()
           if k not in (b'bup.s3.accessKeyId', b'bup.s3.secretAccessKey')}
    cfg.update(keys)
    with mock_aws(), pytest.raises(Exception, match=msg):
        S3Storage(FakeRepo(cfg), create=True)


def test_s3_default_credential_chain():
    environ[b'AWS_ACCESS_KEY_ID'] = b'testing'
    environ[b'AWS_SECRET_ACCESS_KEY'] = b'testing'
    cfg = {k: v for k, v in s3_repo().cfg.items()
           if k not in (b'bup.s3.accessKeyId', b'bup.s3.secretAccessKey')}
    with s3_store(cfg) as store:
        wr = store.get_writer(b'small', Kind.DATA)
        wr.write(b'chain')
        wr.close()
        assert read_all(store.get_reader(b'small', Kind.DATA)) == b'chain'


@pytest.mark.parametrize('kind', [Kind.DATA, Kind.REFS])
# pylint: disable-next=redefined-outer-name
def test_s3_seek_whence_and_tell(s3_storage, kind):
    wr = s3_storage.get_writer(b'seekme', kind)
    wr.write(b'hello world')
    wr.close()
    rd = s3_storage.get_reader(b'seekme', kind)
    rd.seek(-5, os.SEEK_END)
    assert rd.tell() == 6
    assert rd.read(MiB) == b'world'
    rd.seek(2)
    rd.seek(3, os.SEEK_CUR)
    assert rd.tell() == 5
    assert rd.read(1) == b' '
    rd.seek(0, os.SEEK_END)
    assert rd.tell() == 11
    assert rd.read(MiB) == b''
    with pytest.raises(ValueError):
        rd.seek(-12, os.SEEK_END)
    rd.close()


# pylint: disable-next=redefined-outer-name
def test_s3_detects_size_change(s3_storage):
    wr = s3_storage.get_writer(b'truncated', Kind.DATA)
    data = b'asdf' * 10
    wr.write(b'asdf' * 10)
    wr.close()

    rd = s3_storage.get_reader(b'truncated', Kind.DATA)
    assert rd.read(40) == data
    rd.close()

    assert s3_storage.s3.put_object(
        Bucket=s3_storage.bucket,
        Key='data/truncated',
        Body=bytes(b'asdf' * 3),
    )['ResponseMetadata']['HTTPStatusCode'] == 200

    # This isn't really the best detection, but we do want
    # to avoid hitting S3 for every request that's otherwise
    # cached ... so we can't rely on rd.read(), it'll return
    # data from the cache directly.
    rd = s3_storage.get_reader(b'truncated', Kind.DATA)
    with pytest.raises(Exception) as exc:
        rd.reader
    assert 'Cached size' in str(exc.value)
    rd.close()


# pylint: disable-next=redefined-outer-name
def test_s3_detects_content_change(s3_storage):
    wr = s3_storage.get_writer(b'changed', Kind.DATA)
    data = b'asdf' * 10
    wr.write(b'asdf' * 10)
    wr.close()

    rd = s3_storage.get_reader(b'changed', Kind.DATA)
    assert rd.read(40) == data
    rd.close()

    assert s3_storage.s3.put_object(
        Bucket=s3_storage.bucket,
        Key='data/changed',
        Body=bytes(b'xsdf' * 10),
    )['ResponseMetadata']['HTTPStatusCode'] == 200

    # This isn't really the best detection, but we do want
    # to avoid hitting S3 for every request that's otherwise
    # cached ... so we can't rely on rd.read(), it'll return
    # data from the cache directly.
    rd = s3_storage.get_reader(b'changed', Kind.DATA)
    with pytest.raises(Exception) as exc:
        rd.reader
    assert 'Cached etag' in str(exc.value)
    rd.close()
