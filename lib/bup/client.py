
from binascii import hexlify, unhexlify
from contextlib import closing
import os, re, struct, sys, time, zlib
import socket

from bup import git, ssh, vfs, vint, protocol, path
from bup.git import PackWriter
from bup.helpers import \
    (Conn,
     ExitStack,
     atomically_replaced_file,
     chunkyreader,
     debug1,
     debug2,
     finalized,
     linereader,
     lines_until_sentinel,
     mkdirp,
     nullcontext_if_not,
     progress,
     qprogress,
     DemuxConn)
from bup.io import path_msg
from bup.vint import read_vint, read_vuint, read_bvec, write_bvec


bwlimit = None


class ClientError(Exception):
    pass


def _raw_write_bwlimit(f, buf, bwcount, bwtime):
    if not bwlimit:
        f.write(buf)
        return (len(buf), time.time())
    else:
        # We want to write in reasonably large blocks, but not so large that
        # they're likely to overflow a router's queue.  So our bwlimit timing
        # has to be pretty granular.  Also, if it takes too long from one
        # transmit to the next, we can't just make up for lost time to bring
        # the average back up to bwlimit - that will risk overflowing the
        # outbound queue, which defeats the purpose.  So if we fall behind
        # by more than one block delay, we shouldn't ever try to catch up.
        for i in range(0,len(buf),4096):
            now = time.time()
            next = max(now, bwtime + 1.0*bwcount/bwlimit)
            time.sleep(next-now)
            sub = buf[i:i+4096]
            f.write(sub)
            bwcount = len(sub)  # might be less than 4096
            bwtime = next
        return (bwcount, bwtime)


_protocol_rs = br'([-a-z]+)://'
_host_rs = br'(?P<sb>\[)?((?(sb)[0-9a-f:]+|[^:/]+))(?(sb)\])'
_port_rs = br'(?::(\d+))?'
_path_rs = br'(/.*)?'
_url_rx = re.compile(br'%s(?:%s%s)?%s' % (_protocol_rs, _host_rs, _port_rs, _path_rs),
                     re.I)

def parse_remote(remote):
    assert remote is not None
    url_match = _url_rx.match(remote)
    if url_match:
        # Backward compatibility: version of bup prior to this patch
        # passed "hostname:" to parse_remote, which wasn't url_match
        # and thus went into the else, where the ssh version was then
        # returned, and thus the dir (last component) was the empty
        # string instead of None from the regex.
        # This empty string was then put into the name of the index-
        # cache directory, so we need to preserve that to avoid the
        # index-cache being in a different location after updates.
        if url_match.group(1) == b'bup-rev':
            if url_match.group(5) is None:
                return url_match.group(1, 3, 4) + (b'', )
        elif not url_match.group(1) in (b'ssh', b'bup', b'file'):
            raise ClientError('unexpected protocol: %s'
                              % url_match.group(1).decode('ascii'))
        return url_match.group(1,3,4,5)
    else:
        rs = remote.split(b':', 1)
        if len(rs) == 1 or rs[0] in (b'', b'-'):
            return b'file', None, None, rs[-1]
        else:
            return b'ssh', rs[0], None, rs[1]


