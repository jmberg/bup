
import sys

from bup import client, git, options
from bup.compat import argv_bytes
from bup.config import ConfigError
from bup.helpers import EXIT_FAILURE, log
from bup.io import byte_stream
from bup.repo import main_repo_location, repo_for_location
from bup.url import URL


optspec = """
bup config [-r <remote>] [-t <str|int|bool>] <name>
bup config [-r <remote>] <name> <value>
bup config [-r <remote>] --unset <name>
bup config [-r <remote>] --list-keys
bup config [-r <remote>] --list-with-value
--
r,remote=          remote repository path
t,type=            interpret the value as str, int, or bool (default str)
list-keys          list all keys instead of reading/setting
list-with-value    list keys and values
unset              remove the option <name>
"""

_TYPES = (None, 'str', 'int', 'bool')

def main(argv):
    o = options.Options(optspec)
    opt, _, extra = o.parse_bytes(argv[1:])

    if opt.type not in _TYPES:
        o.fatal(f"--type must be one of {', '.join(_TYPES)}")

    listing = opt.list_keys or opt.list_with_value
    if opt.list_keys and opt.list_with_value:
        o.fatal("--list-keys and --list-with-value are mutually exclusive")
    if listing and (extra or opt.unset or opt.type):
        o.fatal("name, value, --unset, and --type cannot be used with --list-keys/--list-with-value")
    if opt.unset and len(extra) != 1:
        o.fatal("--unset requires exactly one name")
    if not listing and not opt.unset and len(extra) not in (1, 2):
        o.fatal("must give a name, and optionally a value")
    if (len(extra) == 2 or opt.unset) and opt.type:
        o.fatal("--type must not be used when writing")

    loc = main_repo_location(argv_bytes(opt.remote) if opt.remote else None,
                             o.fatal)
    if isinstance(loc, URL) and loc.scheme == b'file':
        git.check_repo_or_die(loc.path)

    with repo_for_location(loc) as repo:
        try:
            _run(opt, extra, repo)
        except (ConfigError, git.GitError, PermissionError, client.ClientError) as ex:
            log(f'error: {ex}\n')
            sys.exit(EXIT_FAILURE)


def _run(opt, extra, repo):
    out = byte_stream(sys.stdout)

    if opt.list_keys:
        for k in repo.config_list():
            out.write(k + b'\n')
        return

    if opt.list_with_value:
        for k, v in repo.config_list(values=True):
            out.write(k + b'=' + v + b'\n')
        return

    name = argv_bytes(extra[0])

    if opt.unset:
        repo.config_write(name, None)
        return

    if len(extra) == 2:
        repo.config_write(name, argv_bytes(extra[1]))
        return

    opttype = None if opt.type == 'str' else opt.type
    v = repo.config_get(name, opttype=opttype)
    if v is None:
        sys.exit(1)
    if isinstance(v, bool):
        v = b'true' if v else b'false'
    elif isinstance(v, int):
        v = b'%d' % v
    out.write(name + b'=' + v + b'\n')
