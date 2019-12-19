"""
Encrypted repository.

The encrypted repository stores all files in encrypted form in any
kind of bup.storage.BupStorage backend, we store the following
types of files
 * configuration files (bup.storage.Kind.CONFIG)
 * pack files (bup.storage.Kind.DATA & bup.storage.Kind.METADATA)
 * idx files (bup.storage.Kind.IDX)

We also cache the idx files locally in unencrypted form, so that we
can generate midx files or look up if an object already exists.


The encryption design should have the following properties:
 1) The data cannot be tampered with at rest without this being
    detected, at least at restore time.
 2) Random access to objects in the (data) packs must be possible.
 3) It should not be possible for an attacker to check if a given
    object (e.g. an image) is contained in a backup.
 4) It should be possible to configure truly append-only backups,
    i.e. backups that even the system making them cannot restore.
    (This serves to prevent a compromised system from accessing
    data that was previously backed up.)

This has the following implications:

 - CTR mode and similar are out, making block-based encryption
   more expensive (actually expand the block due to the MAC)
   (this follows from 1).
 - Either block-based or object-based encryption must be used
   for pack files (follows from 2).
 - From 3, it follows that
   - idx files must be encrypted (at least they store the sha1
     of smaller files), and
   - the block sizes must not be visible, so the sizes need to
     be encrypted as well (to prevent fingerprinting of the blob
     size sequence).
 - Public key encryption should be used for data (to enable 4)


So based on this, the design is the following:

Each repository has two keys:
 1) The (symmetric) 'repokey;, used to encrypt configuration and
    index data.
 2) The (asymmetric) data key (the public part is the 'writekey',
    the private part is the 'readkey')

To access the repository, at least the 'repokey' and one of the
other two keys must be available. Each key enables the following:
 - repokey
   - ref updates
   - idx file creation/reading
     (and consequently existence checks)
 - writekey
   - data pack creation
 - readkey
   - data access

There are different files stored in the repository:
 - refs
   An encrypted JSON-encoded file that contains the refs for the
   repository, stored as a single object in a container described
   below.
 - config
   An encrypted JSON-encoded file that contains the config for the
   repository, stored as a single object in a container described
   below.
 - pack-*.encpack
   Encrypted pack files (not in git format, see below) containing
   the actual data; note that the filename is randomly generated
   (not based on the content like in git).
 - pack-*.encidx
   Encrypted idx files corresponding to the pack-*.encpack files;
   their content is stored in a single object inside the container
   file (see below) and is (currently) just the git/bup compatible
   PackIdxV2.

Each file stored in the repository has the following format, all
values are stored in little endian:

| offsets | data
|  0 -  3 | magic 0x420x550x500x65 ("BUPe")
+
|  4      | header algorithm flags/identifier, currently only
|         | 1 - for libsodium sealed box (data files)
|         | 2 - for libsodium secret box (config/idx files)
+
|  5      | reserved, must be 0
+
|  6 -  7 | length of the encrypted header (EH)
+
|  8 -  H | (H := EH + 8)
|         | encrypted header, with the following format:
|         |
|         |  | 0      | header format
|         |  |        |  1 - this format
|         |  +
|         |  | 1      | data algorithm flags/identifier,
|         |  |        |  1 - for libsodium secret box and
|         |  |        |      libsodium::crypto_stream() for
|         |  |        |      the object size vuint
|         |  +
|         |  | 2      | file type:
|         |  |        |  1 - pack
|         |  |        |  2 - idx (V2)
|         |  |        |  3 - config
|         |  +
|         |  | 3      | compression type
|         |  |        |  0 - no compression
|         |  |        |  1 - zlib
|         |  |        |  2 - zstd
|         |  +
|         |  | 4 - EH | secret key for the remainder of the file
...
|         | encrypted objects

This construction is somewhat wasteful (in the data case) since the
libsodium sealed box already uses an ephemeral key for the encryption;
we could use it for all the data, however, it's only 8 bytes or so
and not worth the extra complexity.

The encrypted objects in the file are prefixed by their encrypted
size (encoded as a vuint before encryption, with a limit of 1 GiB).
The size vuint is currently encrypted using crypto_stream() with a
nonce derived from its position in the file ("0x80 0 ... 0 offset").
The object data is compressed and then prefixed by a type byte (in
git packs, the type encoded as the lowest 3 bits of the size vuint).
The result is then stored in a libsodium secret box, using a similar
nonce ("0 ... 0 offset", which is the same without the top bit set).
The secret box provides authentication, and the nonce construction
protects against an attacker reordering chunks in the file to affect
restored data.
"""

