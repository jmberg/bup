
from os import devnull, environb as environ, fsencode

import pytest

from bup.client import Config
from bup.repo import main_repo_location, repo_location_url
from bup.repo import parse_repo_url_arg, repo_for_url
from bup.url import URL


def test_repo_location_url():
    url = URL(scheme=b'ssh')
    assert repo_location_url(url) is url
    assert repo_location_url(Config(remote=b'h:x', url=url)) is url


def test_main_repo_location():
    class Died(Exception): pass
    def die(msg): raise Died(msg)
    def file(**kwargs): return URL(scheme=b'file', **kwargs)
    def ssh(**kwargs): return URL(scheme=b'ssh', **kwargs)
    def config(reverse, remote):
        orig_rev = environ.get(b'BUP_SERVER_REVERSE', None)
        try:
            if reverse is not None:
                environ[b'BUP_SERVER_REVERSE'] = reverse
            return main_repo_location(remote, die)
        finally:
            if orig_rev is None:
                environ.pop(b'BUP_SERVER_REVERSE', None)
            else:
                environ[b'BUP_SERVER_REVERSE'] = orig_rev

    assert config(None, None) == file(path=fsencode(devnull)) # conftest.py
    assert config(None, b'h:x') == Config(remote=b'h:x',
                                          url=ssh(host=b'h', path=b'x'))
    with pytest.raises(Exception, match='has no colon'):
        config(None, b'-')
    assert config(b'r', None) == URL(scheme=b'bup-rev', host=b'r')


def test_config_repo_dispatch(tmp_path):
    def die(msg): raise Exception(msg)
    def url_for(name):
        return parse_repo_url_arg('-d', b'config://' + fsencode(tmp_path / name), die)

    with pytest.raises(Exception, match='must set bup.type'):
        repo_for_url(url_for('none.conf'))
    (tmp_path / 'none.conf').write_bytes(b'[bup]\n\tfoo = x\n')
    with pytest.raises(Exception, match='must set bup.type'):
        repo_for_url(url_for('none.conf'))
    (tmp_path / 'bad.conf').write_bytes(b'[bup]\n\ttype = nosuch\n')
    with pytest.raises(Exception, match="Invalid repo type 'nosuch'"):
        repo_for_url(url_for('bad.conf'))
    (tmp_path / 'dots.conf').write_bytes(b'[bup]\n\ttype = ../x\n')
    with pytest.raises(Exception, match='invalid bup.type'):
        repo_for_url(url_for('dots.conf'))
    with pytest.raises(Exception, match='not supported for config'):
        repo_for_url(url_for('none.conf'), compression_level=1)
