
import os

from bup.compat import environ

# Eventually, if we physically move the source tree cmd/ to lib/, then
# we could use realpath here and save some stats...

fsencode = os.fsencode

_libdir = os.path.abspath(os.path.dirname(fsencode(__file__)) + b'/..')
_resdir = _libdir
_exedir = os.path.abspath(_libdir + b'/cmd')
_exe = os.path.join(_exedir, b'bup')


def exe():
    return _exe

def exedir():
    return _exedir

cmddir = exedir

def libdir():
    return _libdir

def resource_path(subdir=b''):
    return os.path.join(_resdir, subdir)

def defaultrepo():
    repo = environ.get(b'BUP_DIR')
    if repo:
        return repo
    return os.path.expanduser(b'~/.bup')

def xdg_cache():
    return environ.get(b'XDG_CACHE_HOME') or os.path.expanduser(b'~/.cache')

def index_cache(identifier):
    # If the REPO/index-cache exists, stick with it
    repo_cache = os.path.join(defaultrepo(), b'index-cache')
    if os.path.exists(repo_cache):
        return os.path.join(repo_cache, identifier)
    return os.path.join(xdg_cache(), b'bup', b'remote', identifier)

def default_fsindex():
    return os.path.join(defaultrepo(), b'bupindex')