# TODO
#  * keep metadata packs locally (if split out? configurable?
#    encrypted?)
#  * repo config
#    - stored in repo
#    - symmetrically encrypted using repokey
#    - have a version number and reject != 1
#  * teach PackIdxList that there may be a limit on open files
#    (and mmap address space) or add multi-pack-index support?
#  * address TODOs below in the code

from __future__ import absolute_import
import os
import struct
import zlib
try:
    import zstandard as zstd
except ImportError:
    zstd = None
import json
import fnmatch
from io import BytesIO
from binascii import hexlify, unhexlify
from itertools import islice

try:
    import libnacl.secret
    import libnacl.sealed
except ImportError:
    libnacl = None

from bup import git, vfs
from bup.helpers import mkdirp, pending_raise
from bup.vint import read_vuint, pack
from bup.storage import get_storage, FileNotFound, Kind
from bup.compat import bytes_from_uint
from bup.repo import ConfigRepo
from bup import hashsplit
from bup.git import GitError


# 1 GiB is the most we're willing to store as a single object
# (this is after compression and encryption)
MAX_ENC_BLOB = 1024 * 1024 * 1024
MAX_ENC_BLOB_VUINT_LEN = len(pack('V', MAX_ENC_BLOB))

NONCE_DATA, NONCE_LEN = 0, 0x80

class EncryptedVuintReader:
    """
    Reader for encrypted vuint items.
    """
    def __init__(self, file, vuint_cs, szhint):
        self.file = file
        self.vuint_cs = tuple(x for x in vuint_cs)
        self.offs = 0
        self.szhint = szhint

    def read(self, sz):
        assert sz == 1
        v = self.file.read(1, szhint=self.szhint)[0]
        self.szhint -= 1
        ret = bytes_from_uint(v ^ self.vuint_cs[self.offs])
        self.offs += 1
        assert self.offs < len(self.vuint_cs)
        return ret

class NoneCompressor:
    """
    Compressor API class without real compression, to simplify code.
    """
    def compress(self, data):
        return data
    def flush(self):
        return b''

