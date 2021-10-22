
import sys, re
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
    protocol, host, port, dir = parse_remote(address)
    if protocol == b'config':
        assert compression_level is None, "command-line compression level not supported in this repo type"
        assert max_pack_size is None, "command-line max pack size not supported in this repo type"
        assert max_pack_objects is None, "command-line max pack objects not supported in this repo type"
        return _make_config_repo(host, port, dir, create)
    return RemoteRepo(address, create=create,
                      compression_level=compression_level,
                      max_pack_size=max_pack_size,
                      max_pack_objects=max_pack_objects)


_protocol_rs = br'([-a-z]+)://'
_host_rs = br'(?P<sb>\[)?((?(sb)[0-9a-f:]+|[^:/]+))(?(sb)\])'
_port_rs = br'(?::(\d+))?'
_path_rs = br'(/.*)?'
_url_rx = re.compile(br'%s(?:%s%s)?%s' % (_protocol_rs, _host_rs, _port_rs, _path_rs),
                     re.I)

class ParseError(Exception):
    pass

def parse_remote(remote):
    assert remote is not None
    url_match = _url_rx.match(remote)
    if url_match:
        # Backward compatibility: version of bup prior to this patch
        # passed "hostname:" to parse_remote, which wasn't url_match
        # and thus went into the else, where the ssh version was then
        # returned, and thus the dir (last component) was the empty
        # string instead of None from the regex.
        # This empty string was then put into the name of the index-
        # cache directory, so we need to preserve that to avoid the
        # index-cache being in a different location after updates.
        if url_match.group(1) == b'bup-rev':
            if url_match.group(5) is None:
                return url_match.group(1, 3, 4) + (b'', )
        elif not url_match.group(1) in (b'ssh', b'bup', b'file', b'config'):
            raise ParseError('unexpected protocol: %s'
                             % url_match.group(1).decode('ascii'))
        return url_match.group(1,3,4,5)
    else:
        rs = remote.split(b':', 1)
        if len(rs) == 1 or rs[0] in (b'', b'-'):
            return b'file', None, None, rs[-1]
        else:
            return b'ssh', rs[0], None, rs[1]


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
    except ParseError as e:
        log('error: %s' % e)
        sys.exit(1)
