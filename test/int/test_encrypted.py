import struct
from binascii import hexlify
from os import fsencode

import pytest

from wvpytest import *

from bup.repo import ConfigRepo, encrypted
from bup.repo.encrypted import EncryptedRepo
from bup.storage import Kind, get_storage

libnacl = pytest.importorskip('libnacl')


@pytest.fixture
def enc_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg'))
    pair = libnacl.public.SecretKey()
    box = libnacl.secret.SecretBox()
    cfg = tmp_path / 'enc.conf'
    cfg.write_bytes(b'[bup]\n'
                    b'\ttype = Encrypted\n'
                    b'\tstorage = File\n'
                    b'\tpath = %s\n'
                    b'\trepokey = %s\n'
                    b'\treadkey = %s\n'
                    b'\twritekey = %s\n'
                    b'[bup "repo"]\n'
                    b'\tid = testrepoid\n'
                    % (fsencode(tmp_path / 'repo'),
                       box.hex_sk(), pair.hex_sk(), pair.hex_pk()))
    return fsencode(cfg)


def test_encrypted_container(tmp_path):
    cfg = fsencode(tmp_path / 'store.conf')
    with open(cfg, 'wb') as f:
        f.write(b'[bup]\n\tstorage = File\n\tpath = %s\n'
                % fsencode(tmp_path / 'repo'))
    store = get_storage(ConfigRepo(cfg_file=cfg), create=True)
    secret = libnacl.public.SecretKey()

    # the container wants a repo class, currently only to know the
    # blobbits (for reading optimisation, so not much relevant)
    class BlobBitsRepo:
        # pylint: disable-next=unused-argument
        def config_get(self, name, *, opttype=None):
            assert name == b'bup.split.files'
            return b'legacy:13'
        _blobsize = 1 << 13
    repo = BlobBitsRepo()

    p = encrypted.EncryptedContainer(repo, store, b'test.pack', 'w', Kind.DATA,
                                     compression=9, key=secret.pk)
    p.finish()
    with open(tmp_path / 'repo' / 'test.pack', 'rb') as pfile:
        # minimum file size with header and footer
        wvpasseq(len(pfile.read()), 92)

    p = encrypted.EncryptedContainer(repo, store, b'test2.pack', 'w', Kind.DATA,
                                     compression=9, key=secret.pk)
    offsets = {}
    offsets[b'A'] = p.write(3, None, b'A' * 1000)
    offsets[b'B'] = p.write(3, None, b'B' * 1000)
    offsets[b'C'] = p.write(3, None, b'C' * 1000)
    offsets[b'D'] = p.write(3, None, b'D' * 1000)
    offsets[b'ABCD'] = p.write(3, None, b'ABCD' * 250)
    sk = p.box.sk
    p.finish()
    with open(tmp_path / 'repo' / 'test2.pack', 'rb') as pfile:
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

    store.close()


# pylint: disable-next=redefined-outer-name
def test_encrypted_repo_roundtrip(enc_cfg):
    repo = EncryptedRepo(enc_cfg, create={})
    content = b'hello encrypted world\n' * 10
    blob = repo.write_data(content)
    repo.finish_writing()
    wvpass(repo.exists(blob))
    repo.update_ref(b'refs/heads/main', blob, None)
    repo.close()

    # reopen from scratch, so nothing is served from memory
    repo = EncryptedRepo(enc_cfg)
    wvpasseq(repo.read_ref(b'refs/heads/main'), blob)
    oidx, _, size, it = repo.cat(hexlify(blob))
    wvpasseq(oidx, hexlify(blob))
    wvpasseq(size, len(content))
    wvpasseq(b''.join(it), content)
    repo.close()


# pylint: disable-next=redefined-outer-name
def test_encrypted_repo_deletes_ref(enc_cfg):
    repo = EncryptedRepo(enc_cfg, create={})
    blob = repo.write_data(b'x')
    repo.finish_writing()
    repo.update_ref(b'refs/heads/gone', blob, None)
    repo.delete_ref(b'refs/heads/gone', blob)
    repo.close()

    repo = EncryptedRepo(enc_cfg)
    wvpasseq(repo.read_ref(b'refs/heads/gone'), None)
    repo.close()
