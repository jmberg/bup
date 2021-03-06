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

# For now, this completely relies on the assumption that the current
# encoding (LC_CTYPE, etc.) is ASCII compatible, and that it returns
# the exact same bytes from a decode/encode round-trip (or the reverse
# (e.g. ISO-8859-1).

from __future__ import absolute_import, print_function
import os, fnmatch, stat, sys

sys.path[:0] = [os.path.dirname(os.path.realpath(__file__)) + '/..']

from bup import _helpers, compat, options, git, shquote, ls, vfs
from bup.compat import argv_bytes, fsdecode
from bup.helpers import chunkyreader, handle_ctrl_c, log
from bup.io import byte_stream, path_msg
from bup.repo import from_opts

handle_ctrl_c()


class CommandError(Exception):
    pass


class OptionError(Exception):
    pass


def do_ls(repo, args, out):
    pwd_str = b'/'.join(name for name, item in pwd) or b'/'
    try:
        opt = ls.opts_from_cmdline([fsdecode(arg) for arg in args],
                                   onabort=OptionError, pwd=pwd_str)
    except OptionError as e:
        return
    return ls.within_repo(repo, opt, out, pwd_str)


def write_to_file(inf, outf):
    for blob in chunkyreader(inf):
        outf.write(blob)


def inputiter():
    if os.isatty(stdin.fileno()):
        while 1:
            prompt = b'bup %s> ' % (b'/'.join(name for name, item in pwd) or b'/', )
            if hasattr(_helpers, 'readline'):
                try:
                    yield _helpers.readline(prompt)
                except EOFError:
                    print()  # Clear the line for the terminal's next prompt
                    break
            else:
                out.write(prompt)
                out.flush()
                read_line = stdin.readline()
                if not read_line:
                    print('')
                    break
                yield read_line
    else:
        for line in stdin:
            yield line


def _completer_get_subs(repo, line):
    (qtype, lastword) = shquote.unfinished_word(line)
    dir, name = os.path.split(lastword)
    dir_path = vfs.resolve(repo, dir or b'/')
    _, dir_item = dir_path[-1]
    if not dir_item:
        subs = tuple()
    else:
        subs = tuple(dir_path + (entry,)
                     for entry in vfs.contents(repo, dir_item)
                     if (entry[0] != b'.' and entry[0].startswith(name)))
    return qtype, lastword, subs


_attempt_start = None
_attempt_end = None
def attempt_completion(text, start, end):
    global _attempt_start, _attempt_end
    _attempt_start = start
    _attempt_end = end
    return None

_last_line = None
_last_res = None
def enter_completion(text, iteration):
    global repo
    global _attempt_end
    global _last_line
    global _last_res
    try:
        line = _helpers.get_line_buffer()[:_attempt_end]
        if _last_line != line:
            _last_res = _completer_get_subs(repo, line)
            _last_line = line
        qtype, lastword, subs = _last_res
        if iteration < len(subs):
            path = subs[iteration]
            leaf_name, leaf_item = path[-1]
            res = vfs.try_resolve(repo, leaf_name, parent=path[:-1])
            leaf_name, leaf_item = res[-1]
            fullname = os.path.join(*(name for name, item in res))
            if stat.S_ISDIR(vfs.item_mode(leaf_item)):
                ret = shquote.what_to_add(qtype, lastword, fullname + b'/',
                                          terminate=False)
            else:
                ret = shquote.what_to_add(qtype, lastword, fullname,
                                          terminate=True) + b' '
            return text + ret
    except Exception as e:
        log('\n')
        try:
            import traceback
            traceback.print_tb(sys.exc_traceback)
        except Exception as e2:
            log('Error printing traceback: %s\n' % e2)
        log('\nError in completion: %s\n' % e)


optspec = """
bup ftp [commands...]
--
r,remote=         remote repository path
"""
o = options.Options(optspec)
opt, flags, extra = o.parse(compat.argv[1:])

repo = from_opts(opt, reverse=False)

sys.stdout.flush()
out = byte_stream(sys.stdout)
stdin = byte_stream(sys.stdin)
pwd = vfs.resolve(repo, b'/')
rv = 0