class Client:

    class ViaBupRev:
        def __init__(self):
            # only hand over to __del__ -> close() if complete, which
            # means it's fine to initialize attrs incrementally.
            self._closed = True
            self._out = os.fdopen(3, 'rb')
            self._in = os.fdopen(4, 'wb')
            self.conn = Conn(self._out, self._in)
            self.check_ok = self.conn.check_ok
            sys.stdin.close()
            self._closed = False
        def __enter__(self): return self
        def __del__(self): assert self._closed
        def __exit__(self, type, value, traceback): self.close()
        def close(self):
            if self._closed:
                return
            self._closed = True
            with closing(self._out), \
                 closing(self.conn), \
                 closing(self._in):
                pass

    class ViaSsh:
        def __init__(self, host, port):
            # only hand over to __del__ -> close() if complete, which
            # means it's fine to initialize attrs incrementally.
            self._closed = True
            try:
                # FIXME: ssh and file (ViaBup) shouldn't use the same module
                self._proc = ssh.connect(host, port, b'server')
            except OSError as e:
                raise ClientError('connect: %s' % e) from e
            self.conn = Conn(self._proc.stdout, self._proc.stdin)
            self._closed = False
        def __enter__(self): return self
        def __del__(self): assert self._closed
        def __exit__(self, type, value, traceback): self.close()
        def close(self):
            if self._closed:
                return
            self._closed = True
            def await_ssh(p):
                rc = self._proc.wait()
                if rc:
                    raise ClientError(f'server tunnel returned exit code {rc}')
            with finalized(self._proc, await_ssh), \
                 closing(self._proc.stdout), \
                 closing(self.conn), \
                 closing(self._proc.stdin):
                pass
        def check_ok(self):
            rv = self._proc.poll()
            if rv != None:
                raise ClientError(f'server exited unexpectedly with code {rv}')
            try:
                return self.conn.check_ok()
            except Exception as e:
                raise ClientError(e) from e

    class ViaBup:
        def __init__(self, host, port):
            # only hand over to __del__ -> close() if complete, which
            # means it's fine to initialize attrs incrementally.
            self._closed = True
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((host, 1982 if port is None else int(port)))
            self._sockw = self._sock.makefile('wb')
            self.conn = DemuxConn(self._sock.fileno(), self._sockw)
            self.check_ok = self.conn.check_ok
            self._closed = False
        def __enter__(self): return self
        def __del__(self): assert self._closed
        def __exit__(self, type, value, traceback): self.close()
        def close(self):
            if self._closed:
                return
            self._closed = True
            with closing(self._sock), \
                 closing(self.conn), \
                 finalized(lambda _: self.sock.shutdown(socket.SHUT_WR)), \
                 closing(self._sockw), \
                 closing(self._pin):
                pass

    def __init__(self, remote, create=False):
        # only hand over to __del__ -> close() if complete, which
        # means it's fine to initialize attrs incrementally.
        self.closed = True
        with ExitStack() as ctx:
            self.protocol, self.host, self.port, self.dir = parse_remote(remote)
            # The b'None' here matches python2's behavior of b'%s' % None == 'None',
            # python3 will (as of version 3.7.5) do the same for str ('%s' % None),
            # but crashes instead when doing b'%s' % None.
            cachehost = b'None' if self.host is None else self.host
            cachedir = b'None' if self.dir is None else self.dir
            self.cachedir = path.indexcache(re.sub(br'[^@\w]',
                                                   b'_',
                                                   b'%s:%s' % (cachehost, cachedir)))
            self._busy = None
            if self.protocol == b'bup-rev':
                self._transport = Client.ViaBupRev()
            elif self.protocol in (b'ssh', b'file'):
                self._transport = Client.ViaSsh(self.host, self.port)
            elif self.protocol == b'bup':
                self._transport = Client.ViaBup(self.host, self.port)
            ctx.enter_context(self._transport)
            self.conn = self._transport.conn
            self._available_commands = self._get_available_commands()
            self._require_command(b'init-dir')
            self._require_command(b'set-dir')
            if self.dir:
                self.dir = re.sub(br'[\r\n]', b' ', self.dir)
                if create:
                    self.conn.write(b'init-dir %s\n' % self.dir)
                else:
                    self.conn.write(b'set-dir %s\n' % self.dir)
                self.check_ok()
            self.sync_indexes()
            ctx.pop_all()
        self.closed = False

    def __enter__(self): return self
    def __del__(self): assert self.closed
    def __exit__(self, type, value, traceback): self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if not self._busy:
            self.conn.write(b'quit\n')
        with closing(self._transport):
            self._transport = None # not necessary

    def check_ok(self): return self._transport.check_ok()

    def check_busy(self):
        if self._busy:
            raise ClientError('already busy with command %r' % self._busy)

    def ensure_busy(self):
        if not self._busy:
            raise ClientError('expected to be busy, but not busy?!')

    def _not_busy(self):
        self._busy = None

    def _get_available_commands(self):
        self.check_busy()
        self._busy = b'help'
        conn = self.conn
        conn.write(b'help\n')
        result = set()
        line = self.conn.readline()
        if not line == b'Commands:\n':
            raise ClientError('unexpected help header ' + repr(line))
        while True:
            line = self.conn.readline()
            if line == b'\n':
                break
            if not line.startswith(b'    '):
                raise ClientError('unexpected help line ' + repr(line))
            cmd = line.strip()
            if not cmd:
                raise ClientError('unexpected help line ' + repr(line))
            result.add(cmd)
        # FIXME: confusing
        not_ok = self.check_ok()
        if not_ok:
            raise not_ok
        self._not_busy()
        return frozenset(result)

    def _require_command(self, name):
        if name not in self._available_commands:
            raise ClientError('server does not appear to provide %s command'
                              % name.decode('ascii'))

    def _list_indexes(self):
        self._require_command(b'list-indexes')
        self.check_busy()
        self.conn.write(b'list-indexes\n')
        for line in linereader(self.conn):
            if not line:
                break
            assert(line.find(b'/') < 0)
            parts = line.split(b' ')
            idx = parts[0]
            load = len(parts) == 2 and parts[1] == b'load'
            yield idx, load
        self.check_ok()

    def list_indexes(self):
        for idx, load in self._list_indexes():
            yield idx

    def sync_indexes(self):
        conn = self.conn
        mkdirp(self.cachedir)
        # All cached idxs are extra until proven otherwise
        extra = set()
        for f in os.listdir(self.cachedir):
            debug1(path_msg(f) + '\n')
            if f.endswith(b'.idx'):
                extra.add(f)
        needed = set()
        for idx, load in self._list_indexes():
            if load:
                # If the server requests that we load an idx and we don't
                # already have a copy of it, it is needed
                needed.add(idx)
            # Any idx that the server has heard of is proven not extra
            extra.discard(idx)

        debug1('client: removing extra indexes: %s\n' % extra)
        for idx in extra:
            os.unlink(os.path.join(self.cachedir, idx))
        debug1('client: server requested load of: %s\n' % needed)
        for idx in needed:
            self.sync_index(idx)
        git.auto_midx(self.cachedir)

    def send_index(self, name, f, send_size):
        self._require_command(b'send-index')
        #debug1('requesting %r\n' % name)
        self.check_busy()
        self.conn.write(b'send-index %s\n' % name)
        n = struct.unpack('!I', self.conn.read(4))[0]
        assert(n)

        send_size(n)

        count = 0
        progress('Receiving index from server: %d/%d\r' % (count, n))
        for b in chunkyreader(self.conn, n):
            f.write(b)
            count += len(b)
            qprogress('Receiving index from server: %d/%d\r' % (count, n))
        progress('Receiving index from server: %d/%d, done.\n' % (count, n))
        self.check_ok()

    def sync_index(self, name):
        mkdirp(self.cachedir)
        fn = os.path.join(self.cachedir, name)
        if os.path.exists(fn):
            msg = ("won't request existing .idx, try `bup bloom --check %s`"
                   % path_msg(fn))
            raise ClientError(msg)
        with atomically_replaced_file(fn, 'wb') as f:
            self.send_index(name, f, lambda size: None)

    def _suggest_packs(self):
        ob = self._busy
        if ob:
            assert(ob == b'receive-objects-v2')
            self.conn.write(b'\xff\xff\xff\xff')  # suspend receive-objects-v2
        suggested = []
        for line in linereader(self.conn):
            if not line:
                break
            debug2('%r\n' % line)
            if line.startswith(b'index '):
                idx = line[6:]
                debug1('client: received index suggestion: %s\n'
                       % git.shorten_hash(idx).decode('ascii'))
                suggested.append(idx)
            else:
                assert(line.endswith(b'.idx'))
                debug1('client: completed writing pack, idx: %s\n'
                       % git.shorten_hash(line).decode('ascii'))
                suggested.append(line)
        self.check_ok()
        if ob:
            self._busy = None
        idx = None
        for idx in suggested:
            self.sync_index(idx)
        git.auto_midx(self.cachedir)
        if ob:
            self._busy = ob
            self.conn.write(b'%s\n' % ob)
        return idx

    def new_packwriter(self, compression_level=None,
                       max_pack_size=None, max_pack_objects=None,
                       objcache_maker=None, run_midx=True):
        self._require_command(b'receive-objects-v2')
        self.check_busy()
        def _set_busy():
            self._busy = b'receive-objects-v2'
            self.conn.write(b'receive-objects-v2\n')
        store = RemotePackStore(self.conn,
                                cache=self.cachedir,
                                suggest_packs=self._suggest_packs,
                                onopen=_set_busy,
                                onclose=self._not_busy,
                                ensure_busy=self.ensure_busy,
                                run_midx=run_midx)
        return PackWriter(store=store,
                          compression_level=compression_level,
                          max_pack_size=max_pack_size,
                          max_pack_objects=max_pack_objects)

    def read_ref(self, refname):
        self._require_command(b'read-ref')
        self.check_busy()
        self.conn.write(b'read-ref %s\n' % refname)
        r = self.conn.readline().strip()
        self.check_ok()
        if r:
            assert(len(r) == 40)   # hexified sha
            return unhexlify(r)
        else:
            return None   # nonexistent ref

    def update_ref(self, refname, newval, oldval):
        self._require_command(b'update-ref')
        self.check_busy()
        self.conn.write(b'update-ref %s\n%s\n%s\n'
                        % (refname, hexlify(newval),
                           hexlify(oldval) if oldval else b''))
        self.check_ok()

    def join(self, id):
        self._require_command(b'join')
        self.check_busy()
        self._busy = b'join'
        # Send 'cat' so we'll work fine with older versions
        self.conn.write(b'cat %s\n' % re.sub(br'[\n\r]', b'_', id))
        while 1:
            sz = struct.unpack('!I', self.conn.read(4))[0]
            if not sz: break
            yield self.conn.read(sz)
        # FIXME: ok to assume the only NotOk is a KeyError? (it is true atm)
        e = self.check_ok()
        self._not_busy()
        if e:
            raise KeyError(str(e))

    def cat_batch(self, refs):
        self._require_command(b'cat-batch')
        self.check_busy()
        self._busy = b'cat-batch'
        conn = self.conn
        conn.write(b'cat-batch\n')
        # FIXME: do we want (only) binary protocol?
        for ref in refs:
            assert ref
            assert b'\n' not in ref
            conn.write(ref)
            conn.write(b'\n')
        conn.write(b'\n')
        for ref in refs:
            info = conn.readline()
            if info == b'missing\n':
                yield None, None, None, None
                continue
            if not (info and info.endswith(b'\n')):
                raise ClientError('Hit EOF while looking for object info: %r'
                                  % info)
            oidx, oid_t, size = info.split(b' ')
            size = int(size)
            cr = chunkyreader(conn, size)
            yield oidx, oid_t, size, cr
            detritus = next(cr, None)
            if detritus:
                raise ClientError('unexpected leftover data ' + repr(detritus))
        # FIXME: confusing
        not_ok = self.check_ok()
        if not_ok:
            raise not_ok
        self._not_busy()

    def refs(self, patterns=None, limit_to_heads=False, limit_to_tags=False):
        patterns = patterns or tuple()
        self._require_command(b'refs')
        self.check_busy()
        self._busy = b'refs'
        conn = self.conn
        conn.write(b'refs %d %d\n' % (1 if limit_to_heads else 0,
                                      1 if limit_to_tags else 0))
        for pattern in patterns:
            assert b'\n' not in pattern
            conn.write(pattern)
            conn.write(b'\n')
        conn.write(b'\n')
        for line in lines_until_sentinel(conn, b'\n', ClientError):
            line = line[:-1]
            oidx, name = line.split(b' ')
            if len(oidx) != 40:
                raise ClientError('Invalid object fingerprint in %r' % line)
            if not name:
                raise ClientError('Invalid reference name in %r' % line)
            yield name, unhexlify(oidx)
        # FIXME: confusing
        not_ok = self.check_ok()
        if not_ok:
            raise not_ok
        self._not_busy()

    def rev_list(self, refs, parse=None, format=None):
        """See git.rev_list for the general semantics, but note that with the
        current interface, the parse function must be able to handle
        (consume) any blank lines produced by the format because the
        first one received that it doesn't consume will be interpreted
        as a terminator for the entire rev-list result.

        """
        self._require_command(b'rev-list')
        if format:
            assert b'\n' not in format
            assert parse
        for ref in refs:
            assert ref
            assert b'\n' not in ref
        self.check_busy()
        self._busy = b'rev-list'
        conn = self.conn
        conn.write(b'rev-list\n')
        conn.write(b'\n')
        if format:
            conn.write(format)
        conn.write(b'\n')
        for ref in refs:
            conn.write(ref)
            conn.write(b'\n')
        conn.write(b'\n')
        if not format:
            for line in lines_until_sentinel(conn, b'\n', ClientError):
                line = line.strip()
                assert len(line) == 40
                yield line
        else:
            for line in lines_until_sentinel(conn, b'\n', ClientError):
                if not line.startswith(b'commit '):
                    raise ClientError('unexpected line ' + repr(line))
                cmt_oidx = line[7:].strip()
                assert len(cmt_oidx) == 40
                yield cmt_oidx, parse(conn)
        # FIXME: confusing
        not_ok = self.check_ok()
        if not_ok:
            raise not_ok
        self._not_busy()

    def resolve(self, path, parent=None, want_meta=True, follow=True):
        self._require_command(b'resolve')
        self.check_busy()
        self._busy = b'resolve'
        conn = self.conn
        conn.write(b'resolve %d\n' % ((1 if want_meta else 0)
                                      | (2 if follow else 0)
                                      | (4 if parent else 0)))
        if parent:
            protocol.write_resolution(conn, parent)
        write_bvec(conn, path)
        success = ord(conn.read(1))
        assert success in (0, 1)
        if success:
            result = protocol.read_resolution(conn)
        else:
            result = protocol.read_ioerror(conn)
        # FIXME: confusing
        not_ok = self.check_ok()
        if not_ok:
            raise not_ok
        self._not_busy()
        if isinstance(result, vfs.IOError):
            raise result
        return result

    def config_get(self, name, opttype=None):
        assert isinstance(name, bytes)
        name = name.lower() # git is case insensitive here
        assert opttype in ('int', 'bool', None)
        self._require_command(b'config-get')
        self.check_busy()
        self._busy = b'config'
        conn = self.conn
        conn.write(b'config-get\n')
        vint.send(conn, 'ss', name, opttype.encode('ascii') if opttype else b'')
        kind = read_vuint(conn)
        if kind == 0:
            val = None
        elif kind == 1:
            val = True
        elif kind == 2:
            val = False
        elif kind == 3:
            val = read_vint(conn)
        elif kind == 4:
            val = read_bvec(conn)
        elif kind == 5:
            raise PermissionError(f'config-get does not allow remote access to {name}')
        else:
            raise TypeError(f'Unrecognized result type {kind}')
        # FIXME: confusing
        not_ok = self.check_ok()
        if not_ok:
            raise not_ok
        self._not_busy()
        return val


