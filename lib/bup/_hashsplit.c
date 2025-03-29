#define _LARGEFILE64_SOURCE 1
#define PY_SSIZE_T_CLEAN 1
#undef NDEBUG
#include "../../config/config.h"

// According to Python, its header has to go first:
//   http://docs.python.org/2/c-api/intro.html#include-files
#include <Python.h>

#include <assert.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>

#ifdef HAVE_SYS_MMAN_H
#include <sys/mman.h>
#endif
#ifdef HAVE_SYS_TYPES_H
#include <sys/types.h>
#endif
#ifdef HAVE_SYS_STAT_H
#include <sys/stat.h>
#endif
#ifdef HAVE_UNISTD_H
#include <unistd.h>
#endif

#include "_hashsplit.h"
#include "bup/intprops.h"
#include "bup/pyutil.h"
#include "bupsplit.h"

#if defined(FS_IOC_GETFLAGS) && defined(FS_IOC_SETFLAGS)
#define BUP_HAVE_FILE_ATTRS 1
#endif

#if defined(BUP_MINCORE_BUF_TYPE) && \
    defined(POSIX_FADV_DONTNEED)
#define HASHSPLITTER_ADVISE
#ifdef BUP_HAVE_MINCORE_INCORE
#define HASHSPLITTER_MINCORE_INCORE MINCORE_INCORE
#else
// ./configure ensures that we're on Linux if MINCORE_INCORE isn't defined.
#define HASHSPLITTER_MINCORE_INCORE 1
#endif
#endif

#define min(_a, _b) (((_a) < (_b)) ? (_a) : (_b))

static size_t page_size;
static size_t fmincore_chunk_size;
static size_t advise_chunk;  // checkme
static size_t max_bits;

// FIXME: make sure the object has a good repr, including the fobj, etc.

enum bup_splitter_mode {
    SPLIT_MODE_LEGACY,
    SPLIT_MODE_FASTCDC,
};

/*
 * A HashSplitter is fed a file-like object and will determine
 * how the accumulated record stream should be split.
 */
typedef struct {
    PyObject_HEAD
    PyObject *files, *fobj;
    unsigned int bits;
    long filenum;
    size_t max_blob;
    int fd;
    enum bup_splitter_mode mode;
    PyObject *buf, *progress;
    size_t bufsz; // invariant: value must fit in a Py_ssize_t
    int eof;
    size_t start, end;
    int boundaries;
    unsigned int fanbits;
#ifdef HASHSPLITTER_ADVISE
    BUP_MINCORE_BUF_TYPE *mincore;
    size_t uncached, read;
#endif
} HashSplitter;

static void HashSplitter_unref(HashSplitter *self)
{
    Py_XDECREF(self->files);
    self->files = NULL;
    Py_XDECREF(self->fobj);
    self->fobj = NULL;
    Py_XDECREF(self->buf);
    self->buf = NULL;
    Py_XDECREF(self->progress);
    self->progress = NULL;
#ifdef HASHSPLITTER_ADVISE
    free(self->mincore);
    self->mincore = NULL;
#endif
}

static int HashSplitter_realloc(HashSplitter *self)
{
    // Allocate a new buffer and copy any unread content into it.
    PyObject *buf = PyBytes_FromStringAndSize(NULL, self->bufsz);
    PyObject *oldbuf = self->buf;

    if (!buf) {
        PyErr_Format(PyExc_MemoryError,
                     "cannot allocate %zd byte HashSplittter buffer",
                     self->bufsz);
        return -1;
    }

    self->buf = buf;

    if (oldbuf) {
        assert(self->end >= self->start);
        assert(self->end <= self->bufsz);
        memcpy(PyBytes_AS_STRING(self->buf),
               PyBytes_AS_STRING(oldbuf) + self->start,
               self->end - self->start);
        self->end -= self->start;
        self->start = 0;
        Py_DECREF(oldbuf);
    }

    return 0;
}

static PyObject *unsupported_operation_ex;

