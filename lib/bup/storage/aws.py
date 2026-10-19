"""
AWS storage, for repositories using the original AWS layout.

Uses the following AWS products:
 * S3 object storage for data, metadata and idx files
 * DynamoDB for consistent file listing, and for small objects stored
   inline (refs and config)

This backend is read-only. Use the s3 storage.
"""
import fnmatch
import os

from bup.helpers import mkdirp
from bup.path import index_cache
from bup.storage import BupStorage, FileNotFound, Kind

try:
    import boto3
except ImportError:
    boto3 = None


INLINE_KINDS = (Kind.CONFIG, Kind.REFS)


def _munge(name):
    # the sharding must match the original layout, so existing objects are found
    assert name.startswith('pack-')
    return name[5:9] + '/' + name

class S3Reader:
    def __init__(self, storage, name, defer_checks=False):
        self.storage = storage
        self.name = name
        self.objname = _munge(name)
        self.offs = 0
        self.size = None
        if not defer_checks:
            self._finish_init()

    def _finish_init(self):
        response = self.storage.dynamo.query(TableName=self.storage.table,
                                             ConsistentRead=True,
                                             KeyConditionExpression='filename = :nm',
                                             ExpressionAttributeValues={
                                                 ':nm': { 'S': self.name, }
                                             })
        assert response['Count'] in (0, 1)
        # an in-progress upload has no size yet
        if response['Count'] == 0 or 'tentative' in response['Items'][0]:
            raise FileNotFound(name)
        assert self.name == response['Items'][0]['filename']['S']
        self.size = int(response['Items'][0]['size']['N'])

    # pylint: disable-next=unused-argument
    def read(self, sz=None, szhint=None):
        if self.size is None:
            self._finish_init()
        if sz is None:
            sz = self.size - self.offs
        sz = min(sz, self.size - self.offs)
        if sz <= 0:
            return b''

        storage = self.storage
        ret = storage.s3.get_object(
            Bucket=storage.bucket,
            Key=self.objname,
            Range='bytes=%d-%d' % (self.offs, self.offs + sz - 1),
        )
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
    def __init__(self, reader, cachedir, blksize):
        self.reader = reader
        self.name = reader.name
        self.size = None
        self.fn_rngs = os.path.join(cachedir, self.name + '.rngs')
        self.fn_data = os.path.join(cachedir, self.name + '.data')
        self.offs = 0
        self.blksize = blksize

        if not os.path.exists(self.fn_rngs):
            # unlink data if present, we don't know how valid it is
            # without having the validity ranges
            if os.path.exists(self.fn_data):
                os.unlink(self.fn_data)
            self.f_data = None
            self.ranges = []
        else:
            with open(self.fn_rngs, 'r+b') as f_rngs:
                # pylint: disable-next=consider-using-with
                self.f_data = open(self.fn_data, 'r+b')
                ranges = []
                for line in f_rngs.readlines():
                    line = line.strip()
                    if line.startswith(b'#size='):
                        self.size = int(line[6:].decode('ascii'))
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
        if self.size is None:
            self.reader._finish_init()
            self.size = self.reader.size

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
                if offs <= start < offs + toread:
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
        self.reader.close()

class DynamoReader:
    def __init__(self, storage, name):
        self.storage = storage
        self.name = name
        response = storage.dynamo.query(TableName=storage.table,
                                        ConsistentRead=True,
                                        KeyConditionExpression='filename = :nm',
                                        ExpressionAttributeValues={
                                            ':nm': { 'S': name, }
                                        })
        assert response['Count'] in (0, 1)
        if response['Count'] == 0:
            raise FileNotFound(name)
        item = response['Items'][0]
        assert item['filename']['S'] == name
        self.data = item['data']['B']
        self.offs = 0
        self.generation = int(item['generation']['N'])

    # pylint: disable-next=unused-argument
    def read(self, sz=None, szhint=None):
        assert self.data is not None
        maxread = len(self.data) - self.offs
        if sz is None or sz > maxread:
            sz = maxread
        ret = self.data[self.offs:self.offs + sz]
        self.offs += sz
        return ret

    def close(self):
        if self.data is not None:
            self.data = None

    def seek(self, offs):
        assert self.data is not None
        self.offs = offs

class AWSStorage(BupStorage):
    def __init__(self, repo, create=False):
        super().__init__(repo, create=create)
        if create:
            raise Exception('AWSStorage is read-only, cannot create a repository')
        if boto3 is None:
            raise Exception("AWSStorage: missing boto3 module")

        # no support for opttype, if it's bool or int no need to decode
        def config(k, default=None):
            v = repo.access_config_get(k)
            if v is None:
                return default
            return v.decode('utf-8')

        repo_id = repo.access_config_get(b'bup.repo.id')
        if repo_id is None:
            raise Exception("AWSStorage: needs 'bup.repo.id' in the access config")
        self.cachedir = os.fsdecode(os.path.join(index_cache(), b'aws--' + repo_id))
        mkdirp(self.cachedir)

        self.bucket = config(b'bup.aws.s3bucket')
        if self.bucket is None:
            raise Exception("AWSStorage: must have 's3bucket' configuration")
        self.table = config(b'bup.aws.dynamotable')
        if self.table is None:
            raise Exception("AWSStorage: must have 'dynamotable' configuration")
        region_name = config(b'bup.aws.region')
        if region_name is None:
            raise Exception("AWSStorage: must have 'region' configuration")

        session = boto3.session.Session(
            aws_access_key_id=config(b'bup.aws.accessKeyId'),
            aws_secret_access_key=config(b'bup.aws.secretAccessKey'),
            aws_session_token=config(b'bup.aws.sessionToken'),
            region_name=region_name,
        )

        self.s3 = session.client('s3')
        self.dynamo = session.client('dynamodb')

        self.down_blksize = repo.access_config_get(b'bup.aws.downloadBlockSize',
                                                   opttype='int')
        if self.down_blksize is None:
            self.down_blksize = 8 * 1024
        if not self.down_blksize:
            raise Exception("downloadBlockSize cannot be zero")

    def get_writer(self, name, kind, overwrite=None):
        raise Exception('AWSStorage is read-only, refusing to write %r'
                        % (name,))

    def get_reader(self, name, kind):
        name = name.decode('utf-8')
        if kind in INLINE_KINDS:
            return DynamoReader(self, name)
        assert kind in (Kind.DATA, Kind.METADATA, Kind.IDX)
        if kind not in (Kind.DATA, Kind.METADATA):
            return S3Reader(self, name)
        reader = S3Reader(self, name, defer_checks=True)
        return S3CacheReader(reader, self.cachedir, self.down_blksize)

    def list(self, kind, pattern=None):
        inline = kind in INLINE_KINDS
        scan_args = {
            'TableName': self.table,
            'ConsistentRead': True,
            'ProjectionExpression': '#fn, #ts',
            'ExpressionAttributeNames': {
                '#fn': 'filename',
                '#ts': 'tentative',
                '#data': 'data',
            },
            'FilterExpression': ('attribute_exists(#data)' if inline
                                 else 'attribute_not_exists(#data)'),
        }
        while True:
            response = self.dynamo.scan(**scan_args)
            for item in response['Items']:
                # in-progress uploads
                if 'tentative' in item:
                    continue
                name = item['filename']['S'].encode('utf-8')
                if pattern is None or fnmatch.fnmatch(name, pattern):
                    yield name
            if 'LastEvaluatedKey' not in response:
                break
            scan_args['ExclusiveStartKey'] = response['LastEvaluatedKey']

    def close(self):
        super().close()
