
from contextlib import contextmanager
from errno import EAGAIN
from math import ceil
from os import fsdecode
from stat import S_ISDIR
import mmap as py_mmap
import os, select, sys, time

from bup.options import _tty_width as tty_width


# Get the umask once, early, so we can avoid further global changes.
initial_umask = os.umask(0o777)
os.umask(initial_umask)


# Write (blockingly) to sockets that may or may not be in blocking mode.
# We need this because our stderr is sometimes eaten by subprocesses
# (probably ssh) that sometimes make it nonblocking, if only temporarily,
# leading to race conditions.  Ick.  We'll do it the hard way.
def _hard_write(fd, buf):
    while buf:
        w = select.select([], [fd], [], None)[1]
        if not w:
            raise IOError('select(fd) returned without being writable')
        try:
            sz = os.write(fd, buf)
        except OSError as e:
            if e.errno != EAGAIN:
                raise
        assert(sz >= 0)
        buf = buf[sz:]


_clear_line_seq = b'\x1b[0K'
_last_prog = 0
_last_progress = ''
_active_progressbar = None

def log(s):
    """Print a log message to stderr."""
    global _last_prog
    sys.stdout.flush()
    msg = s if isinstance(s, bytes) else s.encode()
    if _active_progressbar:
        _active_progressbar.message(msg)
    else:
        if _last_prog and _last_progress.endswith('\r'):
            _hard_write(sys.stderr.fileno(), _clear_line_seq)
        _hard_write(sys.stderr.fileno(), msg)
    _last_prog = 0


buglvl = int(os.environ.get('BUP_DEBUG', 0))

def debug1(s):
    if buglvl >= 1:
        log(s)

def debug2(s):
    if buglvl >= 2:
        log(s)


istty1 = os.isatty(1) or (int(os.environ.get('BUP_FORCE_TTY', 0)) & 1)
istty2 = os.isatty(2) or (int(os.environ.get('BUP_FORCE_TTY', 0)) & 2)

def progress(s):
    """Calls log() if stderr is a TTY.  Does nothing otherwise."""
    global _last_progress
    if istty2:
        if _last_progress.endswith('\r'):
            _hard_write(sys.stderr.fileno(), _clear_line_seq)
        log(s)
        _last_progress = s


def qprogress(s):
    """Calls progress() only if we haven't printed progress in a while.

    This avoids overloading the stderr buffer with excess junk.
    """
    global _last_prog
    now = time.time()
    if now - _last_prog > 0.1:
        progress(s)
        _last_prog = now


def reprogress():
    """Calls progress() to redisplay the most recent progress message.

    Useful after you've printed some other message that wipes out the
    progress line.
    """
    if _last_progress and _last_progress.endswith('\r'):
        progress(_last_progress)


TIME_ESTIMATE_CUTOFF = 5 # seconds
_ROUNDTRIP_TIME = 10 # seconds for the moving bar to cross the width


@contextmanager
def suspend_progress():
    """Temporarily hide the active ProgressBar, if there is one.

    Useful while a subprocess writes to the terminal.
    """
    if not _active_progressbar:
        yield None
        return
    _active_progressbar.suspend()
    try:
        yield None
    finally:
        _active_progressbar.resume()


