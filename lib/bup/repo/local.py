
from __future__ import absolute_import
import os, subprocess
from os.path import realpath
from functools import partial
from binascii import hexlify

from bup import git, vfs
from bup.repo.base import BaseRepo


class LocalRepo(BaseRepo):
    def __init__(self, repo_dir=None, compression_level=None,
                 max_pack_size=None, max_pack_objects=None,
                 objcache_maker=None):
        self._packwriter = None
        self.repo_dir = realpath(git.guess_repo(repo_dir))
        # init the superclass only afterwards so it can access self.config()
        git.check_repo_or_die(repo_dir)
        super(LocalRepo, self).__init__(self.repo_dir,
                                        compression_level=compression_level,
                                        max_pack_size=max_pack_size,
                                        max_pack_objects=max_pack_objects)
        self._cp = git.cp(self.repo_dir)
        self.rev_list = partial(git.rev_list, repo_dir=self.repo_dir)
        self._dumb_server_mode = None
        self.objcache_maker = objcache_maker

    def write_repo_id(self, new_id):
        git.git_config_write(b'bup.repo-id', new_id, repo_dir=self.repo_dir)

    @classmethod
    def create(self, repo_dir=None):
        # FIXME: this is not ideal, we should somehow
        # be able to call the constructor instead?
        git.init_repo(repo_dir)
        git.check_repo_or_die(repo_dir)
        # ensure it gets a repo-id
        LocalRepo(repo_dir)

    def config(self, name, opttype=None):
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
                                              objcache_maker=self.objcache_maker)

    def update_ref(self, refname, newval, oldval):
        self.finish_writing()
        return git.update_ref(refname, newval, oldval, repo_dir=self.repo_dir)

    def delete_ref(self, refname, oldval=None):
        git.delete_ref(refname, hexlify(oldval) if oldval else None,
                       repo_dir=self.repo_dir)

    def cat(self, ref):
        it = self._cp.get(ref)
        oidx, typ, size = info = next(it)
        yield info
        if oidx:
            for data in it:
                yield data
        assert not next(it, None)

    def refs(self, patterns=None, limit_to_heads=False, limit_to_tags=False):
        for ref in git.list_refs(patterns=patterns,
                                 limit_to_heads=limit_to_heads,
                                 limit_to_tags=limit_to_tags,
                                 repo_dir=self.repo_dir):
            yield ref

    def send_index(self, name, conn, send_size):
        data = git.open_idx(git.repo(b'objects/pack/%s' % name,
                                     repo_dir=self.repo_dir)).map
        send_size(len(data))
        conn.write(data)

    def rev_list_raw(self, refs, fmt):
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

    def finish_writing(self, run_midx=True):
        if self._packwriter:
            w = self._packwriter
            self._packwriter = None
            return w.close(run_midx=run_midx)

    def abort_writing(self):
        if self._packwriter:
            self._packwriter.abort()

    def packdir(self):
        return git.repo(b'objects/pack', repo_dir=self.repo_dir)