class EncryptedContainer(object):
    HEADER, OBJ = range(2)

    def __init__(self, repo, storage, name, mode, kind, compression=None,
                 key=None, idxwriter=None, overwrite=None, compressor='zlib'):
        self.file = None # for __del__ in case of exceptions
        assert mode in ('r', 'w')
        self.mode = mode
        self._make_compressor = None
        if mode == 'w':
            if compressor == 'zlib':
                assert compression in range(-1, 10)
                self._make_compressor = lambda size: zlib.compressobj(compression)
                compression_type = 1
            elif compressor == 'zstd':
                if zstd is None:
                    raise Exception("zstd compression requires the zstandard module")
                assert compression in range(-1, 23)
                self._make_compressor = lambda size: zstd.ZstdCompressor(compression).compressobj(size=size)
                compression_type = 2
            elif compressor == 'none':
                self._make_compressor = lambda size: NoneCompressor()
                compression_type = 0
            else:
                raise Exception('Unsupported compression algorithm %s' % compressor)
        self.idxwriter = idxwriter
        self._used_nonces = set()
        self.repo = repo
        self.overwrite = overwrite

        if kind in (Kind.DATA, Kind.METADATA):
            self.filetype = 1
            header_alg = 1
        elif kind == Kind.IDX:
            self.filetype = 2
            header_alg = 2
        elif kind == Kind.CONFIG:
            self.filetype = 3
            header_alg = 2
        else:
            assert False, 'Invalid kind %d' % kind
        if header_alg == 1:
            self.ehlen = 84
        elif header_alg == 2:
            self.ehlen = 76
        else:
            assert False
        self.headerlen = self.ehlen + 8 # hdrlen

        bb = self.repo.config_get(b'bup.blobbits', opttype='int')
        self._blobsize = 1 << (bb or hashsplit.BUP_BLOBBITS)

        if mode == 'r':
            self.file = storage.get_reader(name, kind)
            try:
                hdr = self.file.read(8, szhint=8 + self.ehlen)
                assert hdr[:4] == b'BUPe'
                enc, res, ehlen = struct.unpack('<BBH', hdr[4:])
                assert enc == header_alg
                assert res == 0
                assert ehlen == self.ehlen
                if header_alg == 1:
                    assert isinstance(key, libnacl.public.SecretKey)
                    hdrbox = libnacl.sealed.SealedBox(key)
                else:
                    assert key is not None
                    hdrbox = libnacl.secret.SecretBox(key)
                inner_hdr = hdrbox.decrypt(self.file.read(ehlen))
                del hdrbox
                (fmt, alg, tp, compr) = struct.unpack('<BBBB', inner_hdr[:4])
                assert fmt == 1
                assert alg == 1
                assert tp == self.filetype, "type %d doesn't match %d (%s)" % (tp, self.filetype, name)
                assert compr in (0, 1, 2)
                if compr == 0:
                    self._decompress = lambda data: data
                elif compr == 1:
                    self._decompress = zlib.decompress
                elif compr == 2:
                    if zstd is None:
                        raise Exception("zstd compression requires the zstandard module")
                    self._decompress = zstd.ZstdDecompressor().decompress
                self.box = libnacl.secret.SecretBox(inner_hdr[4:])
                self._check = None
                self.offset = self.headerlen
            except:
                self.close()
                raise
        else:
            assert key is not None
            self.file = storage.get_writer(name, kind,
                                           overwrite=overwrite.file if overwrite else None)
            try:
                self.box = libnacl.secret.SecretBox()
                inner_hdr = struct.pack('<BBBB', 1, 1, self.filetype, compression_type)
                inner_hdr += self.box.sk
                if header_alg == 1:
                    hdrbox = libnacl.sealed.SealedBox(key)
                else:
                    hdrbox = libnacl.secret.SecretBox(key)
                eh = hdrbox.encrypt(inner_hdr)
                assert len(eh) == self.ehlen
                del hdrbox
                hdr = b'BUPe'
                hdr += struct.pack('<BxH', header_alg, len(eh))
                hdr += eh
                self.offset = 0
                self._write(hdr, self.HEADER)
                assert self.offset == self.headerlen
            except:
                self.file.abort()
                raise

    def __enter__(self):
        # for now only supported that way
        assert self.mode == 'r'
        return self

    def __exit__(self, tp, value, traceback):
        with pending_raise(value, rethrow=False):
            self.close()

    def __del__(self):
        assert self.file is None
        assert self.overwrite is None

    def nonce(self, kind, write=True):
        assert kind in (NONCE_DATA, NONCE_LEN)
        nonce = struct.pack('>B15xQ', kind, self.offset)
        if write:
            # safety check for nonce reuse
            assert nonce not in self._used_nonces, "nonce reuse!"
            self._used_nonces.add(nonce)
        return nonce

    def _write(self, data, dtype, objtype=None):
        assert self.mode == 'w'
        if dtype == self.OBJ:
            objtypeb = struct.pack('B', objtype)
            z = self._make_compressor(len(objtypeb) + len(data))
            data = z.compress(objtypeb) + z.compress(data) + z.flush()
            data = self.box.encrypt(data, self.nonce(NONCE_DATA),
                                    pack_nonce=False)[1]
            assert len(data) <= MAX_ENC_BLOB
            vuint = pack('V', len(data))
            encvuint = libnacl.crypto_stream_xor(vuint, self.nonce(NONCE_LEN),
                                                 self.box.sk)
            data = encvuint + data
        self.file.write(data)
        retval = self.offset
        self.offset += len(data)
        return retval

    def write(self, objtype, sha, data):
        offs = self._write(data, self.OBJ, objtype)
        if self.idxwriter:
            # Set the crc to the objtype - we cannot copy any objects
            # from one pack file to another without decrypting anyway
            # as the encryption nonce is the file offset, and we have
            # authentication as part of the encryption... but it may
            # be useful to have the objtype in case we need to e.g.
            # attempt to recover all commits (if refs are lost) etc.
            self.idxwriter.add(sha, objtype, offs)
        return offs

    def finish(self):
        assert self.mode == 'w'
        self.file.close()
        self.file = None
        self._cleanup()

    @property
    def size(self):
        assert self.mode == 'w'
        if self.file:
            return self.offset + self.headerlen
        return self.offset

    def abort(self):
        assert self.mode == 'w'
        if self.file is not None:
            self.file.abort()
            self.file = None
            self._cleanup()

    def _cleanup(self):
        if self.mode == 'w':
            del self.box
        elif self.file is not None:
            self.file.close()
        self.file = None
        if self.overwrite is not None:
            self.overwrite.close()
            self.overwrite = None

    def read(self, offset=None):
        assert self.mode == 'r'
        self.offset = offset or self.headerlen
        self.file.seek(self.offset)
        vuint_cs = libnacl.crypto_stream(MAX_ENC_BLOB_VUINT_LEN,
                                         self.nonce(NONCE_LEN, write=False),
                                         self.box.sk)
        sz = read_vuint(EncryptedVuintReader(self.file, vuint_cs, self._blobsize))
        assert sz <= MAX_ENC_BLOB
        data = self.file.read(sz)
        assert len(data) == sz
        data = self.box.decrypt(data, self.nonce(NONCE_DATA, write=False))
        data = self._decompress(data)
        objtype = struct.unpack('B', data[:1])[0]
        return objtype, data[1:]

    def close(self):
        assert self.mode == 'r'
        if self.file is None:
            return
        self.file.close()
        self.file = None
        self._cleanup()