class ProgressBar:
    """Show a progress bar with an estimated remaining time on stderr.

    Use as a context manager.  While it is active, log() messages are
    printed above the bar.  With total=None, the bar is a moving
    indicator showing that progress is being made.
    """

    def __init__(self, total=None):
        self._done = 0
        self._total = total
        self._last = 0.0
        self._tty_width = tty_width()
        self._enabled = self._tty_width >= 30 and istty2
        self._start = time.time()
        self._msg = ''
        self._previous = None

    def _log(self, s):
        if self._enabled:
            _hard_write(sys.stderr.fileno(),
                        s if isinstance(s, bytes) else s.encode())

    def _bar(self):
        n = self._done
        total = self._total
        width = self._tty_width - 2

        if total is not None:
            if n == total:
                self._log(b'|' + b'=' * width + b'|')
            elif n == 0:
                self._log(b'|' + b' ' * width + b'|')
            else:
                partial = int(width * n / total)
                if partial > 0:
                    partial -= 1
                self._log(b'|' + b'=' * partial + b'>'
                          + b' ' * (width - partial - 1) + b'|')
        else:
            width -= 3 # width of the indicator
            fposition = (time.time() - self._start) % _ROUNDTRIP_TIME
            position = int(width * fposition / _ROUNDTRIP_TIME * 2)
            if position > width:
                position = width - (position - width)
            self._log(b'|' + b' ' * position + b'<=>'
                      + b' ' * (width - position) + b'|')

    def _timestr(self, t):
        t = ceil(t)
        hours = int(t / 60 / 60)
        mins = int(t / 60 - hours * 60)
        if hours:
            return ' (%dh%dm)' % (hours, mins)
        secs = int(t - hours * 60 * 60 - mins * 60)
        return ' (%dm%ds)' % (mins, secs)

    def _update(self):
        self._log(b'\n')
        self._bar()
        self._log(b'\033[F')

        self._log(self._msg)
        elapsed = time.time() - self._start
        # The remaining time is only estimated once some progress was made.
        if self._total and self._done and elapsed > TIME_ESTIMATE_CUTOFF:
            remain = elapsed / self._done * (self._total - self._done)
            self._log(self._timestr(remain))
        self._log(_clear_line_seq)

    def _blank(self):
        # Clear the message line and the bar line below it, then return
        # to the message line.
        self._log(b'\r' + _clear_line_seq + b'\n\r' + _clear_line_seq
                  + b'\033[F')

    def __enter__(self):
        global _active_progressbar
        self._previous = _active_progressbar
        _active_progressbar = self
        return self

    def __exit__(self, type, value, traceback):
        global _active_progressbar
        # must leave in the right order to un-nest properly
        assert self is _active_progressbar
        _active_progressbar = self._previous
        self._blank()
        self._log(self._msg)
        self._log(b' Done.')
        elapsed = time.time() - self._start
        if elapsed > TIME_ESTIMATE_CUTOFF:
            self._log(self._timestr(elapsed))
        self._log(b'\n')

    def update(self, done, msg):
        total = self._total
        if total is not None and done > total:
            done = total
        self._done = done
        self._msg = msg
        now = time.time()
        if now - self._last > 0.1:
            self._last = now
            self._update()

    def message(self, msg):
        self._blank()
        _hard_write(sys.stderr.fileno(), msg)
        self._update()

    def suspend(self):
        self._blank()

    def resume(self):
        self._update()


def byte_stream(file):
    return file.buffer


def _make_enc_sh_map():
    m = [None] * 256
    for i in range(7): m[i] = br'\x%02x' % i
    m[7] = br'\a'
    m[8] = br'\b'
    m[9] = br'\t'
    m[10] = br'\n'
    m[11] = br'\v'
    m[12] = br'\f'
    m[13] = br'\r'
    for i in range(14, 27): m[i] = br'\x%02x' % i
    m[27] = br'\e' # ESC
    for i in range(28, 32): m[i] = br'\x%02x' % i
    m[39] = br"\'"
    m[92] = br'\\'
    for i in range(127, 256): m[i] = br'\x%02x' % i
    return m

_enc_sh_map = _make_enc_sh_map()

def _make_enc_shs_map():
    m = [None] * 128
    for i in range(128):
        enc = _enc_sh_map[i]
        if enc:
            m[i] = enc.decode('ascii')
    return m

_enc_shs_map = _make_enc_shs_map()


def enc_dsq(val):
    """Encode val (bytes) in POSIX $'...' (dollar-single-quote)
    format.
    https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap02.html#tag_19_02_04

    """
    result = [b"$'"]
    part_start = 0
    i = 0

    def finish_part():
        nonlocal result, i, part_start
        if i != part_start:
            result.append(val[part_start:i])
        part_start = i = i + 1

    encoding = _enc_sh_map
    while i < len(val):
        b = val[i]
        enc = encoding[b]
        if enc:
            finish_part()
            result.append(enc)
        else:
            i += 1
    finish_part()
    result.append(b"'")
    return b''.join(result)

def enc_dsqs(val):
    """Encode string in POSIX $'...' (dollar-single-quote) format with
    any surrogates (from surrogate escape) \\xNN encoded as the
    original bytes. Pass through any characters whose ord() is >= 128.
    https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap02.html#tag_19_02_04
    https://peps.python.org/pep-0383/

    """
    result = ["$'"]
    part_start = 0
    i = 0

    def finish_part():
        nonlocal result, i, part_start
        if i != part_start:
            result.append(val[part_start:i])
        part_start = i = i + 1

    encoding = _enc_shs_map
    while i < len(val):
        b = ord(val[i])
        if b < 128:
            enc = encoding[b]
        elif 0xdc80 <= b <= 0xdcff: # surrogate escape
            enc = r'\x%02x' % (128 + (b - 0xdc80))
        else:
            enc = None
        if enc:
            finish_part()
            result.append(enc)
        else:
            i += 1
    finish_part()
    result.append("'")
    return ''.join(result)


