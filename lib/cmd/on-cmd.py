#!/bin/sh
"""": # -*-python-*-
# https://sourceware.org/bugzilla/show_bug.cgi?id=26034
export "BUP_ARGV_0"="$0"
arg_i=1
for arg in "$@"; do
    export "BUP_ARGV_${arg_i}"="$arg"
    shift
    arg_i=$((arg_i + 1))
done
# Here to end of preamble replaced during install
bup_python="$(dirname "$0")/../../config/bin/python" || exit $?
exec "$bup_python" "$0"
"""
# end of bup preamble

from __future__ import absolute_import
from subprocess import PIPE
import getopt, os, signal, struct, subprocess, sys

sys.path[:0] = [os.path.dirname(os.path.realpath(__file__)) + '/..']

from bup import compat, options, ssh, path
from bup.compat import argv_bytes
from bup.helpers import DemuxConn, log
from bup.io import byte_stream


optspec = """
bup on <hostname> index ...
bup on <hostname> save ...
bup on <hostname> split ...
bup on <hostname> get ...
"""
o = options.Options(optspec, optfunc=getopt.getopt)
opt, flags, extra = o.parse(compat.argv[1:])
if len(extra) < 2:
    o.fatal('arguments expected')

class SigException(Exception):
    def __init__(self, signum):
        self.signum = signum
        Exception.__init__(self, 'signal %d received' % signum)
def handler(signum, frame):
    raise SigException(signum)

signal.signal(signal.SIGTERM, handler)
signal.signal(signal.SIGINT, handler)

sys.stdout.flush()
out = byte_stream(sys.stdout)

try:
    sp = None
    p = None
    ret = 99

    hp = argv_bytes(extra[0]).split(b':')
    if len(hp) == 1:
        (hostname, port) = (hp[0], None)
    else:
        (hostname, port) = hp
    argv = [argv_bytes(x) for x in extra[1:]]
    p = ssh.connect(hostname, port, b'on--server', stderr=PIPE)

    try:
        argvs = b'\0'.join([b'bup'] + argv)
        p.stdin.write(struct.pack('!I', len(argvs)) + argvs)
        p.stdin.flush()

        # for commands not listed here don't even execute the server
        # (e.g. bup on <host> index ...)
        # (FIXME: consider parsing get arguments?)
        read_append_commands = [b'get']
        append_commands = [b'save', b'split', b'tag']
        read_commands = [b'restore', b'web', b'fuse']
        # FIXME: is there any value in even allowing these?
        #        it's inefficient to make the roundtrip ...
        read_commands += [b'join', b'cat-file', b'ftp', b'ls',
                          b'margin', b'meta']

        mode = None
        if argv[0] in read_append_commands:
            mode = b'read-append'
        elif argv[0] in append_commands:
            mode = b'append'
        elif argv[0] in read_commands:
            mode = b'read'

        if mode is not None:
            # we already put BUP_DIR into the environment, which
            # is inherited here
            sp = subprocess.Popen([path.exe(), b'server', b'--force-repo',
                                   b'--mode=' + mode],
                                   stdin=p.stdout, stdout=p.stdin)
        p.stdin.close()
        p.stdout.close()
        # Demultiplex remote client's stderr (back to stdout/stderr).
        dmc = DemuxConn(p.stderr.fileno(), open(os.devnull, "wb"))
        for line in iter(dmc.readline, b''):
            out.write(line)
    finally:
        while 1:
            # if we get a signal while waiting, we have to keep waiting, just
            # in case our child doesn't die.
            try:
                ret = p.wait()
                if sp:
                    sp.wait()
                break
            except SigException as e:
                log('\nbup on: %s\n' % e)
                os.kill(p.pid, e.signum)
                ret = 84
except SigException as e:
    if ret == 0:
        ret = 99
    log('\nbup on: %s\n' % e)

sys.exit(ret)