class EncryptedRepo(ConfigRepo):
    """
    Implement the Repo abstraction, but store the data in an encrypted fashion.
    """
    def __init__(self, cfg_file, create=False):
        self.storage = None
        self.data_writer = None
        self.data_fakesha = None
        self.meta_writer = None
        self.meta_fakesha = None
        self.cfg_file = cfg_file
        self.idxlist = None
        self.ec_cache = {}
        self._config_cache = None
        self._in_config_read = False
        self.closed = True

        if libnacl is None:
            raise Exception("Encrypted repositories require libnacl")

        self.cachedir = self.access_config_get(b'bup.cachedir', opttype='path')
        if self.cachedir is None:
            raise Exception("encrypted repositories need a 'cachedir'")
        if create:
            mkdirp(self.cachedir)
        if not os.path.isdir(self.cachedir):
            raise Exception("cachedir doesn't exist or isn't a directory - may have to init the repo?")

        self.readkey = None
        self.repokey = None
        self.writekey = None
        self.refsname = self.access_config_get(b'bup.refsname')
        if self.refsname is None:
            self.refsname = b'refs'
        readkey = self.access_config_get(b'bup.readkey')
        if readkey is not None:
            self.readkey = libnacl.public.SecretKey(unhexlify(readkey))
        repokey = self.access_config_get(b'bup.repokey')
        if repokey is not None:
            self.repokey = unhexlify(repokey)
        writekey = self.access_config_get(b'bup.writekey')
        if writekey is not None:
            self.writekey = unhexlify(writekey)
            if self.readkey is not None:
                assert self.writekey == self.readkey.pk
        else:
            assert self.readkey is not None, "at least one of 'readkey' or 'writekey' is required"
            self.writekey = self.readkey.pk

        self.storage = get_storage(self, create=create)

        compressalgo = self.config_get(b'bup.compressalgo')
        if compressalgo is None:
            self.compressor = 'zlib'
        else:
            self.compressor = compressalgo.decode('ascii')

        self.compression = -1 # default for now, we may write bup.repo-id
        super(EncryptedRepo, self).__init__(cfg_file, create)

        if self.max_pack_size is None:
            self.max_pack_size = 1000 * 1000 * 1000
        self.compression = self.compression_level
        if self.compression is None:
            self.compression = -1
        self.separatemeta = self.config_get(b'bup.separatemeta', opttype='bool')
        self.data_written_objs = set()
        if self.separatemeta:
            self.meta_written_objs = set()
        else:
            self.meta_written_objs = self.data_written_objs

        self._synchronize_idxes()
        self.idxlist = git.PackIdxList(self.cachedir)

    def _synchronize_idxes(self):
        changes = False
        local_idxes = set(fnmatch.filter(os.listdir(self.cachedir), b'*.idx'))
        for remote_idx in self.storage.list(b'*.encidx'):
            local_idx = remote_idx.replace(b'.encidx', b'.idx')
            if local_idx in local_idxes:
                local_idxes.remove(local_idx)
            else:
                with self._open_read(remote_idx, Kind.IDX) as ec, \
                     open(os.path.join(self.cachedir, local_idx), 'wb') as f:
                    f.write(ec.read()[1])
                changes = True
        for local_idx in local_idxes:
            changes = True
            os.unlink(os.path.join(self.cachedir, local_idx))

        if changes:
            git.auto_midx(self.cachedir)

    def _create_new_pack(self, kind):
        fakesha = libnacl.randombytes(20)
        hexsha = hexlify(fakesha)
        return fakesha, EncryptedContainer(self, self.storage,
                                           b'pack-%s.encpack' % hexsha, 'w',
                                           kind, self.compression,
                                           key=self.writekey,
                                           idxwriter=git.PackIdxV2Writer(),
                                           compressor=self.compressor)

    def _ensure_data_writer(self):
        if self.data_writer is not None and self.data_writer.size > self.max_pack_size:
            self._finish(self.data_writer, self.data_fakesha)
            if self.meta_writer == self.data_writer:
                self.meta_writer = None
            self.data_writer = None
        if self.data_writer is None:
            self.data_fakesha, self.data_writer = self._create_new_pack(Kind.DATA)

    def _ensure_meta_writer(self):
        if self.meta_writer is not None and self.meta_writer.size > self.max_pack_size:
            self._finish(self.meta_writer, self.meta_fakesha,
                         meta=(self.meta_writer != self.data_writer))
            if self.data_writer == self.meta_writer:
                self.data_writer = None
            self.meta_writer = None
        if self.meta_writer is None:
            if self.separatemeta:
                self.meta_fakesha, self.meta_writer = self._create_new_pack(Kind.METADATA)
            else:
                self._ensure_data_writer()
                self.meta_writer = self.data_writer
                self.meta_fakesha = self.data_fakesha

    def close(self):
        self.abort_writing()
        for ec in self.ec_cache.values():
            ec.close()
        self.ec_cache = {}
        if self.storage is not None:
            self.storage.close()
            self.storage = None
        if self.idxlist is not None:
            self.idxlist.close()
            self.idxlist = None
        super(EncryptedRepo, self).close()

    def _encode_refs(self, refs):
        ret = {}
        for k, v in refs.items():
            ret[hexlify(k).decode('ascii')] = v.decode('ascii')
        return ret

    def _decode_refs(self, encrefs):
        ret = {}
        for k, v in encrefs.items():
            ret[unhexlify(k)] = v.encode('ascii')
        return ret

    def _json_write(self, filename, reader, data):
        wfile = EncryptedContainer(self, self.storage, filename, 'w',
                                   Kind.CONFIG, self.compression,
                                   key=self.repokey,
                                   overwrite=reader,
                                   compressor=self.compressor)
        wfile.write(0, None, json.dumps(data).encode('utf-8'))
        wfile.finish()
        # now invalidate our read cache
        if filename in self.ec_cache:
            self.ec_cache[filename].close()
            del self.ec_cache[filename]

    def update_ref(self, refname, newval, oldval):
        self.finish_writing()
        reader, refs_data = self._json_read(self.refsname)
        refs = self._decode_refs(refs_data)
        if oldval:
            assert refs[refname] == hexlify(oldval)
        refs[refname] = hexlify(newval)
        refs = self._encode_refs(refs)
        self._json_write(self.refsname, reader, refs)

    def _read_config(self):
        self._in_config_read = True
        try:
            reader, self._config_cache = self._json_read(b'config')
        finally:
            self._in_config_read = False
        return reader

    def config_get(self, name, opttype=None):
        if self._in_config_read and name == b'bup.blobbits':
            return None
        name = name.decode('ascii')
        if not self._config_cache:
            reader = self._read_config()
            if reader:
                reader.close()
        val = self._config_cache.get(name, None)
        if val is None:
            return None
        val = val.encode('utf-8')
        if opttype == None:
            return val
        if opttype == 'int':
            return int(val)
        if opttype == 'bool':
            if not val:
                return None
            if val in (b'0', b'false'):
                return False
            if val in (b'1', b'true'):
                return True
            raise GitError(f'invalid boolean value {val}')
        raise GitError(f"unknown opttype={opttype}")

    def config_write(self, key, value):
        reader = self._read_config()
        key = key.decode('ascii')
        if isinstance(value, bytes):
            value = value.decode('utf-8')
        if value is None:
            del self._config_cache[key]
        else:
            self._config_cache[key] = str(value)
        self._json_write(b'config', reader, self._config_cache)

    def _open_read(self, name, kind, cache=False):
        try:
            return self.ec_cache[name]
        except KeyError:
            if kind in (Kind.IDX, Kind.CONFIG):
                key = self.repokey
            elif kind in (Kind.DATA, Kind.METADATA):
                key = self.readkey
            else:
                assert False
            result = EncryptedContainer(self, self.storage, name,
                                        'r', kind, key=key)
            if cache:
                self.ec_cache[name] = result
            return result

    def _json_read(self, filename):
        try:
            reffile = self._open_read(filename, Kind.CONFIG)
            try:
                data = reffile.read()[1]
                return reffile, json.loads(data.decode('utf-8'))
            except Exception as e:
                with pending_raise(e):
                    reffile.close()
                # not reached
                assert False
                return None, {}
        except FileNotFound:
            return None, {}

    def refs(self, patterns=None, limit_to_heads=False, limit_to_tags=False):
        reader, refs_data = self._json_read(self.refsname)
        refs = self._decode_refs(refs_data)
        if reader:
            reader.close()
        # git pattern matching (in show-ref) matches only full components
        # of the /-split ref, so split the patterns by / and then later ...
        if patterns:
            patterns = [p.split(b'/') for p in patterns]
        for ref, refval in refs.items():
            # we check if the found ref ends with any of the patterns
            # (after splitting by / as well, to match only full components)
            refpath = ref.split(b'/')
            if patterns:
                found = False
                for pattern in patterns:
                    if refpath[-len(pattern):] == pattern:
                        found = True
                        break
                if not found:
                    continue
            if limit_to_heads and not ref.startswith(b'refs/heads/'):
                continue
            if limit_to_tags and not ref.startswith(b'refs/tags/'):
                continue
            yield ref, unhexlify(refval)

    def read_ref(self, refname):
        refs = self.refs(patterns=[refname], limit_to_heads=True)
        # TODO: copied from git.read_ref()
        l = tuple(islice(refs, 2))
        if l:
            assert len(l) == 1
            return l[0][1]
        return None

    def rev_list(self, ref_or_refs, count=None, parse=None, format=None):
        # TODO: maybe we should refactor this to not have all of bup rely
        # on the git format ... it's ugly that we have to produce it here
        #
        # TODO: also, this is weird, I'm using existing bup functionality
        # to pretend I'm git, and then bup uses that again, really it stands
        # to reason that bup should do this itself without even *having* a
        # rev_list() method that calls out to git - and it'll probably be
        # faster too since we have the bloom/midx.
        assert count is None
        assert format in (b'%T %at', None)
        # TODO: ugh, this is a messy API ...
        if isinstance(ref_or_refs, str):
            ref = ref_or_refs
        else:
            assert len(ref_or_refs) == 1
            ref = ref_or_refs[0]
        while True:
            commit = git.parse_commit(git.get_cat_data(self.cat(ref), b'commit'))
            if format is None:
                yield ref
            else:
                if format == b'%T %at':
                    data = BytesIO(b'%s %d\n' % (commit.tree, commit.author_sec))
                yield (ref, parse(data))
            if not commit.parents:
                break
            ref = commit.parents[0]

    def is_remote(self):
        # return False so we don't have to implement resolve()
        return False

    def cat(self, ref):
        """If ref does not exist, yield (None, None, None).  Otherwise yield
        (oidx, type, size), and then all of the data associated with
        ref.

        """
        if len(ref) == 40 and all(x in b'0123456789abcdefABCDEF' for x in ref):
            oid = unhexlify(ref)
        else:
            oid = self.read_ref(ref)
            if oid is None:
                yield (None, None, None)
                return
        oidx = hexlify(oid)
        res = self.idxlist.exists(oid,
                                  want_source=True,
                                  want_offset=True)
        if res is None:
            yield (None, None, None)
            return
        where = res.pack
        offs = res.offset
        assert where.startswith(b'pack-') and where.endswith(b'.idx')
        where = where.replace(b'.idx', b'.encpack')
        # Kind.DATA / Kind.METADATA are equivalent here
        ec = self._open_read(where, Kind.DATA, cache=True)
        objtype, data = ec.read(offs)
        yield (oidx, git._typermap[objtype], len(data))
        yield data

    def join(self, ref):
        return vfs.join(self, ref)

    def _data_write(self, objtype, content):
        sha = git.calc_hash(git._typermap[objtype], content)
        if not self.exists(sha):
            self._ensure_data_writer()
            self.data_writer.write(objtype, sha, content)
            self.data_written_objs.add(sha)
        return sha

    def _meta_write(self, objtype, content):
        sha = git.calc_hash(git._typermap[objtype], content)
        if not self.exists(sha):
            self._ensure_meta_writer()
            self.meta_writer.write(objtype, sha, content)
            self.meta_written_objs.add(sha)
        return sha

    def write_commit(self, tree, parent,
                     author, adate_sec, adate_tz,
                     committer, cdate_sec, cdate_tz,
                     msg):
        content = git.create_commit_blob(tree, parent,
                                         author, adate_sec, adate_tz,
                                         committer, cdate_sec, cdate_tz,
                                         msg)
        return self._meta_write(1, content)

    def write_tree(self, shalist):
        content = git.tree_encode(shalist)
        return self._meta_write(2, content)

    def write_data(self, data):
        return self._data_write(3, data)

    def write_symlink(self, target):
        return self._meta_write(3, target)

    def write_bupm(self, data):
        return self._meta_write(3, data)

    def just_write(self, oid, type, content):
        # TODO: teach bup-get to differentiate the type of data/metadata
        return self._data_write(git._typemap[type], content)

    def exists(self, oid, want_source=False):
        if oid in self.data_written_objs:
            return True
        if self.separatemeta and oid in self.meta_written_objs:
            return True
        return self.idxlist.exists(oid, want_source=want_source)

    def _finish(self, writer, fakesha, meta=False):
        hexsha = hexlify(fakesha)
        idxname = os.path.join(self.cachedir, b'pack-%s.idx' % hexsha)
        writer.finish()
        writer.idxwriter.write(idxname, fakesha)
        encidx = EncryptedContainer(self, self.storage,
                                    b'pack-%s.encidx' % hexsha,
                                    'w', Kind.IDX, self.compression,
                                    key=self.repokey,
                                    compressor=self.compressor)
        with open(idxname, 'rb') as idxfile:
            encidx.write(0, None, idxfile.read())
        encidx.finish()

        # recreate bloom/midx if needed
        self.idxlist.close_temps()
        git.auto_midx(self.cachedir)
        self.idxlist.refresh()

        # and clear all the object lists in memory,
        # they're now in the (new) idxlist
        if meta:
            for obj in self.meta_written_objs:
                assert self.idxlist.exists(obj), "Object from mem cache lost!"
            self.meta_written_objs.clear()
        else:
            for obj in self.data_written_objs:
                assert self.idxlist.exists(obj), "Object from mem cache lost!"
            self.data_written_objs.clear()

    def finish_writing(self, run_midx=True):
        if self.meta_writer != self.data_writer and self.meta_writer is not None:
            self._finish(self.meta_writer, self.meta_fakesha, meta=True)
            self.meta_writer = None
        if self.data_writer is not None:
            self._finish(self.data_writer, self.data_fakesha)
            if self.meta_writer == self.data_writer:
                self.meta_writer = None
            self.data_writer = None
        if run_midx:
            git.auto_midx(self.cachedir)

    def abort_writing(self):
        if self.meta_writer != self.data_writer and self.meta_writer is not None:
            self.meta_writer.abort()
            self.meta_writer = None
        if self.data_writer is not None:
            self.data_writer.abort()
            if self.meta_writer == self.data_writer:
                self.meta_writer = None
            self.data_writer = None