static int HashSplitter_nextfile(HashSplitter *self)
{
#ifdef HASHSPLITTER_ADVISE
    self->uncached = 0;
    self->read = 0;

    free(self->mincore);
    self->mincore = NULL;
#endif

    Py_XDECREF(self->fobj);

    /* grab the next file */
    if (!INT_ADD_OK(self->filenum, 1, &self->filenum)) {
        PyErr_SetString(PyExc_OverflowError, "hashsplitter file count overflowed");
        return -1;
    }
    self->fobj = PyIter_Next(self->files);
    if (!self->fobj) {
        if (PyErr_Occurred())
            return -1;
        return 0;
    }

    if (self->progress) {
        // CAUTION: Py_XDECREF evaluates its argument twice!
        PyObject *o = PyObject_CallFunction(self->progress, "li", self->filenum, 0);
        Py_XDECREF(o);
    }

    self->eof = 0;

    self->fd = PyObject_AsFileDescriptor(self->fobj);
    if (self->fd == -1) {
        if (PyErr_ExceptionMatches(PyExc_AttributeError)
            || PyErr_ExceptionMatches(PyExc_TypeError)
            || PyErr_ExceptionMatches(unsupported_operation_ex)) {
            PyErr_Clear();
            return 0;
        }
        return -1;
    }

#ifdef HASHSPLITTER_ADVISE
    struct stat s;
    if (fstat(self->fd, &s) < 0) {
        PyErr_Format(PyExc_IOError, "%R fstat failed: %s",
                     self->fobj, strerror(errno));
        return -1;
    }

    size_t pages;
    if (!INT_ADD_OK(s.st_size, page_size - 1, &pages)) {
        PyErr_Format(PyExc_OverflowError,
                     "%R.fileno() is too large to compute page count",
                     self->fobj);
        return -1;
    }
    pages /= page_size;

    BUP_MINCORE_BUF_TYPE *mcore = malloc(pages);
    if (!mcore) {
        PyErr_Format(PyExc_MemoryError, "cannot allocate %zd byte mincore buffer",
                     pages);
        return -1;
    }

    PyThreadState *thread_state = PyEval_SaveThread();
    off_t pos = 0;
    size_t outoffs = 0;
    while (pos < s.st_size) {
        /* mmap in chunks and fill mcore */
        size_t len = s.st_size - pos;
        if (len > fmincore_chunk_size)
            len = fmincore_chunk_size;

        int rc = 0;
        unsigned char *addr = mmap(NULL, len, PROT_NONE, MAP_PRIVATE, self->fd, pos);
        if (addr == MAP_FAILED) {
            free(mcore);
            PyEval_RestoreThread(thread_state);

            if (errno == EINVAL || errno == ENODEV)
                // Perhaps the file was a pipe, i.e. "... | bup split ..."
                return 0;

            PyErr_SetFromErrno(PyExc_IOError);
            return -1;
        }

        rc = mincore(addr, len, mcore + outoffs);

        if (rc < 0) {
            const int mc_err = errno;
            free(mcore);

            int mu_err = 0;
            if (munmap(addr, len) != 0)
                mu_err = errno;

            PyEval_RestoreThread(thread_state);

            // FIXME: chain exceptions someday
            if (mc_err == ENOSYS) {
                if (!mu_err)
                    return 0;
                errno = mu_err;
            } else {
                perror("error: munmap failed after mincore failure");
                errno = mc_err;
            }

            PyErr_SetFromErrno(PyExc_IOError);
            return -1;
        }
        if (munmap(addr, len)) {
            free(mcore);
            PyEval_RestoreThread(thread_state);
            PyErr_SetFromErrno(PyExc_IOError);
            return -1;
        }
        if (!INT_ADD_OK(pos, fmincore_chunk_size, &pos)) {
            free(mcore);
            PyEval_RestoreThread(thread_state);
            PyErr_Format(PyExc_OverflowError, "%R mincore position overflowed",
                         self->fobj);
            return -1;
        }
        if (!INT_ADD_OK(outoffs, fmincore_chunk_size / page_size, &outoffs)) {
            free(mcore);
            PyEval_RestoreThread(thread_state);
            PyErr_Format(PyExc_OverflowError, "%R mincore offset overflowed",
                         self->fobj);
            return -1;
        }
    }
    PyEval_RestoreThread(thread_state);
    self->mincore = mcore;
#endif
    return 0;
}

static int bits_from_py_kw(unsigned int *bits, PyObject *py, const char *where)
{
    if(!py) {
        PyErr_Format(PyExc_ValueError, "bits must be in [13, %d])", max_bits);
        return 0;
    }

    unsigned int b;
    if(!bup_uint_from_py(&b, py, where))
        return 0;

    if (b < 13 || b > max_bits) {
        PyErr_Format(PyExc_ValueError, "bits must be in [13, %d], not %u",
                     max_bits, b);
        return 0;
    }

    *bits = b;
    return 1;
}

static int HashSplitter_init(HashSplitter *self, PyObject *args, PyObject *kwds)
{
    self->files = NULL;
    self->fobj = NULL;
    self->filenum = -1;
    self->buf = NULL;
    self->progress = NULL;
    self->start = 0;
    self->end = 0;
    self->boundaries = 1;
    self->fanbits = 4;
#ifdef HASHSPLITTER_ADVISE
    self->mincore = NULL;
    self->uncached = 0;
    self->read = 0;
#endif

    static char *argnames[] = {
        "files",
        "bits",
        "progress",
        "keep_boundaries",
        "fanbits",
        "mode",
        NULL
    };
    char *mode;
    PyObject *files = NULL, *py_bits = NULL, *py_fanbits = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO|OpOz", argnames,
                                     &files, &py_bits,
                                     &self->progress, &self->boundaries,
                                     &py_fanbits, &mode))
        goto error;

    self->files = PyObject_GetIter(files);
    if (!self->files)
        goto error;

    /* simplify later checks */
    if (!self->progress || self->progress == Py_None)
        self->progress = NULL;
    else
        Py_INCREF(self->progress);

    if (!bits_from_py_kw(&self->bits, py_bits, "HashSplitter(bits)"))
        goto error;

    if(py_fanbits && !bup_uint_from_py(&self->fanbits, py_fanbits,
                                       "HashSplitter(fanbits)"))
        goto error;
    if (!self->fanbits) {
        PyErr_Format(PyExc_ValueError, "fanbits must be non-zero");
        goto error;
    }

    if (!mode || !strcmp(mode, "legacy")) {
        self->mode = SPLIT_MODE_LEGACY;
    } else if (!strcmp(mode, "fastcdc")) {
        self->mode = SPLIT_MODE_FASTCDC;
    } else {
        PyErr_Format(PyExc_ValueError, "invalid mode");
        goto error;
    }

    if (self->bits >= (log2(sizeof(self->max_blob)) * 8) - 2) {
        PyErr_Format(PyExc_ValueError, "bits value is too large");
        goto error;
    }
    self->max_blob = 1 << (self->bits + 2);

    self->bufsz = advise_chunk;

    if (HashSplitter_realloc(self))
        goto error;

    if (HashSplitter_nextfile(self))
        goto error;

    return 0;
