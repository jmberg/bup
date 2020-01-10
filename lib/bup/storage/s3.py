"""
S3 storage

Uses S3 compatible object storage for data, metadata and idx files
"""
import os
import sys
import fnmatch
import queue
import threading

from bup.helpers import mkdirp
from bup.path import index_cache
from bup.storage import BupStorage, FileAlreadyExists, FileModified, FileNotFound, Kind

try:
    import boto3
    from botocore.exceptions import ClientError as BotoClientError
except ImportError:
    boto3 = None


MIN_S3_CHUNK_SIZE = 1024 * 1024 * 5
DEFAULT_S3_CHUNK_SIZE = MIN_S3_CHUNK_SIZE * 10

_NOT_FOUND = ('404', 'NoSuchKey', 'NotFound')
_PRECONDITION_FAILED = ('PreconditionFailed', '412')


def _error_code(e):
    return e.response.get('Error', {}).get('Code')


class UploadThread(threading.Thread):
    def __init__(self, write):
        super(UploadThread, self).__init__()
        self.exc = None
        self.write = write
        self._queue = queue.Queue(maxsize=1)
        self.daemon = True

    def run(self):
        try:
            while True:
                try:
                    buf = self._queue.get()
                    if buf is None:
                        return
                    self.write(buf)
                finally:
                    self._queue.task_done()
        # pylint: disable-next=bare-except
        except:
            self.exc = sys.exc_info()

    def _join_queue_check(self):
        self._queue.join()
        if self.exc:
            self.join() # clean up the thread
            raise self.exc[1].with_traceback(self.exc[2])

    def put(self, buf):
        assert self.write is not None
        self._join_queue_check()
        self._queue.put(buf)

    def finish(self):
        self.put(None)
        self._join_queue_check()
        self.join()
        self.write = None


def _kind_to_prefix(kind):
    return {
        Kind.IDX: 'idx/',
        Kind.CONFIG: 'conf/',
        Kind.REFS: 'refs/',
        # data/metadata must be same, reader doesn't
        # know which it is and just use Kind.DATA
        Kind.DATA: 'data/',
        Kind.METADATA: 'data/',
    }[kind]

def _munge(name, kind):
    return _kind_to_prefix(kind) + name


class S3Reader:
    def __init__(self, storage, name, kind):
        self.storage = storage
        self.name = name
        self.objname = _munge(name.decode('utf-8'), kind)
        ret = storage._head(self.objname)
        if ret is None:
            raise FileNotFound(name)
        self.offs = 0
        self.etag = ret['ETag']
        self.size = int(ret['ContentLength'])

    # pylint: disable-next=unused-argument
    def read(self, sz=None, szhint=None):
        if sz is None:
            sz = self.size - self.offs
        sz = min(sz, self.size - self.offs)
        if sz <= 0:
            return b''

        storage = self.storage
        try:
            ret = storage.s3.get_object(
                Bucket=storage.bucket,
                Key=self.objname,
                Range='bytes=%d-%d' % (self.offs, self.offs + sz - 1),
                IfMatch=self.etag,
            )
        except BotoClientError as e:
            if _error_code(e) in _PRECONDITION_FAILED:
                raise FileModified(self.name) from e
            raise
        data = ret['Body'].read(sz)
        assert len(data) == sz
        self.offs += sz
        return data

    def close(self):
        pass

    def seek(self, offs):
        self.offs = offs

