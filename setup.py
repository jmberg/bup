#!/usr/bin/env python

from __future__ import absolute_import, print_function
import os
import sys
import shutil
import tempfile

from setuptools import setup, find_packages, Extension
from distutils.ccompiler import new_compiler, gen_preprocess_options

def _try_compile(src, defines=None, flags=None):
    if not defines:
        defines = []
    if not flags:
        flags = []

    tmpdir = tempfile.mkdtemp(prefix='hasfunction-')
    oldstdout = oldstderr = devnull = None
    try:
        try:
            fname = os.path.join(tmpdir, 'funcname.c')
            f = open(fname, 'w')
            f.write(src)
            f.close()
            # this is kinda ugly - but the class doesn't let us
            # provide stdout/stderr for the subprocess ...
            devnull = open(os.devnull, 'w')
            sys.stdout.flush()
            sys.stderr.flush()
            oldstdout = os.dup(sys.stdout.fileno())
            oldstderr = os.dup(sys.stderr.fileno())
            os.dup2(devnull.fileno(), sys.stderr.fileno())
            os.dup2(devnull.fileno(), sys.stdout.fileno())
            cc = new_compiler()
            preargs = gen_preprocess_options(defines, []) + flags
            objects = cc.compile([fname], output_dir=tmpdir,
                                 extra_preargs=preargs)
            cc.link_executable(objects, os.path.join(tmpdir, 'exe'))
        except Exception as e:
            ret = False
        else:
            ret = True
    finally:
        if oldstdout is not None:
            os.dup2(oldstdout, sys.stdout.fileno())
        if oldstderr is not None:
            os.dup2(oldstderr, sys.stderr.fileno())
        if devnull is not None:
            devnull.close()
        shutil.rmtree(tmpdir)
    if ret:
        print(" (yes)")
    else:
        print(" (no)")


def try_compile(define, src, defines=None):
    if _try_compile(src, defines):
        return (define, 1)
    return None

def have_include(include):
    print("checking if we have header %s" % include, end='')
    define = 'HAVE_' + include.upper().replace('/', '_').replace('.', '_')
    return try_compile(define, """
        #include <%s>
        int main(int argc, char **argv) { return 0; }
    """ % include)

def have_func(func):
    print("checking if we have %s()" % func, end='')
    define = 'HAVE_' + func.upper()
    return try_compile(define, """
    int main(int argc, char **argv)
    {
        %s();
        return 0;
    }""" % func)

def have_utimensat():
    # On GNU/kFreeBSD utimensat is defined in GNU libc, but won't work.
    if os.environ.get('OS_GNU_KFREEBSD', None):
        return []
    return have_func('utimensat')

def have_mincore_incore(defines):
    print("checking if we have MINCORE_INCORE", end='')
    mincore_incore_code='''
#ifdef HAVE_UNISTD_H
#include <unistd.h>
#endif
#ifdef HAVE_SYS_MMAN_H
#include <sys/mman.h>
#endif
int main(int argc, char **argv)
{
    if (MINCORE_INCORE)
      return 0;
}
'''
    return try_compile('BUP_HAVE_MINCORE_INCORE',
                       mincore_incore_code, defines)

def check_mincore_buftype(defines):
    if ('HAVE_MINCORE', 1) not in defines:
        return None
    for mtype in ('unsigned char', 'char'):
        mincore_buf_type_code = '''
    #include <sys/mman.h>
    int main(int argc, char **argv)
    {
        void *x = 0;
        %s *buf = 0;
        return mincore(x, 0, buf);
    }
    ''' % mtype
        print("checking if mincore() buf is %s" % mtype, end='')
        if _try_compile(mincore_buf_type_code,
                        flags=['-Wall', '-Werror']):
            return ('BUP_MINCORE_BUF_TYPE', mtype)
    print("ERROR: unexpected mincore definition; please notify bup-list@googlegroups.com")
    sys.exit(2)

def check_field(struct, field, includes):
    code = '''
%s

int main(int argc, char **argv)
{
    struct %s foo;

    foo.%s;
}
''' % (
    '\n'.join(['#include <%s>' % i for i in includes]),
    struct,
    field
)

    print("checking if struct %s has field %s" % (struct, field), end='')
    define = 'HAVE_%s_%s' % (struct, field)
    define = define.upper()
    return try_compile(define, code)

def check_stat_field(field):
    return check_field('stat', field,
                       ['sys/types.h', 'sys/stat.h', 'unistd.h'])

def have_builtin_mul_overflow():
    builtin_mul_overflow_code='''
#include <stddef.h>
int main(int argc, char **argv)
{
    size_t n = 0, size = 0, total;
    __builtin_mul_overflow(n, size, &total);
    return 0;
}
'''

    print("checking if we have __builtin_mul_overflow()", end='')
    return try_compile('BUP_HAVE_BUILTIN_MUL_OVERFLOW',
                       builtin_mul_overflow_code)


# Create a class so that we can lazily evaluate this only if needed
class Defines:
    def __init__(self):
        self._defines = None
    def _fill(self):
        if self._defines is not None:
            return
        defines = [
            # for stat
            have_include('sys/stat.h'),
            have_include('sys/types.h'),
            # for stat and mincore
            have_include('unistd.h'),
            # for mincore
            have_include('sys/mman.h'),
            # for FS_IOC_GETFLAGS and FS_IOC_SETFLAGS.
            have_include('linux/fs.h'),
            have_include('sys/ioctl.h'),
            have_utimensat(),
            have_func('utimes'),
            have_func('lutimes'),
            have_builtin_mul_overflow(),
            have_func('mincore'),
        ]
        defines = [d for d in defines if d is not None]
        defines.append(have_mincore_incore(defines))
        defines.append(check_mincore_buftype(defines))
        defines += [
            check_stat_field('st_atim'),
            check_stat_field('st_mtim'),
            check_stat_field('st_ctim'),
            check_stat_field('st_atimensec'),
            check_stat_field('st_mtimensec'),
            check_stat_field('st_ctimensec'),
        ]
        defines.append(check_field('tm', 'tm_gmtoff', ['time.h']))
        defines.append(('_FILE_OFFSET_BITS', 64))
        self._defines = [d for d in defines if d is not None]

    def __getitem__(self, idx):
        self._fill()
        return self._defines[idx]

    def __len__(self):
        self._fill()
        return len(self._defines)

    def __bool__(self):
        return True

_helpers_mod = Extension('bup._helpers',
                         sources=[
                             'lib/bup/_helpers.c',
                             'lib/bup/bupsplit.c',
                             'lib/bup/_hashsplit.c'
                         ],
                         depends=[
                             'bupsplit.h',
                             '_hashsplit.h'
                         ],
                         define_macros=Defines(),
                         extra_compile_args=[
                             '-Wall',
                             '-Werror',
                             '-Wno-unknown-pragmas'
                         ])

setup(
    name='bup',
    version='0.4',
    python_requires='>=2.7,>3.4',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'pyxattr',
        'pylibacl',
        'libnacl',
    ],
    scripts=[
        "cmd/bup",
    ],
    ext_modules=[_helpers_mod],
)
