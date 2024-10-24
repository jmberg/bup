
from bup.repo.base import BaseRepo
from bup import client


class RemoteRepo(BaseRepo):
    def __init__(self, address, create=False, compression_level=None,
                 max_pack_size=None, max_pack_objects=None):
        self.closed = True # in case Client instantiation fails
        self.client = client.Client(address, create=create)
        self.closed = False
        self.config_get = self.client.config_get
        # init the superclass only afterwards so it can access self.config_get()
        super(RemoteRepo, self).__init__(address,
                                         compression_level=compression_level,
                                         max_pack_size=max_pack_size,
                                         max_pack_objects=max_pack_objects)
        self.rev_list = self.client.rev_list
        self.list_indexes = self.client.list_indexes
        self.read_ref = self.client.read_ref
        self.send_index = self.client.send_index
        self.join = self.client.join
        self.refs = self.client.refs
        self.resolve = self.client.resolve
        self._packwriter = None

    def config_write(self, key, value):
        # ignore this for now, server has it by itself, or not
        # if/when we add actual config writing, try to pass it
        # through but handle calls from _ensure_repo_id() in a
        # graceful manner
        if key == b'bup.repo-id':
            pass
        assert False, "writing to remote config is not yet supported"

    def close(self):
        super(RemoteRepo, self).close()
        if self.client:
            self.client.close()
            self.client = None

    def update_ref(self, refname, newval, oldval):
        self.finish_writing()
        return self.client.update_ref(refname, newval, oldval)

    def _ensure_packwriter(self):
        if not self._packwriter:
            self._packwriter = self.client.new_packwriter(
                                    compression_level=self.compression_level,
                                    max_pack_size=self.max_pack_size,
                                    max_pack_objects=self.max_pack_objects)

    def is_remote(self):
        return True

    def cat(self, ref):
        # Yield all the data here so that we don't finish the
        # cat_batch iterator (triggering its cleanup) until all of the
        # data has been read.  Otherwise we'd be out of sync with the
        # server.
        items = self.client.cat_batch((ref,))
        oidx, typ, size, it = info = next(items)
        yield info[:-1]
        if oidx: yield from it
        assert not next(items, None)

    def write_commit(self, tree, parent,
                     author, adate_sec, adate_tz,
                     committer, cdate_sec, cdate_tz,
                     msg):
        self._ensure_packwriter()
        return self._packwriter.new_commit(tree, parent,
                                           author, adate_sec, adate_tz,
                                           committer, cdate_sec, cdate_tz,
                                           msg)

    def write_tree(self, shalist):
        self._ensure_packwriter()
        return self._packwriter.new_tree(shalist)

    def write_data(self, data):
        self._ensure_packwriter()
        return self._packwriter.new_blob(data)

    def just_write(self, sha, type, content):
        self._ensure_packwriter()
        return self._packwriter.just_write(sha, type, content)

    def exists(self, sha, want_source=False):
        self._ensure_packwriter()
        return self._packwriter.exists(sha, want_source=want_source)

    def finish_writing(self, run_midx=True):
        if self._packwriter:
            w = self._packwriter
            self._packwriter = None
            return w.close()
        return None

    def abort_writing(self):
        if self._packwriter:
            self._packwriter.abort()