error:
    HashSplitter_unref(self);
    return -1;
}

static PyObject *HashSplitter_iter(PyObject *self)
{
    Py_INCREF(self);
    return self;
}

#ifdef HASHSPLITTER_ADVISE

static int bup_py_fadvise(int fd, off_t offset, off_t len, int advice)
{
    const int rc = posix_fadvise(fd, offset, len, advice);
    PyObject *py_err = NULL;
    switch (rc) {
    case 0:
    case ESPIPE:
        return 1; // ignore
    case EBADF:
        py_err = PyExc_IOError;
        break;
    case EINVAL:
        py_err = PyExc_ValueError;
        break;
    default:
        py_err = PyExc_OSError;
        break;
    }
    int errn = errno;
    PyErr_SetFromErrno(py_err);
    errno = errn;
    return 0;
}

static int HashSplitter_uncache(HashSplitter *self, int last)
{
    if (!self->mincore)
        return 0;

    assert(self->uncached <= self->read);
    size_t len = self->read - self->uncached;
    if (!last) {
        len /= advise_chunk;
        len *= advise_chunk;
    }
    size_t pages = len / page_size;

    // now track where and how much to uncache
    off_t start = self->uncached; // see assumptions (size_t <= off_t)

    // Check against overflow up front
    size_t pgstart = self->uncached / page_size;
    {
        size_t tmp;
        if (!INT_ADD_OK(pgstart, pages, &tmp)) {
            PyErr_Format(PyExc_OverflowError, "%R mincore offset too big for size_t",
                         self);
            return -1;
        }
    }
    if (pages == SIZE_MAX) {
        PyErr_Format(PyExc_OverflowError, "can't handle SIZE_MAX page count for %R",
                     self);
        return -1;
    }
    size_t i;
    for (i = 0, len = 0; i < pages; i++) {
        // We check that page_size fits in an off_t elsewhere, at startup
        if (self->mincore[pgstart + i] & HASHSPLITTER_MINCORE_INCORE) {
            if (len) {
                if(!bup_py_fadvise(self->fd, start, len, POSIX_FADV_DONTNEED))
                    return -1;
            }
            start += len + page_size;
            len = 0;
        } else {
            len += page_size;
        }
    }
    if (len) {
        if(!bup_py_fadvise(self->fd, start, len, POSIX_FADV_DONTNEED))
            return -1;
    }

    if (!INT_ADD_OK(start, len, &self->uncached)) {
        PyErr_Format(PyExc_OverflowError, "%R mincore uncached size too big for size_t",
                     self);
        return -1;
    }
    return 0;
}
#endif /* defined HASHSPLITTER_ADVISE */

static int HashSplitter_read(HashSplitter *self)
{
    if (!self->fobj)
        return 0;

    assert(self->start <= self->end);
    assert(self->end <= self->bufsz);

    Py_ssize_t len = 0, start_read = self->end;
    if (self->fd != -1) {
        /* this better be the common case ... */
        do {
            Py_BEGIN_ALLOW_THREADS;
            len = read(self->fd,
                       PyBytes_AS_STRING(self->buf) + self->end,
                       self->bufsz - self->end);
            Py_END_ALLOW_THREADS;

            if (len < 0) {
                PyErr_SetFromErrno(PyExc_IOError);
                return -1;
            }

            self->end += len;

#ifdef HASHSPLITTER_ADVISE
            if (!INT_ADD_OK(self->read, len, &self->read)) {
                PyErr_Format(PyExc_OverflowError, "%R mincore read count overflowed",
                             self);
                return -1;
            }

            assert(self->uncached <= self->read);
            if (len == 0
                && self->read > self->uncached
                && self->read - self->uncached >= advise_chunk) {
                if(HashSplitter_uncache(self, len == 0))
                    return -1;
            }
#endif
        } while (len /* not eof */ &&
                 self->bufsz > self->end);
    } else {
        do {
            assert(self->bufsz >= self->end);
            assert(self->bufsz - self->end <= PY_SSIZE_T_MAX);
            PyObject *r = PyObject_CallMethod(self->fobj, "read", "n",
                                              self->bufsz - self->end);
            if (!r)
                return -1;

            Py_buffer buf;
            if (PyObject_GetBuffer(r, &buf, PyBUF_FULL_RO)) {
                Py_DECREF(r);
                return -1;
            }

            len = buf.len;
            assert(len >= 0);
            // see assumptions (Py_ssize_t <= size_t)
            if ((size_t) len > self->bufsz - self->end) {
                PyErr_Format(PyExc_ValueError, "read(%d) returned %zd bytes",
                             self->bufsz - self->end, len);
                PyBuffer_Release(&buf);
                Py_DECREF(r);
                return -1;
            }
            if (len)
                assert(!PyBuffer_ToContiguous(PyBytes_AS_STRING(self->buf) + self->end,
                                              &buf, len, 'C'));
            PyBuffer_Release(&buf);
            Py_DECREF(r);

            self->end += len;
        } while (len /* not eof */ &&
                 self->bufsz > self->end);
    }

    if (self->progress && self->end - start_read) {
        PyObject *o = PyObject_CallFunction(self->progress, "ln",
                                            self->filenum,
                                            self->end - start_read);
        if (o == NULL)
            return -1;
        Py_DECREF(o);
    }

    return len;
}

