
from __future__ import absolute_import

import random
from binascii import hexlify, unhexlify

from bup import vfs, git
from bup.compat import pending_raise, bytes_from_byte
from bup.helpers import debug2


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
        raise NotImplementedError("%s::%s must be implemented" % (
                                    obj.__class__.__name__, fn.__name__))
    return newfn

class BaseRepo(object):
    def __init__(self, key, compression_level=None,
                 max_pack_size=None, max_pack_objects=None):
        self.closed = False
        self._cache_id = _repo_cache_id(key)
        if compression_level is None:
            compression_level = self.config(b'pack.compression',
                                            opttype='int')
        if compression_level is None:
            compression_level = self.config(b'core.compression',
                                            opttype='int')
        # if it's still None, use the built-in default in the
        # lower levels (which should be 1)
        self.compression_level = compression_level
        if max_pack_size is None:
            max_pack_size = self.config(b'pack.packSizeLimit',
                                        opttype='int')
        # if it's still None, use the lower level logic, which
        # (in the case of remote repo) might also read it from
        # the local (otherwise unused) repo's config
        self.max_pack_size = max_pack_size
        self.max_pack_objects = max_pack_objects
        self.ensure_repo_id()

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

    @property
    def cache_id(self):
        """Return an identifier that differs from any other repository that
        doesn't share the same repository-specific information
        (e.g. refs, tags, etc.)."""
        return self._cache_id

    def is_remote(self):
        return False

    def join(self, ref):
        return vfs.join(self, ref)

    def resolve(self, path, parent=None, want_meta=True, follow=True):
        ## FIXME: mode_only=?
        return vfs.resolve(self, path, parent=parent,
                           want_meta=want_meta, follow=follow)

    def ensure_repo_id(self):
        val = self.config(b'bup.repo-id')
        if val is not None:
            return
        # create lots of random bits ...
        randgen = random.SystemRandom()
        chars = b'abcdefghijklmnopqrstuvwxyz0123456789'
        new_id = b''.join(bytes_from_byte(randgen.choice(chars)) for x in range(31))
        self.write_repo_id(new_id)

    def rev_parse(self, committish):
        """Resolve the full hash for 'committish', if it exists.

        Should be roughly equivalent to 'git rev-parse'.

        Returns the hex value of the hash if it is found, None if 'committish' does
        not correspond to anything.
        """
        head = self.read_ref(committish)
        if head:
            debug2("resolved from ref: commit = %s\n" % hexlify(head))
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
    def config(self, name, opttype=None):
        """
        Return the configuration value of 'name', returning None if it doesn't
        exist. opttype may be 'int' or 'bool' to return the value per git's
        parsing of --int or --bool.
        """

    @notimplemented
    def write_repo_id(self, new_id):
        """
        Write the given 'new_id' to the configuration file to be retrieved
        later with the b'bup.repo-id' key.
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
        """

    @notimplemented
    def delete_ref(self, refname, oldval=None):
        """
        Delete the ref called 'refname', if 'oldval' is not None then it's
        the current value of the ref and the implementation shall atomically
        check against it while deleting.
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
    def rev_list_raw(self, refs, fmt):
        """
        Yield chunks of data of the raw rev-list in git format.
        (optional, used only by bup server)
        """

    def _maybe_write(self, objtype, contents, metadata):
        """
        Internal helper function for the below write implementations.
        """
        sha = git.calc_hash(objtype, contents)
        if not self.exists(sha):
            self.just_write(sha, objtype, contents, metadata)
        return sha

    def write_commit(self, tree, parent,
                     author, adate_sec, adate_tz,
                     committer, cdate_sec, cdate_tz,
                     msg):
        """
        Create and tentatively write a new commit object to the
        repository.  The date_sec values must be epoch-seconds,
        and if a tz is None, the local timezone is assumed.
        """
        content = git.create_commit_blob(tree, parent,
                                         author, adate_sec, adate_tz,
                                         committer, cdate_sec, cdate_tz,
                                         msg)
        return self._maybe_write(b'commit', content, True)

    def write_tree(self, shalist):
        """
        Tentatively write the given tree shalist into the repository.
        Return the new object's oid.
        """
        content = git.tree_encode(shalist)
        return self._maybe_write(b'tree', content, True)

    def write_data(self, data):
        """
        Tentatively write the given data blob into the repository.
        Return the new object's oid.
        """
        return self._maybe_write(b'blob', data, False)

    def write_symlink(self, target):
        """
        Tentatively write the given symlink target into the repository.
        Return the new object's oid.
        """
        return self._maybe_write(b'blob', target, True)

    def write_bupm(self, data):
        """
        Tentatively write the given bupm (fragment) into the repository.
        Return the new object's oid.
        """
        return self._maybe_write(b'blob', data, False)

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

    def walk_object(self, oidx, stop_at=None, include_data=None):
        return git.walk_object(self, oidx, stop_at, include_data)
