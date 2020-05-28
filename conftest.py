
import sys

sys.path[:0] = ['lib']

from os.path import basename
from traceback import extract_stack
import subprocess

import pytest

from bup import helpers

@pytest.fixture(autouse=True)
def no_lingering_errors():
    def fail_if_errors():
        if helpers.saved_errors:
            bt = extract_stack()
            src_file, src_line, src_func, src_txt = bt[-4]
            msg = 'saved_errors ' + repr(helpers.saved_errors)
            assert False, '%s:%-4d %s' % (basename(src_file),
                                          src_line, msg)

    fail_if_errors()
    helpers.clear_errors()
    yield None
    fail_if_errors()
    helpers.clear_errors()

@pytest.fixture()
def tmpdir(tmp_path):
    try:
        yield bytes(tmp_path)
    finally:
        subprocess.call([b'chmod', b'-R', b'u+rwX', bytes(tmp_path)])
