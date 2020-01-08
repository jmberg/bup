
from __future__ import absolute_import, print_function
import os
from contextlib import contextmanager

from wvpytest import *

from bup.storage import Kind, FileAlreadyExists, FileNotFound, get_storage
from bup.repo import ConfigRepo


try:
    # Allow testing this against any kind of storage backend
    # (for development) - note that you may have to clean up
    # data inside it after each run manually.
    repo_conf = os.environ['STORAGE_TEST_CONF']
except KeyError:
    repo_conf = None


class NoOpRepo(ConfigRepo):
    def finish_writing(self):
        pass
    def config_get(self, key, opttype=None):
        if key == b'bup.repo-id':
            return 'noop'
        return None

@contextmanager
def create_test_config(tmpdir):
    if repo_conf is None:
        cfgfile = os.path.join(tmpdir, b'repo.conf')
        cfg = open(cfgfile, 'wb')
        cfg.write(b'[bup]\n')
        cfg.write(b'  storage = File\n')
        cfg.write(b'  path = %s\n' % os.path.join(tmpdir, b'repo'))
        cfg.write(b'  cachedir = %s\n' % os.path.join(tmpdir, b'cache'))
        cfg.close()
        create = True
    else:
        wvstart("storage config from %s" % repo_conf)
        cfgfile = repo_conf
        create = False
    with NoOpRepo(cfg_file=cfgfile) as repo:
        store = get_storage(repo, create=create)
        yield store
    del store

def test_storage_config(tmpdir):
    with create_test_config(tmpdir) as store:
        wvstart("create a new file")
        wr = store.get_writer(b'test-storage-overwrite', Kind.CONFIG)
        wr.write(b'a' * 100)
        wr.close()

        wvstart("cannot overwrite it")
        wvexcept(FileAlreadyExists, store.get_writer,
                 b'test-storage-overwrite', Kind.CONFIG)

        wvstart("replace it atomically")
        rd = store.get_reader(b'test-storage-overwrite', Kind.CONFIG)
        wr = store.get_writer(b'test-storage-overwrite', Kind.CONFIG,
                              overwrite=rd)
        wr.write(b'b' * 100)
        wr.close()
        wr = store.get_writer(b'test-storage-overwrite', Kind.CONFIG,
                              overwrite=rd)
        wr.write(b'c' * 100)
        wr.abort()
        wvpasseq(rd.read(), b'a' * 100)
        rd.close()

        rd = store.get_reader(b'test-storage-overwrite', Kind.CONFIG)
        wvpasseq(rd.read(), b'b' * 100)
        wvstart("seek")
        wvpasseq(rd.read(), b'')
        rd.seek(0)
        wvpasseq(rd.read(), b'b' * 100)
        rd.seek(90)
        wvpasseq(rd.read(), b'b' * 10)
        rd.seek(90)
        wvpasseq(rd.read(10), b'b' * 10)
        rd.close()

        wvstart("not found")
        wvexcept(FileNotFound, store.get_reader, b'test-404', Kind.CONFIG)

def test_storage_packs(tmpdir):
    with create_test_config(tmpdir) as store:
        kinds = {
            Kind.METADATA: ("METADATA", b"mpack"),
            Kind.DATA: ("DATA", b"dpack"),
            Kind.IDX: ("IDX", b"idx"),
        }
        for kind, (kindname, ext) in kinds.items():
            wvstart("create a new file %s" % kindname)
            filename = b'pack-zzzzzzz.%s' % ext
            wr = store.get_writer(filename, kind)
            wr.write(b'a' * 100)
            wr.close()

            for nkind, (nkindname, _) in kinds.items():
                wvstart("cannot overwrite by %s" % nkindname)
                wvexcept(FileAlreadyExists, store.get_writer,
                         filename, kind)
                rd = store.get_reader(filename, kind)
                wvexcept(Exception, store.get_writer,
                         filename, kind, overwrite=rd)
                rd.close()

            wvstart("read back")
            rd = store.get_reader(filename, kind)
            wvpasseq(rd.read(), b'a' * 100)
            rd.close()
