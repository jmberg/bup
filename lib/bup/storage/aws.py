"""
AWS storage

Uses S3 compatible object storage for data, metadata and idx files
"""
import os
import sys
import fnmatch
import datetime
import threading
import queue

from bup.storage import BupStorage, FileNotFound, Kind
# FIXME FileAlreadyExists

try:
    import boto3
    from botocore.exceptions import ClientError as BotoClientError
except ImportError:
    boto3 = None


MIN_AWS_CHUNK_SIZE = 1024 * 1024 * 5
DEFAULT_AWS_CHUNK_SIZE = MIN_AWS_CHUNK_SIZE * 10


class UploadThread(threading.Thread):
    def __init__(self, write):
        super(UploadThread, self).__init__()
        self.exc = None
        self.write = write
        self._queue = queue.Queue(maxsize=1)
        self.setDaemon(True)

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
        except:
            self.exc = sys.exc_info()

    def _join_queue_check(self):
        self._queue.join()
        if self.exc:
            self.join() # clean up the thread
            raise Exception(self.exc[1]).with_traceback(self.exc[2])

    def put(self, buf):
        assert self.write is not None
        self._join_queue_check()
        self._queue.put(buf)

    def finish(self):
        self.put(None)
        self._join_queue_check()
        self.join()
        self.write = None


def _nowstr():
    # time.time() appears to return the same, but doesn't
    # actually seem to guarantee UTC
    return datetime.datetime.utcnow().strftime('%s')

def _munge(name):
    # do some sharding - S3 uses the first few characters for this
    if name.startswith('refs/'):
        return name
    if name.startswith('conf/'):
        return name
    assert name.startswith('pack-')
    return name[5:9] + '/' + name

def _unmunge(name):
    if name.startswith('refs/'):
        return name
    if name.startswith('conf/'):
        return name
    assert name[4:10] == '/pack-'
    return name[5:]

class S3Reader:
    def __init__(self, storage, name):
        self.storage = storage
        self.name = name
        self.objname = _munge(name)
        try:
            ret = storage.s3.head_object(
                Bucket=storage.bucket,
                Key=self.objname,
            )
        except BotoClientError: # FIXME?
            raise FileNotFound(name)
        self.offs = 0
        self.etag = ret['ETag']
        self.size = int(ret['ContentLength'])

    def read(self, sz=None, szhint=None):
        assert sz > 0, "size must be positive (%d)" % sz
        if sz is None:
            sz = self.size - self.offs

        startrange = 'bytes=%d-' % self.offs
        retr_range = '%s%d' % (startrange, self.offs + sz - 1)
        storage = self.storage
        try:
            ret = storage.s3.get_object(
                Bucket=storage.bucket,
                Key=self.objname,
                Range=retr_range,
            )
        except BotoClientError:
            raise Exception("cannot access %s (check storage class)" % self.objname)
        assert 'ContentRange' in ret
        startrange = startrange.replace('=', ' ')
        assert ret['ContentRange'].startswith(startrange)
        self.offs += sz
        return ret['Body'].read(sz)

    def close(self):
        pass

    def seek(self, offs):
        self.offs = offs

class S3CacheReader:
    # TODO: this class is not concurrency safe
    # TODO: this class sort of relies on sparse files (at least for efficiency)
    def __init__(self, storage, name, cachedir, blksize):
        self._reader = None
        self.storage = storage
        self.name = name
        self.fn_rngs = os.path.join(cachedir, self.name + '.rngs')
        self.fn_data = os.path.join(cachedir, self.name + '.data')
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
            f_rngs = open(self.fn_rngs, 'r+b')
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
                self.f_data = open(self.fn_data, 'r+b')

            f_rngs.close()
        if not self.size:
            self.size = self.reader.size
            # if we didn't have the size cached write it out now
            self._write_ranges()

    @property
    def reader(self):
        if not self._reader:
            self._reader = S3Reader(self.storage, self.name)
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
            self.f_data = open(self.fn_data, 'w+b')
        self.f_data.seek(offset)
        self.f_data.write(data)

        # update ranges file
        sz = len(data)
        self.ranges.append((offset, offset + sz - 1))
        self._write_ranges()

    def _write_ranges(self):
        self._compress_ranges()
        f_rngs = open(self.fn_rngs, 'w+b')
        f_rngs.write(b'#size=%d\n' % self.size)
        f_rngs.write(b'\n'.join((b'%d - %d' % i for i in self.ranges)))
        f_rngs.write(b'\n')
        f_rngs.close()

    def read(self, sz=None, szhint=None):
        data = []
        beginoffs = self.offs
        if szhint is None:
            szhint = sz
        if szhint > self.size - self.offs:
            szhint = self.size - self.offs
        if sz is None:
            sz = self.size - self.offs
        origsz = sz
        while sz:
            # find a range that overlaps the start of the needed data (if any)
            gotcached = False
            for start, end in self.ranges:
                if start <= self.offs and self.offs <= end:
                    self.f_data.seek(self.offs)
                    rsz = sz
                    avail = end - self.offs + 1
                    if rsz > avail:
                        rsz = avail
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

