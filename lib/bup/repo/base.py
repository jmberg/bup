
import re
from binascii import unhexlify

from bup import git, hashsplit
from bup.compat import dataclass
from bup.config import ConfigError
from bup.helpers import notimplemented
from bup.io import path_msg


def _make_base(config_get, compression_level, max_pack_size, max_pack_objects):

    @dataclass(slots=True, frozen=True)
    class Base():
        """Components shared by all repos."""
        compression_level: int
        max_pack_size: int
        max_pack_objects: int

    if compression_level is None:
        compression_level = config_get(b'pack.compression', opttype='int')
    if compression_level is None:
        compression_level = config_get(b'core.compression', opttype='int')
    # If it's still None, the lower levels should choose 1
    return Base(compression_level=compression_level,
                max_pack_objects=max_pack_objects,
                max_pack_size=max_pack_size or config_get(b'pack.packSizeLimit',
                                                          opttype='int'))

class RepoProtocol:
    # Specification only; intentially has no implementations
    # Subclassing just indicates intent.
    # Currently, the vfs relies on id(repo) for caching paths.

    # Size in bytes of the binary oids in this repository (20 or 32)
    hashsize: int

    @notimplemented
    def is_remote(self):
        """Return true if this is a "remote" repository."""

    @notimplemented
    def join(self, ref):
        """..."""

    @notimplemented
    def resolve(self, path, parent=None, want_meta=True, follow=True):
        """..."""

    @notimplemented
    def config_get(self, name, *, opttype=None):
        """
        Return the configuration value of 'name', returning None if it doesn't
        exist. opttype indicates the type of option.
        """

    @notimplemented
    def _config_write(self, name, value):
        """
        Set the configuration option 'name' to 'value', or unset
        the option if the value is None. Raise ConfigError if
        the value is not valid (for the option).

        This is the internal version that implementations provide,
        the user-facing config_write() already checks some values.
        """

    def config_write(self, name, value):
        _config_opt_types = {
            b'pack.compression': 'int',
            b'pack.packsizelimit': 'int',
            b'core.compression': 'int',
            b'bup.split.trees': 'bool',
        }

        if value is None:
            # allowed to unset all of the above and bup.split.files
            valid = True
        elif name.lower() == b'bup.split.files':
            valid = re.fullmatch(hashsplit._method_rx, value) is not None
        else:
            valid = git.git_config_check(name, value,
                                         _config_opt_types.get(name.lower()))
        if not valid:
            raise ConfigError(f'invalid value {path_msg(value)} for {path_msg(name)}')
        return self._config_write(name, value)


    @notimplemented
    def config_list(self, *, values=False):
        """
        Return an iterable over the configuration keys,
        or (key, value)pairs if values is true, as bytes.
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
        Delete the ref called 'refname'. If oldval is not None, atomically
        check and delete it only if it currently matches that value.
        """

    def rev_parse(self, committish):
        """Return the binary oid for 'committish' (a ref name or a
        full hex oid), returning None if it doesn't exist.

        Roughly equivalent to 'git rev-parse'.
        """
        head = self.read_ref(committish)
        if head:
            return head

        if len(committish) == 2 * self.hashsize:
            try:
                oid = unhexlify(committish)
            except TypeError:
                return None
            # Use cat() because exists() uses the local cache only.
            oidx, _, _, data = self.cat(committish)
            if oidx:
                for _ in data: pass
                return oid

        return None

    @notimplemented
    def cat(self, ref):
        """Return (oidx, type, size, data_iterator).  When the ref is
        missing, all elements will be None.  For some repositories
        (like RemoteRepo), the iterator must be consumed before
        calling other repository methods.

        """

    @notimplemented
    def rev_list(self, ref_or_refs, parse=None, format=None):
        """
        Yield information about commits as per "git rev-list".  If a format
        is not provided, yield one hex hash at a time.  If a format is
        provided, pass it to rev-list and call parse(git_stdout) for each
        commit with the stream positioned just after the rev-list "commit
        HASH" header line.  When a format is provided yield (oidx,
        parse(git_stdout)) for each commit.
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

    @notimplemented
    def write_symlink(self, target):
        """
        Tentatively write the given symlink target into the repository.
        Return the new object's oid.
        """

    @notimplemented
    def write_bupm(self, data):
        """
        Tentatively write the given bupm (fragment) into the repository.
        Return the new object's oid.
        """

    @notimplemented
    def just_write(self, oid, type, content):
        """
        TODO
        """

    @notimplemented
    def finish_writing(self):
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

    @notimplemented
    def packdir(self):
        """
        Return the local pack directory where the git packs are stored.
        Only supported by LocalRepo.
        """
