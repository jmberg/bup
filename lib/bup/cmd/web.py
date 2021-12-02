
from __future__ import absolute_import, print_function
from collections import namedtuple
import mimetypes, os, posixpath, signal, stat, sys, time, webbrowser
from binascii import hexlify
import traceback


from bup import options, vfs
from bup.helpers import (chunkyreader, debug1, format_filesize,
                         log, saved_errors)
from bup.metadata import Metadata
from bup.path import resource_path
from bup.repo import from_opts
from bup.io import path_msg

try:
    from tornado import gen
    from tornado.httpserver import HTTPServer
    from tornado.ioloop import IOLoop
    from tornado.netutil import bind_unix_socket
    import tornado.web
except ImportError:
    log('error: cannot find the python "tornado" module; please install it\n')
    sys.exit(1)


# FIXME: right now the way hidden files are handled causes every
# directory to be traversed twice.


def http_date_from_utc_ns(utc_ns):
    return time.strftime('%a, %d %b %Y %H:%M:%S', time.gmtime(utc_ns / 10**9))


class QueryArgs:
    __slots__ = (
        'args',
        'opts',
        # arg names - also see below for types/defaults
        'hidden',
        'meta',
        'hashes',
        'hsizes',
    )

    def __init__(self, opts, **kw):
        self.opts = opts
        self.args = (
            ('hidden', int, 0),
            ('meta', int, 0),
            ('hashes', int, 0),
            ('hsizes', int, 1 if (opts and opts.human_readable) else 0),
        )
        for name, tp, default in self.args:
            if name in kw:
                setattr(self, name, tp(kw[name]))
            else:
                setattr(self, name, default)

    @classmethod
    def from_args(cls, opts, args):
        new = QueryArgs(opts)
        for name, tp, default in new.args:
            try:
                setattr(new, name, tp(args.get(name, [default])[-1]))
            except ValueError:
                pass
        return new

    def change(self, **kw):
        new = QueryArgs(self.opts)
        for name, tp, default in self.args:
            if name in kw:
                setattr(new, name, tp(kw[name]))
            else:
                setattr(new, name, getattr(self, name))
        return new

    def __radd__(self, v):
        return v + bytes(self)

    def __bytes__(self):
        vals = []
        fmts = {
            int: b'%d',
        }
        for name, tp, default in self.args:
            val = getattr(self, name)
            if val != default:
                fmt = fmts[tp]
                n = name.encode('ascii')
                vals.append(n + b'=' + fmt % val)
        if not vals:
            return b''
        return b'?' + b'&'.join(vals)

    def __str__(self):
        return self.__bytes__().decode('ascii')


def _compute_breadcrumbs(path, args):
    """Returns a list of breadcrumb objects for a path."""
    breadcrumbs = []
    breadcrumbs.append((b'[root]', b'/' + args))
    path_parts = path.split(b'/')[1:-1]
    full_path = b'/'
    for part in path_parts:
        full_path += part + b"/"
        url_append = b""
        breadcrumbs.append((part, full_path + args))
    return breadcrumbs


def _contains_hidden_files(repo, dir_item):
    """Return true if the directory contains items with names other than
    '.' and '..' that begin with '.'

    """
    for name, item in vfs.contents(repo, dir_item, want_meta=False):
        if name in (b'.', b'..'):
            continue
        if name.startswith(b'.'):
            return True
    return False


