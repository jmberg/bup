
import os, re

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

def cachedir(forwhat):
    xdg_cache = environ.get(b'XDG_CACHE_HOME')
    if not xdg_cache:
        xdg_cache = os.path.expanduser(b'~/.cache')
    xdg = os.path.join(xdg_cache, b'bup', forwhat)

    # if already there, use it
    if os.path.exists(xdg):
        return xdg

    # but if not check old path
    legacy = os.path.join(defaultrepo(), forwhat)
    if os.path.exists(legacy):
        return legacy

    # and if that also doesn't exist use new path
    return xdg

def indexcache(forwhat):
    forwhat = re.sub(br'[^@\w]', b'_', forwhat)
    return os.path.join(cachedir(b'index-cache'), forwhat)

def index():
    return cachedir(b'bupindex')
