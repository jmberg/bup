
import sys
from bup.repo import local, remote

from bup import git, client
from bup.compat import environ, argv_bytes
from bup.helpers import log, parse_num


LocalRepo = local.LocalRepo
RemoteRepo = remote.RemoteRepo

def make_repo(address, create=False, compression_level=None,
              max_pack_size=None, max_pack_objects=None):
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