if extra:
    lines = (argv_bytes(arg) for arg in extra)
else:
    if hasattr(_helpers, 'readline'):
        _helpers.set_completer_word_break_characters(b' \t\n\r/')
        _helpers.set_attempted_completion_function(attempt_completion)
        _helpers.set_completion_entry_function(enter_completion)
        if sys.platform.startswith('darwin'):
            # MacOS uses a slightly incompatible clone of libreadline
            _helpers.parse_and_bind(b'bind ^I rl_complete')
        _helpers.parse_and_bind(b'tab: complete')
    lines = inputiter()

for line in lines:
    if not line.strip():
        continue
    words = [word for (wordstart,word) in shquote.quotesplit(line)]
    cmd = words[0].lower()
    #log('execute: %r %r\n' % (cmd, parm))
    try:
        if cmd == b'ls':
            do_ls(repo, words[1:], out)
            out.flush()
        elif cmd == b'cd':
            np = pwd
            for parm in words[1:]:
                res = vfs.resolve(repo, parm, parent=np)
                _, leaf_item = res[-1]
                if not leaf_item:
                    raise CommandError(b'"%s" does not exist' %
                                       b'/'.join(name for name, item in res))
                if not stat.S_ISDIR(vfs.item_mode(leaf_item)):
                    raise CommandError(b'"%s" is not a directory' % parm)
                np = res
            pwd = np
        elif cmd == b'pwd':
            if len(pwd) == 1:
                out.write(b'/')
            out.write(b'/'.join(name for name, item in pwd) + b'\n')
            out.flush()
        elif cmd == b'cat':
            for parm in words[1:]:
                res = vfs.resolve(repo, parm, parent=pwd)
                _, leaf_item = res[-1]
                if not leaf_item:
                    raise CommandError(b'"%s" does not exist' %
                                       b'/'.join(name for name, item in res))
                with vfs.fopen(repo, leaf_item) as srcfile:
                    write_to_file(srcfile, out)
            out.flush()
        elif cmd == b'get':
            if len(words) not in [2,3]:
                rv = 1
                raise CommandError(b'Usage: get <filename> [localname]')
            rname = words[1]
            (dir,base) = os.path.split(rname)
            lname = len(words) > 2 and words[2] or base
            res = vfs.resolve(repo, rname, parent=pwd)
            _, leaf_item = res[-1]
            if not leaf_item:
                raise CommandError(b'"%s" does not exist' %
                                   b'/'.join(name for name, item in res))
            with vfs.fopen(repo, leaf_item) as srcfile:
                with open(lname, 'wb') as destfile:
                    log('Saving %s\n' % path_msg(lname))
                    write_to_file(srcfile, destfile)
        elif cmd == b'mget':
            for parm in words[1:]:
                dir, base = os.path.split(parm)

                res = vfs.resolve(repo, dir, parent=pwd)
                _, dir_item = res[-1]
                if not dir_item:
                    raise CommandError(b'"%s" does not exist' % dir)
                for name, item in vfs.contents(repo, dir_item):
                    if name == b'.':
                        continue
                    if fnmatch.fnmatch(name, base):
                        if stat.S_ISLNK(vfs.item_mode(item)):
                            deref = vfs.resolve(repo, name, parent=res)
                            deref_name, deref_item = deref[-1]
                            if not deref_item:
                                raise CommandError(b'"%s" does not exist' %
                                                   b'/'.join(name for name, item in res))
                            item = deref_item
                        with vfs.fopen(repo, item) as srcfile:
                            with open(name, 'wb') as destfile:
                                log('Saving %s\n' % path_msg(name))
                                write_to_file(srcfile, destfile)
        elif cmd == b'help' or cmd == b'?':
            out.write(b'Commands: ls cd pwd cat get mget help quit\n')
            out.flush()
        elif cmd in (b'quit', b'exit', b'bye'):
            break
        else:
            rv = 1
            raise CommandError(b'no such command "%s"' % cmd)
    except CommandError as e:
        rv = 1
        out.write(b'error: %s\n' % e.args[0])
        out.flush()
    except Exception as e:
        rv = 1
        out.write(b'error: %s\n' % str(e).encode())
        out.flush()

sys.exit(rv)