static inline size_t HashSplitter_roll(Rollsum *r, unsigned int nbits,
                                       const unsigned char *buf, const size_t len,
                                       unsigned int *extrabits)
{
    // Return the buff offset of the next split point for a rollsum
    // watching the least significant nbits.  Set extrabits to the
    // count of contiguous one bits that are more significant than the
    // lest significant nbits and the next most significant bit (which
    // is ignored).

    assert(nbits <= 32);

    PyThreadState *thread_state = PyEval_SaveThread();

    // Compute masks for the two 16-bit rollsum components such that
    // (s1_* | s2_*) is the mask for the entire 32-bit value.  The
    // least significant nbits of the complete mask will be all ones.
    const uint16_t s2_mask = (1 << nbits) - 1;
    const uint16_t s1_mask = (nbits <= 16) ? 0 : (1 << (nbits - 16)) - 1;

    size_t count;
    for (count = 0; count < len; count++) {
        rollsum_roll(r, buf[count]);

        if ((r->s2 & s2_mask) == s2_mask && (r->s1 & s1_mask) == s1_mask) {
            uint32_t rsum = rollsum_digest(r);

            rsum >>= nbits;
            /*
             * See the DESIGN document, the bit counting loop used to
             * be written in a way that shifted rsum *before* checking
             * the lowest bit, make that explicit now so the code is a
             * bit easier to understand.
             */
            rsum >>= 1;
            *extrabits = 0;
            while (rsum & 1) {
                (*extrabits)++;
                rsum >>= 1;
            }

            PyEval_RestoreThread(thread_state);
            assert(count < len);
            return count + 1;
        }
    }
    PyEval_RestoreThread(thread_state);
    return 0;
}

static inline int split_found(uint32_t v, unsigned int nbits,
                              unsigned int *extrabits)
{
    /* compiler should lift this out/up */
    const uint32_t mask = (1 << nbits) - 1;

    /* empirically, this masking is faster than __builtin_ctz() */
    if ((v & mask) == mask) {
        v >>= nbits;
        /*
         * See the DESIGN document, the bit counting loop used to
         * be written in a way that shifted rsum *before* checking
         * the lowest bit, make that explicit now so the code is a
         * bit easier to understand.
         */
        v >>= 1;
#if defined(__has_builtin)
#if   __has_builtin(__builtin_ctz)
#define USE_BUILTIN_CTZ 1
#endif
#endif
#ifdef USE_BUILTIN_CTZ
        *extrabits = __builtin_ctz(~v);
#else
        *extrabits = 0;
        while (v & 1) {
            (*extrabits)++;
            v >>= 1;
        }
#endif
#undef USE_BUILTIN_CTZ
        return 1;
    }
    return 0;
}

static size_t
HashSplitter_find_offs_legacy(unsigned int nbits,
                              const unsigned char *buf,
                              const size_t len,
                              unsigned int *extrabits)
{
    struct {
        uint32_t s1, s2;
    } state = {
        .s1 = (BUP_WINDOWSIZE * ROLLSUM_CHAR_OFFSET),
        .s2 = (BUP_WINDOWSIZE * (BUP_WINDOWSIZE-1) * ROLLSUM_CHAR_OFFSET),
    };

    /* first part without any dropped bytes */
    for (size_t pos = 0; pos < BUP_WINDOWSIZE; pos++) {
        uint32_t s;
        uint8_t add = buf[pos];
        uint8_t drop = 0;

        state.s1 += add - drop;
        state.s2 += state.s1 - BUP_WINDOWSIZE * (drop + ROLLSUM_CHAR_OFFSET);

        s = (state.s1 << 16) | (state.s2 & 0xffff);
        if (split_found(s, nbits, extrabits))
            return pos + 1;
    }

    /* main loop with dropping from behind */
    for (size_t pos = BUP_WINDOWSIZE; pos < len; pos++) {
        uint32_t s;
        uint8_t add = buf[pos];
        uint8_t drop = buf[pos - BUP_WINDOWSIZE];

        state.s1 += add - drop;
        state.s2 += state.s1 - BUP_WINDOWSIZE * (drop + ROLLSUM_CHAR_OFFSET);

        s = (state.s1 << 16) | (state.s2 & 0xffff);
        if (split_found(s, nbits, extrabits))
            return pos + 1;
    }

    return 0;
}

