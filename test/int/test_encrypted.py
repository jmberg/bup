
import os
import struct
from contextlib import contextmanager
import pytest

from wvpytest import *

from bup import storage
from bup.storage import Kind
from bup.repo import ConfigRepo, encrypted


@contextmanager
def create_test_config(tmpdir):
    cfgfile = os.path.join(tmpdir, b'repo.conf')
    cfg = open(cfgfile, 'wb')
    cfg.write(b'[bup]\n')
    cfg.write(b'  storage = File\n')
    cfg.write(b'  path = %s\n' % os.path.join(tmpdir, b'repo'))
    cfg.write(b'  cachedir = %s\n' % os.path.join(tmpdir, b'cache'))
    cfg.close()
    class NoOpRepo(ConfigRepo):
        def finish_writing(self, run_midx=True):
            pass
        def config_get(self, key, opttype=None):
            if key == b'bup.repo-id':
                return 'noop'
            return False
    with NoOpRepo(cfg_file=cfgfile) as repo:
        yield storage.get_storage(repo, create=True)

def test_encrypted_container(tmpdir):
    libnacl = pytest.importorskip('libnacl')
    with create_test_config(tmpdir) as store:
        secret = libnacl.public.SecretKey()

        # the container wants a repo class, currently only to know the
        # blobbits (for reading optimisation, so not much relevant)
        class BlobBitsRepo:
            def config_get(self, name, opttype):
                assert name == b'bup.split.files'
                assert opttype == 'int'
                return 13
        repo = BlobBitsRepo()

        p = encrypted.EncryptedContainer(repo, store, b'test.pack', 'w', Kind.DATA,
                                         compression=9, key=secret.pk)
        p.finish()
        pfile = open(os.path.join(tmpdir, b'repo', b'test.pack'), 'rb')
        # minimum file size with header and footer
        wvpasseq(len(pfile.read()), 92)

        p = encrypted.EncryptedContainer(repo, store, b'test2.pack', 'w', Kind.DATA,
                                         compression=9, key=secret.pk)
        offsets = {}
        offsets[b'A'] = p.write(3, None, b'A'* 1000)
        offsets[b'B'] = p.write(3, None, b'B'* 1000)
        offsets[b'C'] = p.write(3, None, b'C'* 1000)
        offsets[b'D'] = p.write(3, None, b'D'* 1000)
        offsets[b'ABCD'] = p.write(3, None, b'ABCD'* 250)
        sk = p.box.sk
        p.finish()
        pfile = open(os.path.join(tmpdir, b'repo', b'test2.pack'), 'rb')
        pdata = pfile.read()
        # the simple stuff above compresses well
        wvpasseq(len(pdata), 265)

        # check header
        wvpasseq(struct.unpack('<4sBBH', pdata[:8]), (b'BUPe', 1, 0, 84))

        # check secret header
        eh = libnacl.sealed.SealedBox(secret).decrypt(pdata[8:84 + 8])
        wvpasseq(struct.unpack('<BBBB', eh[:4]), (1, 1, 1, 1))
        # ignore vuint_key here, it's random
        wvpasseq(sk, eh[4:])

        # read the objects and check if they're fine
        p = encrypted.EncryptedContainer(repo, store, b'test2.pack', 'r', Kind.DATA,
                                         key=secret)
        for k in sorted(offsets.keys()):
            wvpasseq(p.read(offsets[k]), (3, k * (1000 // len(k))))
        p.close()

        # this does some extra checks - do it explicitly
        store.close()

def test_basic_encrypted_repo(tmpdir):
    pytest.importorskip('libnacl')
    with create_test_config(tmpdir) as store:
        src = os.path.join(tmpdir, b'src')
        os.mkdir(src)

        for i in range(100):
            open(os.path.join(src, b'%d' % i), 'wb').write(b'%d' % i)