def _dir_contents(repo, resolution, args):
    """Yield the display information for the contents of dir_item."""

    def display_info(name, item, resolved_item, display_name=None, omitsize=False):
        global opt
        # link should be based on fully resolved type to avoid extra
        # HTTP redirect.
        link = tornado.escape.url_escape(name, plus=False)
        if stat.S_ISDIR(vfs.item_mode(resolved_item)):
            link += '/'
        link = link.encode('ascii')

        if not omitsize:
            size = vfs.item_size(repo, item)
            if args.hsizes:
                display_size = format_filesize(size)
            else:
                display_size = size
        else:
            display_size = None

        if not display_name:
            mode = vfs.item_mode(item)
            if stat.S_ISDIR(mode):
                display_name = name + b'/'
                display_size = None
            elif stat.S_ISLNK(mode):
                display_name = name + b'@'
                display_size = None
            else:
                display_name = name

        meta = resolved_item.meta
        if not isinstance(meta, Metadata):
            meta = None
        try:
            oidx = hexlify(resolved_item.oid)
        except AttributeError:
            oidx = ''
        return display_name, link + args, display_size, meta, oidx

    dir_item = resolution[-1][1]
    for name, item in vfs.contents(repo, dir_item):
        if not args.hidden:
            if (name not in (b'.', b'..')) and name.startswith(b'.'):
                continue
        if name == b'.':
            if len(resolution) > 1:
                parent_item = resolution[-2][1] if len(resolution) > 1 else dir_item
                yield display_info(b'..', parent_item, parent_item, b'..', omitsize=True)
            continue
        res_item = vfs.ensure_item_has_metadata(repo, item, include_size=args.meta)
        yield display_info(name, item, res_item, omitsize=not args.meta)


class BupRequestHandler(tornado.web.RequestHandler):

    def initialize(self, repo=None):
        self.repo = repo

    def decode_argument(self, value, name=None):
        if name == 'path':
            return value
        return super(BupRequestHandler, self).decode_argument(value, name)

    def get(self, path):
        return self._process_request(path)

    def head(self, path):
        return self._process_request(path)

    def _process_request(self, path):
        print('Handling request for %s' % path)
        sys.stdout.flush()
        # Set want_meta because dir metadata won't be fetched, and if
        # it's not a dir, then we're going to want the metadata.
        res = vfs.resolve(self.repo, path, want_meta=True)
        leaf_name, leaf_item = res[-1]
        if not leaf_item:
            self.send_error(404)
            return
        mode = vfs.item_mode(leaf_item)
        if stat.S_ISDIR(mode):
            self._list_directory(path, res)
        else:
            self._get_file(self.repo, path, res)

    def _list_directory(self, path, resolution):
        """Helper to produce a directory listing.

        Return value is either a file object, or None (indicating an
        error).  In either case, the headers are sent.
        """
        global opt
        args = QueryArgs.from_args(opt, self.request.arguments)

        if not path.endswith(b'/') and len(path) > 0:
            print('Redirecting from %s to %s' % (path_msg(path), path_msg(path + b'/')))
            return self.redirect(path + b'/' + args, permanent=True)

        self.render(
            'list-directory.html',
            path=path,
            breadcrumbs=_compute_breadcrumbs(path, args),
            files_hidden=_contains_hidden_files(self.repo, resolution[-1][1]),
            args=args,
            dir_contents=_dir_contents(self.repo, resolution, args))
        return None

    def _set_header(self, path, file_item):
        meta = file_item.meta
        ctype = self._guess_type(path)
        assert len(file_item.oid) == 20
        self.set_header("Last-Modified", http_date_from_utc_ns(meta.mtime))
        self.set_header("Content-Type", ctype)
        self.set_header("Etag", hexlify(file_item.oid))
        self.set_header("Content-Length", str(meta.size))

    @gen.coroutine
    def _get_file(self, repo, path, resolved):
        """Process a request on a file.

        Return value is either a file object, or None (indicating an error).
        In either case, the headers are sent.
        """
        try:
            file_item = resolved[-1][1]
            file_item = vfs.augment_item_meta(repo, file_item, include_size=True)

            # we defer the set_header() calls until after we start writing
            # so we can still generate a 500 failure if something fails ...
            if self.request.method != 'HEAD':
                set_header = False
                with vfs.fopen(self.repo, file_item) as f:
                    it = chunkyreader(f)
                    for blob in chunkyreader(f):
                        if not set_header:
                            self._set_header(path, file_item)
                            set_header = True
                        self.write(blob)
            else:
                self._set_header(path, file_item)
        except Exception as e:
            self.set_status(500)
            self.write("<h1>Server Error</h1>\n")
            self.write("%s: %s\n" % (e.__class__.__name__, str(e)))
            self.write('<pre>')
            self.write(traceback.format_exc())
            self.write('</pre>')
        raise gen.Return()

    def _guess_type(self, path):
        """Guess the type of a file.

        Argument is a PATH (a filename).

        Return value is a string of the form type/subtype,
        usable for a MIME Content-type header.

        The default implementation looks the file's extension
        up in the table self.extensions_map, using application/octet-stream
        as a default; however it would be permissible (if
        slow) to look inside the data to make a better guess.
        """
        base, ext = posixpath.splitext(path)
        if ext in self.extensions_map:
            return self.extensions_map[ext]
        ext = ext.lower()
        if ext in self.extensions_map:
            return self.extensions_map[ext]
        else:
            return self.extensions_map['']

    if not mimetypes.inited:
        mimetypes.init() # try to read system mime.types
    extensions_map = mimetypes.types_map.copy()
    extensions_map.update({
        '': 'text/plain', # Default
        '.py': 'text/plain',
        '.c': 'text/plain',
        '.h': 'text/plain',
        })


