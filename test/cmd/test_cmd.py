
import os
import subprocess
from functools import partial
import re
from bup.compat import fsdecode


STATUS = re.compile(br'^!\s*(.*?)\s+(\S+)\s*$')

def _run(script):
    os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
    process = subprocess.Popen([script], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    stdout = process.communicate()[0]
    process.wait()
    print(fsdecode(stdout))
    assert process.returncode == 0
    lines = stdout.split(b'\n')
    for line in lines:
        m = STATUS.match(line)
        if m:
            name = m.group(1)
            status = m.group(2)
            assert status == b'ok', '%s failed' % name


_distcheck = os.environ.get('DISTCHECK', None) == '1'
_dir = os.path.dirname(__file__)
for f in sorted(os.listdir(_dir)):
    if not f.startswith('test-') or f.endswith('~'):
        continue
    script = os.path.join(_dir, f)
    tname = f.replace('.sh', '').replace('-', '_')
    if (tname == 'test_release_archive') != _distcheck:
        continue

    fn = partial(_run, script)
    fn.__name__ = tname
    globals()[fn.__name__] = fn
