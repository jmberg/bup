
import os, subprocess
from os.path import realpath
from functools import partial
from binascii import hexlify

from bup import git
from bup.repo.base import BaseRepo


class LocalRepo(BaseRepo):
    def __init__(self, repo_dir=None, compression_level=None,
                 max_pack_size=None, max_pack_objects=None,
                 server=False):
        self.closed = True # until super().__init__()
        self._packwriter = None
        self.repo_dir = realpath(repo_dir or git.guess_repo())
        git.check_repo_or_die(repo_dir)
        self.config_write = partial(git.git_config_write, repo_dir=self.repo_dir)
        self.config_list = partial(git.git_config_list, repo_dir=self.repo_dir)
        super(LocalRepo, self).__init__(self.repo_dir,
                                        compression_level=compression_level,
                                        max_pack_size=max_pack_size,
                                        max_pack_objects=max_pack_objects)
        self._cp = git.cp(self.repo_dir)
        self.rev_list = partial(git.rev_list, repo_dir=self.repo_dir)
        if server and self.config_get(b'bup.dumb-server', opttype='bool'):
            # don't make midx files in dumb server mode
            self.objcache_maker = lambda : None
            self.run_midx = False
        else:
            self.objcache_maker = None
            self.run_midx = True

    @classmethod
    def create(self, repo_dir=None):
        # FIXME: this is not ideal, we should somehow
        # be able to call the constructor instead?
        git.init_repo(repo_dir)
        git.check_repo_or_die(repo_dir)
        # ensure it gets a repo-id
        with LocalRepo(repo_dir):
            pass

    def config_get(self, name, opttype=None):
        val = git.git_config_get(name, opttype=opttype, repo_dir=self.repo_dir)
        if val is None and name == b'bup.dumb-server':
            return os.path.exists(git.repo(b'bup-dumb-server',
                                           repo_dir=self.repo_dir))
        return val

    def list_indexes(self):
        for f in os.listdir(git.repo(b'objects/pack',
                                     repo_dir=self.repo_dir)):
            if f.endswith(b'.idx'):
                yield f

    def read_ref(self, refname):
        return git.read_ref(refname, repo_dir=self.repo_dir)

    def _ensure_packwriter(self):
        if not self._packwriter:
            self._packwriter = git.PackWriter(repo_dir=self.repo_dir,
                                              compression_level=self.compression_level,
                                              max_pack_size=self.max_pack_size,
                                              max_pack_objects=self.max_pack_objects,
                                              objcache_maker=self.objcache_maker,
                                              run_midx=self.run_midx)

    def update_ref(self, refname, newval, oldval):
        self.finish_writing()
        return git.update_ref(refname, newval, oldval, repo_dir=self.repo_dir)

    def delete_ref(self, refname, oldval=None):
        git.delete_ref(refname, hexlify(oldval) if oldval else None,
                       repo_dir=self.repo_dir)

    def get(self, ref, *, include_size=True, include_data=True):
        it = self._cp.get(ref, include_data=True if (include_data is True) else False)
        oidx, typ, size = next(it)
        if isinstance(include_data, tuple):
            for _ in it: assert False
            include_data = typ in include_data
            it = self._cp.get(ref, include_data=include_data)
            next(it)
        if include_data and not oidx:
            # there cannot be data if no object was found
            for _ in it: assert False
        if include_data:
            data_it = it
        else:
            data_it = None
            for _ in it: assert False
        if isinstance(include_data, tuple) and not typ in include_data:
            for _ in data_it: pass
            data_it = None
        return (oidx, typ,
                size if include_size else None,
                data_it)

    def refs(self, patterns=None, limit_to_heads=False, limit_to_tags=False):
        yield from git.list_refs(patterns=patterns,
                                 limit_to_heads=limit_to_heads,
                                 limit_to_tags=limit_to_tags,
                                 repo_dir=self.repo_dir)

    def send_index(self, name, conn, send_size):
        with git.open_idx(git.repo(b'objects/pack/%s' % name,
                                   repo_dir=self.repo_dir)) as idx:
            send_size(len(idx.map))
            conn.write(idx.map)

    def rev_list_raw(self, refs, fmt):
        """
        Yield chunks of data of the raw rev-list in git format.
        (optional, used only by bup server)
        """
        args = git.rev_list_invocation(refs, format=fmt)
        p = subprocess.Popen(args, env=git._gitenv(self.repo_dir),
                             stdout=subprocess.PIPE)
        while True:
            out = p.stdout.read(64 * 1024)
            if not out:
                break
            yield out
        rv = p.wait()  # not fatal
        if rv:
            raise git.GitError('git rev-list returned error %d' % rv)

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

    def just_write(self, sha, type, content, metadata=False):
        self._ensure_packwriter()
        return self._packwriter.just_write(sha, type, content)

    def exists(self, sha, want_source=False):
        self._ensure_packwriter()
        return self._packwriter.exists(sha, want_source=want_source)

    def finish_writing(self):
        if self._packwriter:
            w = self._packwriter
            self._packwriter = None
            return w.close()
        return None

    def abort_writing(self):
        if self._packwriter:
            self._packwriter.abort()

    def packdir(self):
        return git.repo(b'objects/pack', repo_dir=self.repo_dir)

    # Hack for 'bup gc' until we move more of that into repo
    def restart_cp(self):
        self._cp.restart()