def _check_exc(e, *codes):
    if not hasattr(e, 'response'):
        return
    if not 'Error' in e.response:
        return
    if not 'Code' in e.response['Error']:
        return
    if not e.response['Error']['Code'] in codes:
        raise

class UploadFile:
    def __init__(self):
        self._bufs = []
        self._len = 0
        self._finished = False
        self._pos = None

    def __len__(self):
        return self._len

    def write(self, b):
        assert not self._finished
        if not b:
            return
        sz = len(b)
        self._bufs.append(b)
        self._len += sz

    def finish(self):
        self._finished = True
        self._pos = (0, 0)

    def tell(self):
        assert self._finished
        assert self._pos == (0, 0)
        return 0

    def seek(self, pos):
        assert self._finished
        assert pos <= self._len
        assert pos == 0
        self._pos = (0, 0)

    def read(self, sz):
        idx, subpos = self._pos
        if idx >= len(self._bufs):
            return b''
        rem = len(self._bufs[idx]) - subpos
        if sz < rem:
            self._pos = (idx, subpos + sz)
            return self._bufs[idx][subpos:subpos + sz]
        self._pos = (idx + 1, 0)
        if subpos == 0:
            return self._bufs[idx]
        return self._bufs[idx][subpos:]

    def __bytes__(self):
        return b''.join(self._bufs)