static size_t
HashSplitter_find_offs_fastcdc(unsigned int nbits,
                               const unsigned char *buf,
                               const size_t len,
                               unsigned int *extrabits)
{
    /* https://github.dev/UWASL/dedup-bench/blob/main/dedup/src/chunking/fastcdc.cpp */
    static const uint64_t GEAR_TABLE[256] = {
        0x651748f5a15f8222, 0xd6eda276c877d8ea, 0x66896ef9591b326b,
        0xcd97506b21370a12, 0x8c9c5c9acbeb2a05, 0xb8b9553ee17665ef,
        0x1784a989315b1de6, 0x947666c9c50df4bd, 0xb3f660ea7ff2d6a4,
        0xbcd6adb8d6d70eb5, 0xb0909464f9c63538, 0xe50e3e46a8e1b285,
        0x21ed7b80c0163ce0, 0xf209acd115f7b43b, 0xb8c9cb07eaf16a58,
        0xb60478aa97ba854c, 0x8fb213a0b5654c3d, 0x42e8e7bd9fb03710,
        0x737e3de60a90b54f, 0x9172885f5aa79c8b, 0x787faae7be109c36,
        0x86ad156f5274cb9f, 0x6ac0a8daa59ee1ab, 0x5e55bc229d5c618e,
        0xa54fb69a5f181d41, 0xc433d4cf44d8e974, 0xd9efe85b722e48a3,
        0x7a5e64f9ea3d9759, 0xba3771e13186015d, 0x5d468c5fad6ef629,
        0x96b1af02152ebfde, 0x63706f4aa70e0111, 0xe7a9169252de4749,
        0xf548d62570bc8329, 0xee639a9117e8c946, 0xd31b0f46f3ff6847,
        0xfed7938495624fc5, 0x1ef2271c5a28122e, 0x7fd8e0e95eac73ef,
        0x920558e0ee131d4c, 0xce2e67cb1034bcd1, 0x6f4b338d34b004ae,
        0x92f5e7271cf95c9a, 0x12e1305a9c558342, 0x1e30d88013ad77ae,
        0x09acc1a57bbb604e, 0xaf187082c6f56192, 0xd2e5d987f04ac6f0,
        0x3b22fca40423da70, 0x7dfba8ce699a9a87, 0xe8b15f90ea96bd2a,
        0xcda1a1089cc2cbe7, 0x72f70448459de898, 0x1ab992dbb61cd46e,
        0x912ad04becbb29da, 0x98c6bb3aa3ce09ed, 0x6373bd2e7a041f3a,
        0x1f98f28bd178c53a, 0xe6adbc82ba5d9f96, 0x7456da7d805cbe01,
        0xd673662dcc135eeb, 0xb299e26eaadcb311, 0x2c2582172f8114af,
        0xeded114d7f623da6, 0xb3462a0e623276e4, 0x3af752be3d34bfaa,
        0x1311ccc0a1855a89, 0x0812bbcecc92b2e4, 0x9974b5747289f2f5,
        0x3a030eff770f2026, 0x52462b2aa42a847a, 0x2beaa107d15a012b,
        0x0c0035e0fe073398, 0x4f2f9de2ac206766, 0x5dd51a617c291deb,
        0x1ac66905652cc03b, 0x11067b0947fc07a1, 0x02b5fcd96ad06d52,
        0x74244ec1aa2821fd, 0xf6089e32060e9439, 0xd8f076a33bcbf1a7,
        0x5162743c755d8d5e, 0x8d34fc683e4e3d06, 0x46efe9b21a0252a3,
        0x4631e8d0109c6145, 0xfdf7a14bc0223957, 0x750934b3d0b8bb1e,
        0x2ecd1b3efed5ddb9, 0x2bcbd89a83ccfbce, 0x3507c79e58dd5886,
        0x5476a67ecd4a772f, 0xaa0be3856dd76405, 0x22289a358a4dd421,
        0xf570433f14503ad1, 0x8a9f440251a722c3, 0x77dd711752b4398c,
        0xbbd9edf9c6160a31, 0xb94b59220b23f079, 0xfdca3d75d2f33ccf,
        0xb29452c460c9e977, 0xe89afe2dd4bf3b02, 0x47ec6f32c91bfee4,
        0x1aab5ec3445706b8, 0x588bf4fa55334006, 0xe2290ca1e29acd96,
        0x3c49e189f831c37c, 0x6448c973b5177498, 0x556a6e09ba158de7,
        0x90b25013a8d9a067, 0xa4f2f7a50c58e1c4, 0x5e765e871008700e,
        0x242f5ae7738327af, 0xc1e6a2819cc5a219, 0xcb48d801fd6a5449,
        0xa208de2301931383, 0xde3c143fe44e39b0, 0x6bb74b09c73e4133,
        0xb5b1ed1b63d54c11, 0x587567d454ce7716, 0xf47ddbc987cb0392,
        0x87b19254448f03f1, 0x985fd00ec372fafa, 0x64b92ba521aa46e4,
        0xce63f4013d587b0f, 0xa691ae698726030e, 0xeaefbf690264e9aa,
        0x68edd400523eb152, 0x35d9353aa1957c60, 0x2e2c2d7a9cb68385,
        0xfc7549edaf43bf9e, 0x48b2adb23026e2c7, 0x3777cb79a024bcf9,
        0x644128f7c184102d, 0x70189d3ca4390de9, 0x085fea7986d4cd34,
        0x6dbe7626c8457464, 0x9fa41cfa9c4265eb, 0xdaa163a641946463,
        0x02f5c4bd9efa2074, 0x783201871822c3c9, 0xb0dfec499202bce0,
        0x1f1c9c12d84dccab, 0x1596f8819f2ed68e, 0xb0352c3e9fc84468,
        0x24a6673db9122956, 0x84f5b9e60b274739, 0x7216b28a0b54ac46,
        0xc7789de20e9cdca4, 0x903db5d289dd6563, 0xce66a947f7033516,
        0x3677dbc62307b2ca, 0x8d8e9d5530eb46ac, 0x79c4bad281bd93e2,
        0x287d942042068c36, 0xde4b98e5464b6ad5, 0x612534b97d1d21bf,
        0xdf98659772d822a1, 0x93053df791aa6264, 0x2254a8a2d54528ba,
        0x2301164aeb69c43d, 0xf56863474ac2417f, 0x6136b73e1b75de42,
        0xc7c3bd487e06b532, 0x7232fbed1eb9be85, 0x36d60f0bd7909e43,
        0xe08cbf774a4ce1f2, 0xf75fbc0d97cb8384, 0xa5097e5af367637b,
        0x7bce2dcfa856dbb2, 0xfbfb729dd808c894, 0x3dc8eba10ad7112e,
        0xf2d1854eedce4928, 0xb705f5c1aebd2104, 0x78fa4d004417d956,
        0x9e5162660729f858, 0xda0bcd5eb9f91f0e, 0x748d1be11e06b362,
        0xf4c2be9a04547734, 0x6f2bcd7c88abdf9a, 0x50865dafdfd8a404,
        0x9d820665691728f0, 0x59fe7a56aa07118e, 0x4df1d768c23660ec,
        0xab6310b8edfb8c5e, 0x029b47623fc9ffe4, 0x50c2cca231374860,
        0x0561505a8dbbdc69, 0x8d07fe136de385f3, 0xc7fb6bb1731b1c1c,
        0x2496d1256f1fac7a, 0x79508cee90d84273, 0x09f51a2108676501,
        0x2ef72d3dc6a50061, 0xe4ad98f5792dd6d6, 0x69fa05e609ae7d33,
        0xf7f30a8b9ae54285, 0x04a2cb6a0744764b, 0xc4b0762f39679435,
        0x60401bc93ef6047b, 0x76f6aa76e23dbe0c, 0x8a209197811e39da,
        0x4489a9683fa03888, 0x2604ad5741a6f8d8, 0x7faa9e0c64a94532,
        0x0dbfee8cdae8f54e, 0x0a7c5885f0b76d4a, 0x55dfb1ac12e83645,
        0xedc967651c4938cc, 0x4e006ab71a48b85e, 0x193f621602de413c,
        0xb56458b71d56944f, 0xf2b639509a2fa5da, 0xb4a76f284c365450,
        0x4d3b65d2d2ae22f7, 0xbcc5f8303efca485, 0x8a044f312671aaea,
        0x688d69e89af0f57a, 0x229957dc1facede8, 0x2ed75c321073da13,
        0xf199e7ece5fcefef, 0x50c85b5c837a6c64, 0x71703c6e676bf698,
        0xc1b4eb52b1e5a518, 0x0f46a5e6c9cb68ca, 0xebb933688d69d7f7,
        0x5ab7404b8d1e3ef4, 0x261acc20c5a64a90, 0xb88788798adc718a,
        0x3e44e9b6bad5bc15, 0xf6bb456f086346bc, 0xd66e17e5734cbde1,
        0x392036dae96e389d, 0x4a62ceac9d4202de, 0x9d55f412f32e5f6e,
        0x0e1d841509d9ee9d, 0xc3130bdc638ed9e2, 0x0cd0e82af24964d9,
        0x3ec4c59463ba9b50, 0x055bc4d8685ab1bc, 0xb9e343c96a3a4253,
        0x8eba190d8688f7f9, 0xd31df36c792c629b, 0xddf82f659b127104,
        0x6f12dc8ba930fbb7, 0xa0aee6bb7e81a7f0, 0x8c6ba78747ae8777,
        0x86f00167eda1f9bc, 0x3a6f8b8f8a3790c9, 0x7845bb4a1c3bfbbb,
        0xc875ab077f66cf23, 0xa68b83d8d69b97ee, 0xb967199139f9a0a6,
        0x8a3a1a4d3de036b7, 0xdf3c5c0c017232a4, 0x8e60e63156990620,
        0xd31b4b03145f02fa
    };
    uint64_t fp = 0;
    size_t i = 1 << (nbits - 2);  // skip min block size
    const uint64_t tmask_c = 0x575d590003570000; // 21 bits set - max nbits
    const uint64_t tmask_j = 0x575d590003560000;

    unsigned int tmp = nbits, pos = 0;
    while (tmp) {
        if (tmask_c & (1ULL << pos))
           tmp--;
        pos += 1;
    }
    // don't need mask_c since the below uses tmask_c-tmask_j
    uint64_t mask_j = tmask_j & ~(~0ULL << pos);

    uint64_t jump_length = 585; // j=9

    if (len <= i) {
        *extrabits = 0;
        return len;
    }

    for (; i < len; i++) {
        fp = (fp << 1) + GEAR_TABLE[(int)buf[i]];
        if ((fp & mask_j) == 0) {
            if ((fp & (tmask_c - tmask_j)) == 0) { // eqivalent to "& mask_c"
                *extrabits = __builtin_popcountll(~(fp & tmask_c)) - nbits;
                return i;
            }
            i += jump_length;
            if (i > len)
                return 0;
        }
    }

    return 0;
}

