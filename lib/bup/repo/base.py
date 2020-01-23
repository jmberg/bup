
import random

from bup import vfs
from bup.compat import pending_raise, bytes_from_byte


_next_repo_cache_id = 0
_repo_cache_ids = {}

def _repo_cache_id(key):
    global _next_repo_cache_id, _repo_cache_ids
    repo_cache_id = _repo_cache_ids.get(key)
    if repo_cache_id is not None:
        return repo_cache_id
    repo_cache_id = _next_repo_cache_id = _next_repo_cache_id + 1
    _repo_cache_ids[key] = repo_cache_id
    return repo_cache_id

def notimplemented(fn):
    def newfn(obj, *args, **kwargs):
        raise NotImplementedError(f'{obj.__class__.__name__}.{fn.__name__}')
    return newfn

class BaseRepo:
    def __init__(self, key, compression_level=None,
                 max_pack_size=None, max_pack_objects=None):
        self.closed = False
        self.vfs_cache_id = _repo_cache_id(key)
        if compression_level is None:
            compression_level = self.config_get(b'pack.compression',
                                                opttype='int')
        if compression_level is None:
            compression_level = self.config_get(b'core.compression',
                                                opttype='int')
        # if it's still None, use the built-in default in the
        # lower levels (which should be 1)
        self.compression_level = compression_level
        self.max_pack_size = max_pack_size
        self.max_pack_objects = max_pack_objects
        self.dumb_server_mode = False
        self._ensure_repo_id()

    def close(self):
        self.closed = True
        self.finish_writing()

    def __del__(self):
        assert self.closed

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        with pending_raise(value, rethrow=False):
            self.close()

    def is_remote(self):
        return False

    def join(self, ref):
        return vfs.join(self, ref)

    def resolve(self, path, parent=None, want_meta=True, follow=True):
        ## FIXME: mode_only=?
        return vfs.resolve(self, path, parent=parent,
                           want_meta=want_meta, follow=follow)

    def _ensure_repo_id(self):
        val = self.config_get(b'bup.repo-id')
        if val is not None:
            return
        # create lots of random bits ...
        randgen = random.SystemRandom()
        chars = b'abcdefghijklmnopqrstuvwxyz0123456789'
        new_id = b''.join(bytes_from_byte(randgen.choice(chars)) for x in range(31))
        self.config_write(b'bup.repo-id', new_id)

    @notimplemented
    def config_get(self, name, opttype=None):
        """
        Return the configuration value of 'name', returning None if it doesn't
        exist. opttype indicates the type of option.
        """

    @notimplemented
    def list_indexes(self):
        """
        List all indexes in this repository (optional, used only by bup server)
        """

    @notimplemented
    def read_ref(self, refname):
        """
        Read the ref called 'refname', return the oidx (hex oid)
        """

    @notimplemented
    def update_ref(self, refname, newval, oldval):
        """
        Update the ref called 'refname' from oldval (None if it previously
        didn't exist) to newval, atomically doing a check against oldval
        and updating to newval. Both oldval and newval are given as oidx
        (hex-encoded oid).
        Must also finish_writing() internally, so that all objects are
        committed before the ref is updated.
        """

    @notimplemented
    def cat(self, ref):
        """
        If ref does not exist, yield (None, None, None).  Otherwise yield
        (oidx, type, size), and then all of the data associated with ref.
        """

    @notimplemented
    def refs(self, patterns=None, limit_to_heads=False, limit_to_tags=False):
        """
        Yield the refs filtered according to the list of patterns,
        limit_to_heads ("refs/heads"), tags ("refs/tags/") or both.
        """

    @notimplemented
    def send_index(self, name, conn, send_size):
        """
        Read the given index (name), then call the send_size
        function with its size as the only argument, and write
        the index to the given conn using conn.write().
        (optional, used only by bup server)
        """

    @notimplemented
    def write_commit(self, tree, parent,
                     author, adate_sec, adate_tz,
                     committer, cdate_sec, cdate_tz,
                     msg):
        """
        Tentatively write a new commit with the given parameters. You may use
        git.create_commit_blob().
        """

    @notimplemented
    def write_tree(self, shalist):
        """
        Tentatively write a new tree object into the repository, given the
        shalist (a list or tuple of (mode, name, oid)). You can use the
        git.tree_encode() function to convert from shalist to raw format.
        Return the new object's oid.
        """

    @notimplemented
    def write_data(self, data):
        """
        Tentatively write the given data into the repository.
        Return the new object's oid.
        """

    def write_symlink(self, target):
        """
        Tentatively write the given symlink target into the repository.
        Return the new object's oid.
        """
        return self.write_data(target)

    def write_bupm(self, data):
        """
        Tentatively write the given bupm (fragment) into the repository.
        Return the new object's oid.
        """
        return self.write_data(data)

    @notimplemented
    def just_write(self, oid, type, content):
        """
        TODO
        """

    @notimplemented
    def finish_writing(self, run_midx=True):
        """
        Finish writing, i.e. really add the previously tentatively written
        objects to the repository.
        TODO: document run_midx
        """

    @notimplemented
    def abort_writing(self):
        """
        Abort writing and delete all the previously tenatively written objects.
        """

    @notimplemented
    def exists(self, oid, want_source=False):
        """
        Check if the given oid (binary format) already exists in the
        repository (or the tentatively written objects), returning
        None if not, True if it exists, or the idx name if want_source
        is True and it exists.
        """