class S3Writer:
    def __init__(self, storage, name, kind, overwrite):
        self.storage = None
        self.name = name
        self.objname = _munge(name)
        self.buf = UploadFile()
        self.size = 0
        self.kind = kind
        self.etags = []
        self.upload_id = None
        self.upload_thread = None
        self.chunk_size = storage.chunk_size
        self.overwrite = overwrite
        self.storage = storage

        if self.overwrite is None:
            self.upload_thread = UploadThread(self._bg_upload)
            self.upload_thread.start()

    def __del__(self):
        self._end_thread()
        if self.storage:
            self.abort()

    def _bg_upload(self, buf):
        ret = self.storage.s3.upload_part(
            Body=bytes(buf),
            Bucket=self.storage.bucket,
            ContentLength=len(buf),
            Key=self.objname,
            UploadId=self.upload_id,
            PartNumber=len(self.etags) + 1,
        )
        self.etags.append(ret['ETag'])

    def _start_upload(self):
        if self.upload_id is not None:
            return
        storage = self.storage
        storage_class = self.storage._get_storage_class(self.kind, self.size)
        self.upload_id = storage.s3.create_multipart_upload(
            Bucket=storage.bucket,
            StorageClass=storage_class,
            Key=self.objname,
        )['UploadId']

    def _upload_buf(self):
        self._start_upload()
        self.buf.finish()
        self.upload_thread.put(self.buf)
        self.buf = UploadFile()

    def write(self, data):
        sz = len(data)
        # must send at least 5 MB chunks (except last)
        if self.upload_thread and len(self.buf) + sz >= self.chunk_size:
            # upload exactly the chunk size so we avoid even any kind
            # of fingerprinting here... seems paranoid but why not
            needed = self.chunk_size - len(self.buf)
            self.buf.write(data[:needed])
            self._upload_buf()
            data = data[needed:]
            sz -= needed
            self.size += needed
            if not sz:
                return
        self.buf.write(data)
        self.size += sz

    def _end_thread(self):
        if self.upload_thread:
            self.upload_thread.finish()
            self.upload_thread = None

    def close(self):
        if self.storage is None:
            self._end_thread()
            return
        if self.overwrite is not None:
            assert self.upload_thread is None
            try:
                self.storage.s3.put_object(
                    Bucket=self.storage.bucket,
                    Key=self.objname,
                    Body=bytes(self.buf),
                    IfMatch=self.overwrite.etag,
                )
            except:
                raise # FIXME
            return
        self._upload_buf()
        self._end_thread()
        storage = self.storage
        storage.s3.complete_multipart_upload(
            Bucket=storage.bucket,
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
        self.storage = None
        self.etags = None

    def abort(self):
        storage = self.storage
        self.storage = None
        self._end_thread()
        if self.upload_id is not None:
            storage.s3.abort_multipart_upload(Bucket=storage.bucket,
                                              Key=self.objname,
                                              UploadId=self.upload_id)


class AWSStorage(BupStorage):
    def __init__(self, repo, create=False):
        BupStorage.__init__(self, repo)
        if boto3 is None:
            raise Exception("AWSStorage: missing boto3 module")

        # no support for opttype, if it's bool or int no need to decode
        def config_get(k, default=None, opttype=None):
            # AWS options are needed to access the repo, others
            # (e.g. bup.separatemeta) are for the repo contents
            if k.startswith(b'bup.aws.'):
                v = repo.access_config_get(k, opttype=opttype)
            else:
                v = repo.config_get(k, opttype=opttype)
            if v is None:
                return default
            return v.decode('utf-8')

        self.cachedir = config_get(b'bup.aws.cachedir', opttype='path')
        if not self.cachedir:
            raise Exception("AWSStorage: cachedir is required")

        self.bucket = config_get(b'bup.aws.s3bucket')
        if self.bucket is None:
            raise Exception("AWSStorage: must have 's3bucket' configuration")
        region_name = config_get(b'bup.aws.region')
        if region_name is None:
            raise Exception("AWSStorage: must have 'region' configuration")

        session = boto3.session.Session(
            aws_access_key_id=config_get(b'bup.aws.accessKeyId'),
            aws_secret_access_key=config_get(b'bup.aws.secretAccessKey'),
            aws_session_token=config_get(b'bup.aws.sessionToken'),
            region_name=region_name,
        )

        self.s3 = session.client('s3', endpoint_url=config_get(b'bup.aws.endpoint-url'))

        defclass = config_get(b'bup.aws.defaultStorageClass',
                              default='STANDARD')

        self.chunk_size = repo.access_config_get(b'bup.aws.chunkSize',
                                                 opttype='int')
        if self.chunk_size is None:
            self.chunk_size = DEFAULT_AWS_CHUNK_SIZE
        if self.chunk_size < MIN_AWS_CHUNK_SIZE:
            raise Exception('chunkSize must be >= 5 MiB')

        self.down_blksize = repo.access_config_get(b'bup.aws.downloadBlockSize',
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
            kinddef = config_get(b'bup.aws.%sStorageClass' % pfx)
            clsdef.small = config_get(b'bup.aws.%sStorageClassSmall' % pfx)
            clsdef.large = config_get(b'bup.aws.%sStorageClassLarge' % pfx)
            clsdef.threshold = repo.access_config_get(b'bup.aws.%sStorageClassThreshold' % pfx,
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

        config_storage_class = StorageClassConfig()
        config_storage_class.large = 'STANDARD'
        config_storage_class.small = '--NEVER-USED--'
        config_storage_class.threshold = 0
        self.storage_classes[Kind.CONFIG] = config_storage_class
        self.storage_classes[Kind.REFS] = config_storage_class

        if create:
            self.s3.create_bucket(Bucket=self.bucket, ACL='private',
                                  CreateBucketConfiguration={
                                      'LocationConstraint': region_name,
                                  })

    def _get_storage_class(self, kind, size):
        clsdef = self.storage_classes[kind]
        if size <= clsdef.threshold:
            return clsdef.small
        return clsdef.large

    def get_writer(self, name, kind, overwrite=None):
        assert kind in (Kind.DATA, Kind.METADATA, Kind.IDX,
                        Kind.CONFIG, Kind.REFS)
        name = name.decode('utf-8')
        if kind == Kind.CONFIG:
            name = 'conf/' + name
        elif kind == Kind.REFS:
            name = 'refs/' + name
        return S3Writer(self, name, kind, overwrite)

    def get_reader(self, name, kind):
        assert kind in (Kind.DATA, Kind.METADATA, Kind.IDX,
                        Kind.CONFIG, Kind.REFS)
        name = name.decode('utf-8')
        if kind == Kind.CONFIG:
            name = 'conf/' + name
        elif kind == Kind.REFS:
            name = 'refs/' + name
        if not self.cachedir or kind not in (Kind.DATA, Kind.METADATA):
            return S3Reader(self, name)
        return S3CacheReader(self, name, self.cachedir, self.down_blksize)

    def list(self, pattern=None):
        # TODO: filter this somehow based on the pattern?
        token = None
        while True:
            if token is not None:
                ret = self.s3.list_objects_v2(
                    Bucket=self.bucket,
                    ContinuationToken=token,
                )
            else:
                ret = self.s3.list_objects_v2(
                    Bucket=self.bucket,
                )
            if ret['KeyCount'] == 0:
                break
            for item in ret['Contents']:
                key = item['Key']
                if key.startswith('refs/') or key.startswith('conf/'):
                    continue
                name = _unmunge(key).encode('ascii')
                if fnmatch.fnmatch(name, pattern):
                    yield name
            token = ret.get('NextContinuationToken')
            if token is None:
                break

    def close(self):
        super(AWSStorage, self).close()