static PyObject *HashSplitter_iternext(HashSplitter *self)
{
    unsigned int nbits = self->bits;

    while (1) {
        assert(self->end >= self->start);
        const unsigned char *buf;

        /* read some data if possible/needed */
        if (self->end < self->bufsz && self->fobj) {
            if (self->eof && (!self->boundaries || self->start == self->end))
                HashSplitter_nextfile(self);

            int rc = HashSplitter_read(self);
            if (rc < 0)
                return NULL;
            if (rc == 0)
                self->eof = 1;
        }

        /* check first if we've completed */
        if (self->start == self->end && !self->fobj) {
            /* quick free - not really required */
            Py_DECREF(self->buf);
            self->buf = NULL;
            return NULL;
        }

        buf = (void *)PyBytes_AS_STRING(self->buf);
        const size_t maxlen = min(self->end - self->start, self->max_blob);

        unsigned int extrabits;
        size_t ofs;
        switch (self->mode) {
        case SPLIT_MODE_LEGACY:
            ofs = HashSplitter_find_offs_legacy(nbits, buf + self->start,
                                                maxlen, &extrabits);
            break;
        case SPLIT_MODE_FASTCDC:
            ofs = HashSplitter_find_offs_fastcdc(nbits, buf + self->start,
                                                 maxlen, &extrabits);
            break;
        default:
            assert(0);
        }

        unsigned int level;
        if (ofs) {
            level = extrabits / self->fanbits;
        } else if (self->end - self->start >= self->max_blob) {
            ofs = self->max_blob;
            level = 0;
        } else if (self->start != self->end &&
                   self->eof && (self->boundaries || !self->fobj)) {
            ofs = self->end - self->start;
            level = 0;
        } else {
            /*
             * We've not found a split point, not been able to split
             * due to a max blob, nor reached EOF - new buffer needed.
             */
            if (HashSplitter_realloc(self))
                return NULL;
            continue;
        }
        assert(self->end - self->start >= ofs);

        /* return the found chunk as a buffer view into the total */
        PyObject *mview = PyMemoryView_FromObject(self->buf);
        PyObject *ret = PySequence_GetSlice(mview, self->start, self->start + ofs);
        Py_DECREF(mview);
        self->start += ofs;
        PyObject *result = Py_BuildValue("Ni", ret, level);
        if (result == NULL) {
            Py_DECREF(ret);
            return NULL;
        }
        return result;
    }
}