class RemotePackStore:
    def __init__(self, conn, *, cache, suggest_packs, onopen, onclose,
                 ensure_busy, run_midx=True):
        self._closed = False
        self._bwcount = 0
        self._bwtime = time.time()
        self._byte_count = 0
        self._cache = cache
        self._conn = conn
        self._ensure_busy = ensure_busy
        self._obj_count = 0
        self._objcache = None
        self._onclose = onclose
        self._onopen = onopen
        self._packopen = False
        self._suggest_packs = suggest_packs

    def __del__(self): assert self._closed

    def byte_count(self): return self._byte_count
    def object_count(self): return self._obj_count

    def objcache(self, *, require=True):
        if self._objcache is not None:
            return self._objcache
        if not require:
            return None
        self._objcache = git.PackIdxList(self._cache)
        assert self._objcache is not None
        return self._objcache

    def _open(self):
        if not self._packopen:
            self._onopen()
            self._packopen = True

    def write(self, datalist, sha):
        assert(self._conn)
        if not self._packopen:
            self._open()
        self._ensure_busy()
        data = b''.join(datalist)
        assert(data)
        assert(sha)
        crc = zlib.crc32(data) & 0xffffffff
        outbuf = b''.join((struct.pack('!I', len(data) + 20 + 4),
                           sha,
                           struct.pack('!I', crc),
                           data))
        try:
            self._bwcount, self._bwtime = \
                _raw_write_bwlimit(self._conn, outbuf, self._bwcount, self._bwtime)
        except IOError as e:
            raise ClientError(e) from e
        self._byte_count += len(data)
        self._obj_count += 1

        if self._conn.has_input():
            self._objcache.close_temps()
            self._suggest_packs()
            self._objcache.refresh()

        return sha, crc

    def finish_pack(self, *, abort=False):
        if abort:
            raise ClientError("don't know how to abort remote pack writing")
        # Called by other PackWriter methods like breakpoint().
        # Must not close the connection (self._conn)
        self._objcache, objcache = None, self._objcache
        with nullcontext_if_not(objcache):
            if not (self._packopen and self._conn):
                return None
            self._conn.write(b'\0\0\0\0')
            self._packopen = False
            self._onclose() # Unbusy
            if objcache is not None:
                objcache.close()
            return self._suggest_packs() # Returns last idx received

    def abort(self): self.finish_pack(abort=True)

    def close(self):
        self._closed = True
        oid = self.finish_pack()
        self._conn = None
        return oid