def enc_sh(val):
    """Minimally POSIX quote val (bytes) as a single line. Use no
    quotes if possible, single quotes if val doesn't contain single
    quotes or newline, otherwise dollar-single-quote.
    https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap02.html#tag_19_02

    For now, like git with core.quotePath set to false, this
    conservatively hex escapes all bytes with the high bit set,
    keeping the output compatible with any encoding that's compatible
    with ASCII, e.g. UTF-8, Latin-1, etc.

    """
    #pylint: disable=consider-using-in
    assert isinstance(val, bytes), val
    if val == b'':
        return b"''"
    need_sq = False
    need_dsq = False
    for c in val: # 32 is space
        if c < 32 or c >= 127 or c == b"'"[0]:
            need_dsq = True
            break
        # This set is everything from POSIX except ' and \n (handled above).
        if c in b'|&;<>()$`\\" \t*?[]^!#~=%{,}':
            need_sq = True
    if need_dsq:
        return enc_dsq(val)
    if need_sq:
        return b"'%s'" % val
    return val

def enc_shs(val):
    """Minimally POSIX quote val (string) as a single line. Use no
    quotes if possible, single quotes if val doesn't contain single
    quotes or newline, otherwise dollar-single-quote.
    https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap02.html#tag_19_02
    https://peps.python.org/pep-0383/

    \\xNN encode any surrogates (from surrogate escape) as the
    original bytes. Pass through any characters whose ord() is >= 128.

    """
    #pylint: disable=consider-using-in
    assert isinstance(val, str), val
    if val == '':
        return "''"
    need_sq = False
    need_dsq = False
    for ch in val:
        c = ord(ch)
        if c < 32 or c == b"'"[0] or c == 127 \
           or 0xdc80 <= c <= 0xdcff: # lone surrogate (PEP-0383)
            need_dsq = True
            break
        # This set is everything from POSIX except ' and \n (handled above).
        if ch in '|&;<>()$`\\" \t*?[]^!#~=%{,}':
            need_sq = True
    if need_dsq:
        return enc_dsqs(val)
    if need_sq:
        return f"'{val}'"
    return val


def path_msg(x):
    """Return a string representation of a path."""
    return enc_shs(fsdecode(x))

def cmd_msg(cmd):
    """Return an sh quoted representation of the cmd list (bytes or
    strings) as a string.  The individual elements are quoted, not the
    whole command.  e.g. ("ls", "x y") becomes "ls 'x y'".

    """
    return ' '.join(enc_shs(x if isinstance(x, str) else fsdecode(x))
                    for x in cmd)

def walk_path_msg(ref_name, item_path):
    # walk ref of
    #   archive/latest/home
    # produces
    #   archive/latest/home 1c8749ada58cbb2b7e3752db12ee7bbbded5cf84:rlb/.bupm
    #
    # Currently, the item_path is always an optional sequence of
    # commits (named by their oids), followed by the last commit's
    # tree (also oid named), followed by any remaining items, with
    # their git tree names.
    if len(item_path) == 1:
        return path_msg(item_path[0].name)
    root = None # either the last commit or a tree
    path_top = 1 # skips the tree if we have a commit
    for i in range(-1, - (len(item_path) + 1), -1):
        if item_path[i].type == b'commit':
            root = i
            break
    if root is None:
        root = 0
    else:
        path_top = root + 2
    path = path_msg(b'/'.join(x.name for x in item_path[path_top:]))
    path = f'{path_msg(item_path[root].name)}:{path}'
    if S_ISDIR(item_path[-1].mode):
        return f'{path_msg(ref_name)} {path}/'
    return f'{path_msg(ref_name)} {path}'


def qsql_id(s):
    return ''.join(('"', s.replace('"', '""'), '"'))
def qsql_str(s):
    return ''.join(("'", s.replace("'", "''"), "'"))


assert not hasattr(py_mmap.mmap, '__del__')

class mmap(py_mmap.mmap):
    '''mmap.mmap wrapper that detects and complains about any instances
    that aren't explicitly closed.

    '''
    def __new__(cls, *args, **kwargs):
        result = super().__new__(cls, *args, **kwargs)
        result._bup_closed = True  # supports __del__
        return result

    def __init__(self, *args, **kwargs):
        # Silence deprecation warnings.  mmap's current parent is
        # object, which accepts no params and as of at least 2.7
        # warns about them.
        if py_mmap.mmap.__init__ is not object.__init__:
            super().__init__(self, *args, **kwargs)
        self._bup_closed = False

    def close(self):
        self._bup_closed = True
        super().close()

    def __enter__(self):
        super().__enter__()
        return self
    def __exit__(self, type, value, traceback):
        # Don't call self.close() when the parent has its own __exit__;
        # defer to it.
        self._bup_closed = True
        result = super().__exit__(type, value, traceback)
        return result

    def __del__(self):
        assert self._bup_closed