class S3CacheReader:
    # TODO: this class is not concurrency safe
    # TODO: this class sort of relies on sparse files (at least for efficiency)
    def __init__(self, storage, name, cachefile, blksize, kind):
        self._reader = None
        self.storage = storage
        self.name = name
        self.kind = kind
        self.fn_rngs = cachefile + b'.rngs'
        self.fn_data = cachefile + b'.data'
        self.offs = 0
        self.blksize = blksize
        self.f_data = None
        self.size = None

        if not os.path.exists(self.fn_rngs):
            # unlink data if present, we don't know how valid it is
            # without having the validity ranges
            if os.path.exists(self.fn_data):
                os.unlink(self.fn_data)
            self.ranges = []
        else:
            with open(self.fn_rngs, 'r+b') as f_rngs:
                ranges = []
                for line in f_rngs.readlines():
                    line = line.strip()
                    if line.startswith(b'#size='):
                        self.size = int(line[6:])
                        continue
                    if not line or line.startswith(b'#'):
                        continue
                    start, end = line.split(b'-')
                    start = int(start.strip())
                    end = int(end.strip())
                    ranges.append((start, end))
                self.ranges = ranges
                # some things we do rely on ordering
                self.ranges.sort()
                if self.ranges:
                    # pylint: disable-next=consider-using-with
                    self.f_data = open(self.fn_data, 'r+b')
        if not self.size:
            self.size = self.reader.size
            # if we didn't have the size cached write it out now
            self._write_ranges()

    @property
    def reader(self):
        if not self._reader:
            self._reader = S3Reader(self.storage, self.name, self.kind)
        return self._reader

    def _compress_ranges(self):
        ranges = []
        if not self.ranges:
            return
        self.ranges.sort()
        new_start, new_end = None, None
        for start, end in self.ranges:
            if new_start is None:
                new_start, new_end = start, end
                continue
            if new_end + 1 == start:
                new_end = end
                continue
            ranges.append((new_start, new_end))
            new_start, new_end = start, end
        if new_start is not None:
            ranges.append((new_start, new_end))
        self.ranges = ranges

    def _write_data(self, offset, data):
        assert len(data) > 0

        if self.f_data is None:
            # pylint: disable-next=consider-using-with
            self.f_data = open(self.fn_data, 'w+b')
        self.f_data.seek(offset)
        self.f_data.write(data)

        # update ranges file
        sz = len(data)
        self.ranges.append((offset, offset + sz - 1))
        self._write_ranges()

    def _write_ranges(self):
        self._compress_ranges()
        with open(self.fn_rngs, 'w+b') as f_rngs:
            f_rngs.write(b'#size=%d\n' % self.size)
            f_rngs.write(b'\n'.join((b'%d - %d' % i for i in self.ranges)))
            f_rngs.write(b'\n')

    def read(self, sz=None, szhint=None):
        data = []
        beginoffs = self.offs
        if sz is None:
            sz = self.size - self.offs
        if szhint is None:
            szhint = sz
        szhint = min(szhint, self.size - self.offs)
        sz = min(sz, self.size - self.offs)
        origsz = sz
        while sz:
            # find a range that overlaps the start of the needed data (if any)
            gotcached = False
            for start, end in self.ranges:
                if start <= self.offs and self.offs <= end:
                    self.f_data.seek(self.offs)
                    rsz = sz
                    avail = end - self.offs + 1
                    rsz = min(rsz, avail)
                    rdata = self.f_data.read(rsz)
                    assert len(rdata) == rsz
                    data.append(rdata)
                    sz -= rsz
                    self.offs += rsz
                    gotcached = True
                    break
            if gotcached:
                continue

            # read up to szhint from original offset
            offs = self.offs
            # round the offset down to a download block
            blksize = self.blksize
            offs = blksize * (offs // blksize)
            # calculate how much was requested (including hint)
            toread = szhint - (offs - beginoffs)
            # and round that up to the next blksize too (subject to EOF limit)
            toread = blksize * ((toread + blksize - 1) // blksize)
            if offs + toread > self.size:
                toread = self.size - offs
            # and download what we calculated, unless we find overlap
            for start, end in self.ranges:
                if offs < start < offs + toread:
                    toread = start - offs
                    break
            # grab it from the reader
            self.reader.seek(offs)
            rdata = self.reader.read(toread)
            assert len(rdata) == toread
            # and store it in the cache - next loop iteration will find it
            # (this avoids having to worry about szhint specifically here)
            self._write_data(offs, rdata)

        ret = b''.join(data)
        assert len(ret) == origsz
        return ret

    def seek(self, offs):
        assert offs <= self.size
        self.offs = offs

    def close(self):
        if self.f_data is not None:
            self.f_data.close()
        if self._reader:
            self._reader.close()


class UploadBuffer:
    def __init__(self):
        self._bufs = []
        self._len = 0

    def __len__(self):
        return self._len

    def write(self, b):
        if not b:
            return
        self._bufs.append(b)
        self._len += len(b)

    def __bytes__(self):
        return b''.join(self._bufs)


class S3Writer:
    def __init__(self, storage, name, kind, overwrite):
        # set to None once closed or aborted
        self.storage = storage
        self.s3 = storage.s3
        self.bucket = storage.bucket
        self.name = name
        self.objname = _munge(name.decode('utf-8'), kind)
        self.buf = UploadBuffer()
        self.size = 0
        self.kind = kind
        self.etags = []
        self.upload_id = None
        self.upload_thread = None
        self.chunk_size = storage.chunk_size
        self.overwrite = overwrite

        if self.overwrite is None:
            self.upload_thread = UploadThread(self._bg_upload)
            self.upload_thread.start()

    def __del__(self):
        if self.storage is not None:
            self.abort()

    def _bg_upload(self, buf):
        ret = self.s3.upload_part(
            Body=bytes(buf),
            Bucket=self.bucket,
            ContentLength=len(buf),
            Key=self.objname,
            UploadId=self.upload_id,
            PartNumber=len(self.etags) + 1,
        )
        self.etags.append(ret['ETag'])

    def _start_upload(self):
        if self.upload_id is not None:
            return
        storage_class = self.storage._get_storage_class(self.kind, self.size)
        self.upload_id = self.s3.create_multipart_upload(
            Bucket=self.bucket,
            StorageClass=storage_class,
            Key=self.objname,
        )['UploadId']

    def _upload_buf(self):
        self._start_upload()
        self.upload_thread.put(self.buf)
        self.buf = UploadBuffer()

    def write(self, data):
        assert self.storage is not None
        if self.upload_thread is None:
            self.buf.write(data)
            self.size += len(data)
            return
        # must send at least 5 MB chunks (except last), upload exactly
        # the chunk size so we avoid even any kind of fingerprinting here
        while len(self.buf) + len(data) >= self.chunk_size:
            needed = self.chunk_size - len(self.buf)
            self.buf.write(data[:needed])
            # count the data before uploading it so the storage class
            # is chosen based on the size of the whole object
            self.size += needed
            self._upload_buf()
            data = data[needed:]
        self.buf.write(data)
        self.size += len(data)

    def _end_thread(self):
        if self.upload_thread:
            thread = self.upload_thread
            self.upload_thread = None
            thread.finish()

    def close(self):
        assert self.storage is not None
        if self.overwrite is not None:
            try:
                self.s3.put_object(
                    Bucket=self.bucket,
                    Key=self.objname,
                    Body=bytes(self.buf),
                    IfMatch=self.overwrite.etag,
                )
            except BotoClientError as e:
                if _error_code(e) in _PRECONDITION_FAILED + _NOT_FOUND:
                    raise FileModified(self.name) from e
                raise
            self.storage = None
            return

        if len(self.buf) or not self.etags:
            self._upload_buf()
        self._end_thread()
        try:
            self.s3.complete_multipart_upload(
                Bucket=self.bucket,
                Key=self.objname,
                MultipartUpload={
                    'Parts': [
                        {
                            'ETag': etag,
                            'PartNumber': n + 1,
                        }
                        for n, etag in enumerate(self.etags)
                    ]
                },
                UploadId=self.upload_id,
                IfNoneMatch='*',
            )
        except BotoClientError as e:
            if _error_code(e) in _PRECONDITION_FAILED:
                raise FileAlreadyExists(self.name) from e
            raise
        self.storage = None

    def abort(self):
        assert self.storage is not None
        s3 = self.s3
        upload_id = self.upload_id
        self.storage = None
        try:
            self._end_thread()
        finally:
            if upload_id is not None:
                s3.abort_multipart_upload(Bucket=self.bucket,
                                          Key=self.objname,
                                          UploadId=upload_id)


class S3Storage(BupStorage):
    def __init__(self, repo, create=False):
        self._closed = True # for now

        if boto3 is None:
            raise Exception("S3Storage: missing boto3 module")

        def config_get(k, default=None):
            v = repo.access_config_get(k)
            if v is None:
                return default
            return v.decode('utf-8')

        repo_id = repo.access_config_get(b'bup.repo.id')
        if repo_id is None:
            raise Exception("S3Storage: needs 'bup.repo.id' in the access config")
        # keep the cache separate from the encrypted repo's own cache
        self.cachedir = os.path.join(index_cache(), b's3--' + repo_id)
        mkdirp(self.cachedir)

        self.bucket = config_get(b'bup.s3.bucket')
        if self.bucket is None:
            raise Exception("S3Storage: must have 'bucket' configuration")
        region_name = config_get(b'bup.s3.region')
        if region_name is None:
            raise Exception("S3Storage: must have 'region' configuration")

        access_key = config_get(b'bup.s3.accessKeyId')
        secret_key = config_get(b'bup.s3.secretAccessKey')
        session_token = config_get(b'bup.s3.sessionToken')
        if (access_key is None) != (secret_key is None):
            raise Exception("S3Storage: 'accessKeyId' and 'secretAccessKey' must be set together")
        if session_token is not None and access_key is None:
            raise Exception("S3Storage: 'sessionToken' requires 'accessKeyId' and 'secretAccessKey'")

        BupStorage.__init__(self, repo)

        # with no keys configured, boto3 uses its default chain
        session = boto3.session.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
            region_name=region_name,
        )

        self.s3 = session.client('s3', endpoint_url=config_get(b'bup.s3.endpoint-url'))

        defclass = config_get(b'bup.s3.defaultStorageClass', default='STANDARD')

        self.chunk_size = repo.access_config_get(b'bup.s3.chunkSize',
                                                 opttype='int')
        if self.chunk_size is None:
            self.chunk_size = DEFAULT_S3_CHUNK_SIZE
        if self.chunk_size < MIN_S3_CHUNK_SIZE:
            raise Exception('chunkSize must be >= 5 MiB')

        self.down_blksize = repo.access_config_get(b'bup.s3.downloadBlockSize',
                                                   opttype='int')
        if self.down_blksize is None:
            self.down_blksize = 8 * 1024
        if not self.down_blksize:
            raise Exception("downloadBlockSize cannot be zero")

        class StorageClassConfig:
            def __init__(self):
                self.small = None
                self.large = None
                self.threshold = None

        self.storage_classes = {}
        for kind, pfx in ((Kind.DATA, b'data'),
                          (Kind.METADATA, b'metadata'),
                          (Kind.IDX, b'idx')):
            clsdef = self.storage_classes[kind] = StorageClassConfig()
            kinddef = config_get(b'bup.s3.%sStorageClass' % pfx)
            clsdef.small = config_get(b'bup.s3.%sStorageClassSmall' % pfx)
            clsdef.large = config_get(b'bup.s3.%sStorageClassLarge' % pfx)
            clsdef.threshold = repo.access_config_get(b'bup.s3.%sStorageClassThreshold' % pfx,
                                                      opttype='int')
            if not kinddef:
                kinddef = defclass
            if clsdef.small is None:
                clsdef.small = kinddef
            if clsdef.large is None:
                clsdef.large = kinddef
            if clsdef.threshold is None:
                clsdef.threshold = 1024 * 1024
            if clsdef.threshold >= self.chunk_size:
                raise Exception("storage class threshold must be < chunkSize (default 50 MiB)")

        # config and refs are small and must always be readable at once
        config_storage_class = StorageClassConfig()
        config_storage_class.small = 'STANDARD'
        config_storage_class.large = 'STANDARD'
        config_storage_class.threshold = 0
        self.storage_classes[Kind.CONFIG] = config_storage_class
        self.storage_classes[Kind.REFS] = config_storage_class

        if create:
            kwargs = {}
            # S3 rejects an explicit location constraint for us-east-1
            if region_name != 'us-east-1':
                kwargs['CreateBucketConfiguration'] = {
                    'LocationConstraint': region_name,
                }
            try:
                self.s3.create_bucket(Bucket=self.bucket, ACL='private', **kwargs)
            except BotoClientError as e:
                # re-initialising a repo in a bucket we already own is fine
                if _error_code(e) != 'BucketAlreadyOwnedByYou':
                    raise

    def _head(self, objname):
        try:
            return self.s3.head_object(Bucket=self.bucket, Key=objname)
        except BotoClientError as e:
            if _error_code(e) in _NOT_FOUND:
                return None
            raise

    def _get_storage_class(self, kind, size):
        clsdef = self.storage_classes[kind]
        if size <= clsdef.threshold:
            return clsdef.small
        return clsdef.large

    def get_writer(self, name, kind, overwrite=None):
        assert kind in (Kind.DATA, Kind.METADATA, Kind.IDX,
                        Kind.CONFIG, Kind.REFS)
        if overwrite is None and self._head(_munge(name.decode('utf-8'), kind)) is not None:
            raise FileAlreadyExists(name)
        return S3Writer(self, name, kind, overwrite)

    def get_reader(self, name, kind):
        assert kind in (Kind.DATA, Kind.METADATA, Kind.IDX,
                        Kind.CONFIG, Kind.REFS)
        if kind not in (Kind.DATA, Kind.METADATA):
            return S3Reader(self, name, kind)
        return S3CacheReader(self, name, os.path.join(self.cachedir, name),
                             self.down_blksize, kind)

    def list(self, kind, pattern=None):
        prefix = _kind_to_prefix(kind)
        paginator = self.s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get('Contents', []):
                name = item['Key'][len(prefix):].encode('utf-8')
                if pattern is None or fnmatch.fnmatch(name, pattern):
                    yield name
