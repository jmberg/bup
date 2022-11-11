from __future__ import absolute_import
from subprocess import PIPE
import getopt, os, signal, struct, subprocess, sys

from bup import options, ssh, path
from bup.compat import argv_bytes
from bup.helpers import DemuxConn, log
from bup.io import byte_stream


optspec = """
bup on <hostname> index ...
bup on <hostname> save ...
bup on <hostname> split ...
bup on <hostname> get ...
"""

def main(argv):
    o = options.Options(optspec, optfunc=getopt.getopt)
    opt, flags, extra = o.parse_bytes(argv[1:])
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
            cmdmodes =  {
                b'get': b'unrestricted',
                b'save': b'append',
                b'split': b'append',
                b'tag': b'append',
                b'join': b'read',
                b'cat-file': b'read',
                b'ftp': b'read',
                b'ls': b'read',
                b'margin': b'read',
                b'meta': b'read',
            }
            mode = cmdmodes.get(argv[0], None)

            if mode is not None:
                # we already put BUP_DIR into the environment, which
                # is inherited here
                sp = subprocess.Popen([path.exe(), b'server', b'--force-repo',
                                       b'--mode=' + mode],
                                       stdin=p.stdout, stdout=p.stdin)
            p.stdin.close()
            p.stdout.close()
            # Demultiplex remote client's stderr (back to stdout/stderr).
            with DemuxConn(p.stderr.fileno(), open(os.devnull, "wb")) as dmc:
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