static void HashSplitter_dealloc(HashSplitter *self)
{
    HashSplitter_unref(self);
    PyObject_Del(self);
}

PyTypeObject HashSplitterType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "_helpers.HashSplitter",
    .tp_doc = "Stateful hashsplitter",
    .tp_basicsize = sizeof(HashSplitter),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)HashSplitter_init,
    .tp_iter = HashSplitter_iter,
    .tp_iternext = (iternextfunc)HashSplitter_iternext,
    .tp_dealloc = (destructor)HashSplitter_dealloc,
};

/*
 * A RecordHashSplitter is fed records one-by-one, and will determine
 * if the accumulated record stream should now be split. Once it does
 * return a split point, it resets to restart at the next record.
 */
typedef struct {
    PyObject_HEAD
    Rollsum r;
    unsigned int bits;
    size_t split_size;  // bytes
    size_t max_split_size;
    enum bup_splitter_mode mode;
} RecordHashSplitter;

static int RecordHashSplitter_init(RecordHashSplitter *self, PyObject *args, PyObject *kwds)
{
    static char *argnames[] = { "bits", "mode", NULL };
    char *mode;
    self->split_size = 0;
    rollsum_init(&self->r);

    PyObject *py_bits = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|$Oz", argnames, &py_bits, &mode))
        return -1;

    if (!bits_from_py_kw(&self->bits, py_bits, "RecordHashSplitter(bits)"))
        return -1;

    if (!mode || !strcmp(mode, "legacy")) {
        self->mode = SPLIT_MODE_LEGACY;
// TODO
//    } else if (!strcmp(mode, "fastcdc")) {
//        self->mode = SPLIT_MODE_FASTCDC;
    } else {
        PyErr_Format(PyExc_ValueError, "invalid mode");
        return -1;
    }

    // Same as the file splitter's max_blob
    if (self->bits >= (log2(sizeof(self->max_split_size)) * 8) - 2) {
        PyErr_Format(PyExc_ValueError, "bits value is too large");
        return -1;
    }
    self->max_split_size = 1 << (self->bits + 2);

    return 0;
}