io_loop = None

def handle_sigterm(signum, frame):
    global io_loop
    debug1('\nbup-web: signal %d received\n' % signum)
    log('Shutdown requested\n')
    if not io_loop:
        sys.exit(0)
    io_loop.stop()


optspec = """
bup web [[hostname]:port]
bup web unix://path
--
r,remote=         remote repository path
human-readable    display human readable file sizes (i.e. 3.9K, 4.7M)
browser           show repository in default browser (incompatible with unix://)
"""

opt = None

def main(argv):
    global opt
    signal.signal(signal.SIGTERM, handle_sigterm)

    UnixAddress = namedtuple('UnixAddress', ['path'])
    InetAddress = namedtuple('InetAddress', ['host', 'port'])

    o = options.Options(optspec)
    opt, flags, extra = o.parse_bytes(argv[1:])

    if len(extra) > 1:
        o.fatal("at most one argument expected")

    if len(extra) == 0:
        address = InetAddress(host='127.0.0.1', port=8080)
    else:
        bind_url = extra[0]
        if bind_url.startswith('unix://'):
            address = UnixAddress(path=bind_url[len('unix://'):])
        else:
            addr_parts = extra[0].split(':', 1)
            if len(addr_parts) == 1:
                host = '127.0.0.1'
                port = addr_parts[0]
            else:
                host, port = addr_parts
            try:
                port = int(port)
            except (TypeError, ValueError) as ex:
                o.fatal('port must be an integer, not %r' % port)
            address = InetAddress(host=host, port=port)

    settings = dict(
        debug = 1,
        template_path = resource_path(b'web').decode('utf-8'),
        static_path = resource_path(b'web/static').decode('utf-8'),
    )

    # Disable buffering on stdout, for debug messages
    try:
        sys.stdout._line_buffering = True
    except AttributeError:
        sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)

    with from_opts(opt, reverse=False) as repo:
        handlers = [ (r"(?P<path>/.*)", BupRequestHandler, dict(repo=repo))]
        application = tornado.web.Application(handlers, **settings)

        http_server = HTTPServer(application)
        io_loop_pending = IOLoop.instance()

        if isinstance(address, InetAddress):
            sockets = tornado.netutil.bind_sockets(address.port, address.host)
            http_server.add_sockets(sockets)
            print('Serving HTTP on %s:%d...' % sockets[0].getsockname()[0:2])
            if opt.browser:
                browser_addr = 'http://' + address[0] + ':' + str(address[1])
                io_loop_pending.add_callback(lambda : webbrowser.open(browser_addr))
        elif isinstance(address, UnixAddress):
            unix_socket = bind_unix_socket(address.path)
            http_server.add_socket(unix_socket)
            print('Serving HTTP on filesystem socket %r' % address.path)
        else:
            log('error: unexpected address %r', address)
            sys.exit(1)

        io_loop = io_loop_pending
        io_loop.start()

    if saved_errors:
        log('WARNING: %d errors encountered while saving.\n' % len(saved_errors))
        sys.exit(1)
