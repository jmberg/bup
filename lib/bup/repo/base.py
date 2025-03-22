
import random
from binascii import hexlify, unhexlify

from bup import vfs, git
from bup.compat import pending_raise, bytes_from_byte
from bup.helpers import debug2
from bup import git


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
        self.closed = True
        self._required_config_types = {
            b'pack.compression': 'int',
            b'pack.packsizelimit': 'int',
            b'core.compression': 'int',
            b'bup.split.trees': 'bool',
            b'bup.split.files': 'int',
        }
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
        if max_pack_size is None:
            max_pack_size = self.config_get(b'pack.packSizeLimit',
                                            opttype='int')
        # if it's still None, use the lower level logic, which
        # (in the case of remote repo) might also read it from
        # the local (otherwise unused) repo's config
        self.max_pack_size = max_pack_size
        self.max_pack_objects = max_pack_objects
        self.dumb_server_mode = False
        self._ensure_repo_id()
        self.closed = False

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

    def rev_parse(self, committish):
        """Resolve the full hash for 'committish', if it exists.

        Should be roughly equivalent to 'git rev-parse'.

        Returns the hex value of the hash if it is found, None if 'committish' does
        not correspond to anything.
        """
        head = self.read_ref(committish)
        if head:
            debug2("resolved from ref: commit = %s\n" % hexlify(head).decode('ascii'))
            return head

        if len(committish) == 40:
            try:
                hash = unhexlify(committish)
            except TypeError:
                return None

            if self.exists(hash):
                return hash

        return None

    @notimplemented
    def config_get(self, name, opttype=None):
        """
        Return the configuration value of 'name', returning None if it doesn't
        exist. opttype indicates the type of option.
        """

    def config_check(self, name, value):
        """
        Check validity of the given config option (for bup), so that
        you can't write (using bup config) a setting that'll prevent
        using the repository.
        """
        name = name.lower()
        opttype = self._required_config_types.get(name, None)
        if opttype is None:
            return True
        return git.git_config_check(name, value, opttype)

    def register_config_types(self, new):
        """
        Subclasses call this to register their additional configuration
        value types that need to be checked.
        """
        assert not set(self._required_config_types.keys()).intersection(new.keys())
        self._required_config_types = self._required_config_types.copy()
        self._required_config_types.update(new)

    @notimplemented
    def config_write(self, name, value):
        """
        Write the given configuration name=value to the config file/store.
        When value is None, delete the given option.
        """

    @notimplemented
    def config_list(self, values=False):
        """
        Return a generator over (key, value) tuples (if 'values' is True)
        or just keys (if 'values' is False). The keys and values must be
        just bytes, not coerced to any type (unlike config_get.)
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
    def delete_ref(self, refname, oldval=None):
        """
        Delete the ref called 'refname', if 'oldval' is not None then it's
        the current value of the ref and the implementation shall atomically
        check against it while deleting.
        """

    def cat(self, ref, include_data=True):
        """
        If ref does not exist, yield (None, None, None).  Otherwise yield
        (oidx, type, size), and then all of the data associated with ref.

        If include_data is False, stop after the initial tuple.
        """
        oidx, typ, sz, data_iter = self.get(ref, include_data=include_data)
        yield oidx, typ, sz
        if include_data:
            yield from data_iter

    def get_data(self, ref, expected_type):
        oidx, typ, sz, data_it = self.get(ref)
        assert typ == expected_type
        data = b''.join(data_it)
        assert len(data) == sz
        return data

    @notimplemented
    def get(self, ref, *, include_size=True, include_data=True):
        """
        Return a tuple of (oid, type, size, data_iterator), where the
        size is None if include_size is False, and data_iterator is
        None when include_data is False.
        In addition to being True, include_data may be a tuple of object
        types to retrieve the data for.
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
    def just_write(self, oid, type, content, metadata=False):
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

    def packdir(self):
        """
        Implemented only by the LocalRepo(), returns the local pack dir
        where the git packs are stored.
        """
        raise Exception("Direct pack file access is not supported on this repository.")

    def walk_object(self, oidx, *, stop_at=None, include_data=None,
                    oid_exists=None):
        return git.walk_object(self, oidx, stop_at=stop_at,
                               include_data=include_data,
                               oid_exists=oid_exists)
