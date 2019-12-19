
import sys

from bup.helpers import make_repo_id


def main(argv):
    assert len(argv) == 1, "no arguments supported"
    try:
        # pylint: disable=import-outside-toplevel
        import libnacl.public
        import libnacl.secret
    except ImportError:
        print("libnacl not found, cannot generate keys")
        sys.exit(2)

    pair = libnacl.public.SecretKey()
    box = libnacl.secret.SecretBox()

    print('[bup]')
    print('  type = Encrypted')
    print('  repokey = ' + box.hex_sk().decode('ascii'))
    print('  readkey = ' + pair.hex_sk().decode('ascii'))
    print('  writekey = ' + pair.hex_pk().decode('ascii'))
    print('[bup "repo"]')
    print('  id = ' + make_repo_id().decode('ascii'))
