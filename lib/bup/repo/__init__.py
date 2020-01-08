
from __future__ import absolute_import

from importlib import import_module

from bup.repo import local, remote, base
from bup import git, client


LocalRepo = local.LocalRepo
RemoteRepo = remote.RemoteRepo


class ConfigRepo(base.BaseRepo):
    def __init__(self, cfg_file, create=False):
        self.cfg_file = cfg_file
        super(ConfigRepo, self).__init__(cfg_file)

    def config(self, k, opttype=None):
        assert isinstance(k, bytes)
        return git.git_config_get(k, cfg_file=self.cfg_file, opttype=opttype)

def _make_config_repo(host, port, path, create):
    if not (host is None and port is None and path is not None):
        raise Exception('Must use "config:///path/to/file.conf"!')

    # just instantiate it to get the config() method, instead of
    # open-coding it here
    class DummyRepo(ConfigRepo):
        def close(self):
            pass
    dummy = DummyRepo(path)

    repo_type = dummy.config(b'bup.type').decode('ascii')

    assert not '..' in repo_type

    cls = None
    try:
        module = import_module('bup.repo.%s' % repo_type.lower())
        clsname = repo_type + 'Repo'
        cls = getattr(module, clsname, None)
    except ImportError:
        pass
    if cls is None:
        raise Exception("Invalid repo type '%s'" % repo_type)
    ret = cls(path, create=create)
    assert isinstance(ret, ConfigRepo)
    return ret

def make_repo(address, create=False, compression_level=None,
              max_pack_size=None, max_pack_objects=None):
    protocol, host, port, dir = client.parse_remote(address)
    if protocol == b'config':
        assert compression_level is None, "command-line compression level not supported in this repo type"
        assert max_pack_size is None, "command-line max pack size not supported in this repo type"
        assert max_pack_objects is None, "command-line max pack objects not supported in this repo type"
        return _make_config_repo(host, port, dir, create)
    return RemoteRepo(address, create=create,
                      compression_level=compression_level,
                      max_pack_size=max_pack_size,
                      max_pack_objects=max_pack_objects)
