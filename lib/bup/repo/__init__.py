
from importlib import import_module
from os import environb as environ
from typing import Callable, NoReturn

from bup import client, git
from bup.compat import argv_bytes
from bup.config import url_for_remote_opt
from bup.io import path_msg as pm
from bup.path import defaultrepo
from bup.repo.base import RepoProtocol
from bup.repo.local import LocalRepo
from bup.repo.remote import RemoteRepo
from bup.url import URL, parse_path_oriented_url


public_schemes = frozenset([b'file', b'ssh', b'bup', b'config'])


class ConfigRepo(RepoProtocol):
    """Base class for repositories whose access configuration is in a
    config:// file.  That file must set bup.type, which names a
    bup.repo.<type> module containing a <Type>Repo class derived from
    ConfigRepo, which is then constructed with the config file path.

    """
    def __init__(self, cfg_file):
        self.cfg_file = cfg_file

    def access_config_get(self, name, opttype=None):
        assert isinstance(name, bytes)
        return git.git_config_get(self.cfg_file, name, opttype=opttype)


def _make_config_repo(url, create=None, **kwargs):
    for arg, val in kwargs.items():
        if val is not None:
            raise Exception(f'{arg} is not supported for config:// repositories')
    if url.host or url.port is not None or url.user or not url.path:
        raise Exception('Must use "config:///path/to/file.conf"!')
    cfg_file = url.path
    repo_type = git.git_config_get(cfg_file, b'bup.type')
    if repo_type is None:
        raise Exception(f'{pm(cfg_file)} must set bup.type')
    repo_type = repo_type.decode('ascii')
    if '..' in repo_type:
        raise Exception(f'invalid bup.type {repo_type!r}')
    modname = f'bup.repo.{repo_type.lower()}'
    try:
        module = import_module(modname)
    except ModuleNotFoundError as e:
        if e.name != modname:
            raise
        raise Exception(f"Invalid repo type '{repo_type}'") from e
    cls = getattr(module, repo_type + 'Repo', None)
    if cls is None:
        raise Exception(f"Invalid repo type '{repo_type}'")
    ret = cls(cfg_file, create=create)
    assert isinstance(ret, ConfigRepo)
    return ret


def repo_location_url(location):
    if isinstance(location, URL):
        return location
    if isinstance(location, client.Config):
        return location.url
    raise Exception(f'unexpected location type {location}')


def main_repo_location(remote, die: Callable[[str], NoReturn]):
    """Return a repository location for a typical command.

    Return bup-rev URL if BUP_SERVER_REVERSE if set, a
    bup.client.Config for the remote (--remote) if any, or finally, a
    file URL for the default repository (e.g. BUP_DIR); die if both
    reverse and remote are requested, or if the remote is
    unrecognizable.

    """
    # pylint (3.3.4) ignores die NoReturn annotation
    def die_(msg) -> NoReturn: die(msg)
    reverse = environ.get(b'BUP_SERVER_REVERSE')
    if reverse:
        if remote:
            die_("don't use -r in reverse mode; it's automatic")
        return URL(scheme=b'bup-rev', host=reverse)
    if not remote:
        return URL(scheme=b'file', path=defaultrepo())
    url = url_for_remote_opt(remote)
    if isinstance(url, str):
        die_(url)
    if url.scheme == b'config':
        return url
    return client.Config(remote=remote, url=url)


def repo_for_url(url, **kwargs):
    if url.scheme == b'file':
        create_opts = kwargs.pop('create', None)
        if create_opts is not None:
            LocalRepo.create(url.path, **create_opts)
        return LocalRepo(repo_dir=url.path, **kwargs)
    if url.scheme in (b'ssh', b'bup', b'bup-rev'):
        return RemoteRepo(url, **kwargs)
    if url.scheme == b'config':
        return _make_config_repo(url, **kwargs)
    raise Exception(f'unrecognized repository URL {url}')


def repo_for_location(location, **kwargs):
    if isinstance(location, URL):
        return repo_for_url(location, **kwargs)
    if isinstance(location, client.Config):
        return RemoteRepo(location, **kwargs)
    raise Exception(f'unexpected repository location {location}')


def parse_repo_url_arg(arg, val, misuse):
    """Call misuse(err_msg) if val is not a valid URL, otherwise
    return a corresponding URL instance."""
    res = parse_path_oriented_url(val)
    if not res:
        misuse(f'invalid {arg} {pm(val)}')
    if isinstance(res, str):
        misuse(f'invalid {arg} {pm(val)} ({res})')
    url = repo_location_url(res)
    if url.scheme not in public_schemes:
        misuse(f'invalid {arg} schema in {pm(val)}')
    if url.scheme == b'file' and url.auth:
        misuse(f'{arg} {pm(val)} URL has extra leading slashes or an authority')
    return url
