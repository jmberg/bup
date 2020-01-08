
import sys
from bup.repo import local, remote, base

from importlib import import_module

from bup import git, client
from bup.compat import pending_raise
from bup.compat import environ, argv_bytes
from bup.helpers import log, parse_num


LocalRepo = local.LocalRepo
RemoteRepo = remote.RemoteRepo


class ConfigRepo(base.BaseRepo):
    def __init__(self, cfg_file, create=False):
        self.cfg_file = cfg_file
        super().__init__(self)

    def access_config_get(self, k, opttype=None):
        assert isinstance(k, bytes)
        return git.git_config_get(k, cfg_file=self.cfg_file, opttype=opttype)

def _make_config_repo(host, port, path, create):
    if not (host is None and port is None and path is not None):
        raise Exception('Must use "config:///path/to/file.conf"!')

    repo_type = git.git_config_get(b'bup.type', cfg_file=path).decode('ascii')

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

def from_opts(opt, reverse=True):
    """
    Return a repo - understands:
     * the following optional options:
       - max-pack-size
       - max-pack-objects
       - compress
       - remote
     * the BUP_SERVER_REVERSE environment variable
    """
    git.check_repo_or_die()
    if reverse:
        is_reverse = environ.get(b'BUP_SERVER_REVERSE')
        if is_reverse and opt.remote:
            log("error: don't use -r in reverse mode; it's automatic")
            sys.exit(97)
    else:
        is_reverse = False

    try:
        compress = opt.compress
    except (KeyError, AttributeError):
        compress = None

    try:
        max_pack_size = parse_num(opt.max_pack_size) if opt.max_pack_size else None
    except (KeyError, AttributeError):
        max_pack_size = None

    try:
        max_pack_objects = parse_num(opt.max_pack_objects) if opt.max_pack_objects else None
    except (KeyError, AttributeError):
        max_pack_objects = None

    try:
        if opt.remote:
            return make_repo(argv_bytes(opt.remote), compression_level=compress,
                             max_pack_size=max_pack_size,
                             max_pack_objects=max_pack_objects)

        if is_reverse:
            return make_repo(b'bup-rev://%s' % is_reverse,
                             compression_level=compress,
                             max_pack_size=max_pack_size,
                             max_pack_objects=max_pack_objects)

        return LocalRepo(compression_level=compress,
                         max_pack_size=max_pack_size,
                         max_pack_objects=max_pack_objects)
    except client.ClientError as e:
        log('error: %s' % e)
        sys.exit(1)