static void reset_recordsplitter(RecordHashSplitter *splitter)
{
    rollsum_init(&splitter->r);
    splitter->split_size = 0;
}

static PyObject *RecordHashSplitter_feed(RecordHashSplitter *self, PyObject *args)
{
    Py_buffer buf = { .buf = NULL, .len = 0 };
    if (!PyArg_ParseTuple(args, "y*", &buf))
        return NULL;

    unsigned int extrabits = 0;
    const size_t out = HashSplitter_roll(&self->r, self->bits, buf.buf, buf.len,
                                         &extrabits);

    PyBuffer_Release(&buf);

    unsigned long bits;
    if(!INT_ADD_OK(extrabits, self->bits, &bits))
    {
        PyErr_Format(PyExc_OverflowError, "feed() result too large");
        return NULL;
    }

    if (out)  // split - reinitalize for next split
        reset_recordsplitter(self);

    if(!INT_ADD_OK(self->split_size, buf.len, &self->split_size)) {
        PyErr_Format(PyExc_OverflowError, "feed() data overflows split size");
        return NULL;
    }

    const int force_split = self->split_size > self->max_split_size;
    if (force_split)
        reset_recordsplitter(self);

    return Py_BuildValue("OO",
                         (out || force_split) ? Py_True : Py_False,
                         out ? BUP_LONGISH_TO_PY(bits) : Py_None);
}

static PyMethodDef RecordHashSplitter_methods[] = {
    {"feed", (PyCFunction)RecordHashSplitter_feed, METH_VARARGS,
     "Feed a record into the RecordHashSplitter instance and return a tuple (split, bits).\n"
     "Return (True, bits) if a split point is found, (False, None) otherwise."
    },
    {NULL}  /* Sentinel */
};

PyTypeObject RecordHashSplitterType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "_helpers.RecordHashSplitter",
    .tp_doc = "Stateful hashsplitter",
    .tp_basicsize = sizeof(RecordHashSplitter),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)RecordHashSplitter_init,
    .tp_methods = RecordHashSplitter_methods,
};

int hashsplit_init(void)
{
    // Assumptions the rest of the code can depend on.
    assert(sizeof(Py_ssize_t) <= sizeof(size_t));
    assert(sizeof(size_t) <= sizeof(off_t));
    assert(CHAR_BIT == 8);
    assert(sizeof(Py_ssize_t) <= sizeof(size_t));

    {
        PyObject *io = PyImport_ImportModule("io");
        if (!io)
            return -1;
        PyObject *ex = PyObject_GetAttrString(io, "UnsupportedOperation");
        Py_DECREF(io);
        if (!ex)
            return -1;
        unsupported_operation_ex = ex;
    }

    const long sc_page_size = sysconf(_SC_PAGESIZE);
    if (sc_page_size < 0) {
        if (errno == EINVAL)
            PyErr_SetFromErrno(PyExc_ValueError);
        else
            PyErr_SetFromErrno(PyExc_OSError);
        return -1;
    }
    if (sc_page_size == 0) {
        PyErr_Format(PyExc_Exception, "sysconf returned 0 _SC_PAGESIZE");
        return -1;
    }
    if (!INT_ADD_OK(sc_page_size, 0, &page_size)) {
        PyErr_Format(PyExc_OverflowError, "page size too large for size_t");
        return -1;
    }
    off_t tmp_off;
    if (!INT_ADD_OK(page_size, 0, &tmp_off)) {
        PyErr_Format(PyExc_OverflowError, "page size too large for off_t");
        return -1;
    }

    const size_t pref_chunk_size = 64 * 1024 * 1024;
    fmincore_chunk_size = page_size;
    if (fmincore_chunk_size < pref_chunk_size) {
        if (!INT_MULTIPLY_OK(page_size, (pref_chunk_size / page_size),
                             &fmincore_chunk_size)) {
            PyErr_Format(PyExc_OverflowError, "fmincore page size too large for size_t");
            return -1;
        }
    }

    advise_chunk = 8 * 1024 * 1024;
    /*
     * We read in advise_chunk blocks too, so max_blob cannot be
     * bigger than that, but max_blob is 4 << bits, so calculate
     * max_bits that way.
     */

    max_bits = log2(advise_chunk) - 2;

    if (page_size > advise_chunk)
        advise_chunk = page_size;

    if (advise_chunk > PY_SSIZE_T_MAX) {
        PyErr_Format(PyExc_OverflowError,
                     "hashsplitter advise buffer too large for ssize_t");
        return -1;
    }

    if (PyType_Ready(&HashSplitterType) < 0)
        return -1;

    if (PyType_Ready(&RecordHashSplitterType) < 0)
        return -1;

    return 0;
}
