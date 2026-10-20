
from binascii import hexlify
from contextlib import ExitStack
import glob, os, math, resource, struct, sys

from bup import options, git, midx, _helpers, xstat
from bup.compat import argv_bytes
from bup.helpers import \
    (Sha1, Sha256,
     add_error, atomically_replaced_file,
     debug1,
     fsync,
     log,
     mmap_readwrite,
     qprogress,
     saved_errors,
     unlink)
from bup.io import byte_stream, path_msg
from bup.midx import MissingIdxs, open_midx
from bup.repo import LocalRepo


PAGE_SIZE=4096

optspec = """
bup midx [options...] <idxnames...>
--
o,output=  output midx filename (default: auto-generated)
a,auto     automatically use all existing .midx/.idx files as input
f,force    merge produce exactly one .midx containing all objects
p,print    print names of generated midx files
check      validate contents of the given midx files (with -a, all midx files)
max-files= maximum number of idx files to open at once [-1]
d,dir=     directory containing idx/midx files
sha256     the idx files are sha256 format (only with --dir)
offsets    include object offsets in the midx
"""


def _group(l, count):
    for i in range(0, len(l), count):
        yield l[i:i+count]


def _maybe_open_midx(path, hashsize, *, rm_broken=False):
    """Return a PackMidx for path as open_midx() does unless some of
    its idx files are missing or its object ids aren't hashsize bytes
    long.  In the former case, warn, delete the path if rm_broken is
    true, and return None.  In the latter case, report an error and
    return None.
    """
    missing = None
    try:
        ix = open_midx(path, ignore_missing=False)
    except MissingIdxs as ex:
        missing = ex.paths
    else:
        if ix.hashsize != hashsize:
            ix.close()
            add_error(f'{path_msg(path)}: midx object id size {ix.hashsize} '
                      f'does not match {hashsize}')
            return None
        return ix
    pathm = path_msg(path)
    # FIXME: eventually note_error instead when we're not deleting?
    for idx in missing:
        idxm = path_msg(idx)
        log(f'warning: midx {pathm} refers to mssing idx {idxm}\n')
    if rm_broken:
        log(f'Removing incomplete midx {pathm}\n')
        unlink(path)
    return None


def check_midx(name, hashsize):
    nicename = git.repo_rel(name)
    log('Checking %s.\n' % path_msg(nicename))
    try:
        ix = git.open_object_idx(name, hashsize)
    except git.GitError as e:
        add_error('%s: %s' % (path_msg(name), e))
        return
    if not ix:
        return
    with ix:
        for count,subname in enumerate(ix.idxnames):
            sub = git.open_object_idx(os.path.join(os.path.dirname(name), subname),
                                      hashsize)
            if not sub:
                continue
            with sub:
                for ecount,e in enumerate(sub):
                    if not (ecount % 1234):
                        qprogress('  %d/%d: %s %d/%d\r'
                                  % (count, len(ix.idxnames),
                                     git.shorten_hash(subname).decode('ascii'),
                                     ecount, len(sub)))
                    if not sub.exists(e):
                        add_error("%s: %s: %s missing from idx"
                                  % (path_msg(nicename),
                                     git.shorten_hash(subname).decode('ascii'),
                                     e.hex()))
                    if not ix.exists(e):
                        add_error("%s: %s: %s missing from midx"
                                  % (path_msg(nicename),
                                     git.shorten_hash(subname).decode('ascii'),
                                     e.hex()))
                    if isinstance(ix, midx.PackMidx) and ix.has_offsets:
                        mloc = ix.exists(e, want_offset=True, want_crc=True)
                        sloc = sub.exists(e, want_offset=True, want_crc=True)
                        if mloc is not None and sloc is not None \
                           and mloc.offset is not None \
                           and mloc.offset != sloc.offset:
                            add_error("%s: %s: %s offset %s in midx, %s in idx"
                                      % (path_msg(nicename),
                                         git.shorten_hash(subname).decode('ascii'),
                                         e.hex(), mloc.offset, sloc.offset))
                        if mloc is not None and sloc is not None \
                           and mloc.crc is not None \
                           and sloc.crc is not None \
                           and mloc.crc != sloc.crc & 0xff:
                            add_error("%s: %s: %s crc byte %s in midx, %s in idx"
                                      % (path_msg(nicename),
                                         git.shorten_hash(subname).decode('ascii'),
                                         e.hex(), mloc.crc, sloc.crc & 0xff))
        prev = None
        for ecount,e in enumerate(ix):
            if not (ecount % 1234):
                qprogress('  Ordering: %d/%d\r' % (ecount, len(ix)))
            if e and prev and not e >= prev:
                add_error('%s: ordering error: %s < %s'
                          % (nicename, e.hex(), prev.hex()))
            prev = e


def _open_inputs(name, hashsize, *, expand, rm_broken):
    """Return the open idx objects to merge.  With 'expand',
    an midx without offsets (v4) is ignored and the idx files
    are returned instead, to get offsets/CRCs.
    """
    if name.endswith(b'.idx'):
        yield git.open_idx(name, hashsize)
        return
    ix = _maybe_open_midx(name, hashsize, rm_broken=rm_broken)
    if not ix or not expand or ix.has_offsets:
        if ix:
            yield ix
        return
    idxnames = ix.idxnames
    ix.close()
    dirname = os.path.dirname(name)
    for n in idxnames:
        yield git.open_idx(os.path.join(dirname, n), hashsize)


def _offsets_ofs(ix):
    """Return the position of ix's offset table in its map, or 0 if
    there is none (idx v1 doesn't have one)
    """
    if isinstance(ix, midx.PackMidx):
        return ix.offsets_ofs or 0
    if isinstance(ix, git.PackIdxV2):
        return ix.ofstable_ofs
    return 0


def _crc8_src(ix):
    """Return (ofs, step) locating the low byte of each entry's CRC
    field in ix's map, or (0, 0) if there is none (idx v1).
    """
    if isinstance(ix, midx.PackMidx):
        return (ix.crc8_ofs or 0, 1)
    if isinstance(ix, git.PackIdxV2):
        # big endian storage, so + 3
        return (ix.crctable_ofs + 3, 4)
    return (0, 0)


_first = None
def _do_midx(outdir, outfilename, infilenames, prefixstr,
             hashsize, auto=False, force=False, offsets=False):
    global _first
    assert hashsize in (20, 32)
    if not outfilename:
        assert(outdir)
        if hashsize == 32:
            sumalg = Sha256
        else:
            sumalg = Sha1
        sum = hexlify(sumalg(b'\0'.join(infilenames)).digest())
        outfilename = b'%s/midx-%s.midx' % (outdir, sum)

    if hashsize == 32:
        merge_into = _helpers.merge_into_sha256
    else:
        merge_into = _helpers.merge_into

    inp = []
    total = 0
    allfilenames = []
    with ExitStack() as contexts:
        for name in infilenames:
            for ix in _open_inputs(name, hashsize, expand=offsets,
                                   rm_broken=auto or force):
                contexts.enter_context(ix)
                crc8_ofs, crc8_step = _crc8_src(ix)
                inp.append((
                    ix.map,
                    len(ix),
                    ix.sha_ofs,
                    isinstance(ix, midx.PackMidx) and ix.which_ofs or 0,
                    len(allfilenames),
                    _offsets_ofs(ix),
                    crc8_ofs,
                    crc8_step,
                ))
                for n in ix.idxnames:
                    # FIXME: double-check wrt outfilename above
                    allfilenames.append(os.path.basename(n))
                total += len(ix)
        inp.sort(reverse=True, key=lambda x: x[0][x[2] : x[2] + hashsize])

        if not _first: _first = outdir
        dirprefix = (_first != outdir) and git.repo_rel(outdir) + b': ' or b''
        debug1('midx: %s%screating from %d files (%d objects).\n'
               % (dirprefix, prefixstr, len(infilenames), total))
        if (auto and (total < 1024 and len(infilenames) < 3)) \
           or ((auto or force) and len(infilenames) < 2) \
           or (force and not total):
            debug1('midx: nothing to do.\n')
            return None

        sha_per_page = PAGE_SIZE / hashsize
        pages = total // sha_per_page or 1
        bits = int(math.ceil(math.log(pages, 2)))
        entries = 2**bits
        debug1('midx: table size: %d (%d bits)\n' % (entries*4, bits))

        unlink(outfilename)
        with atomically_replaced_file(outfilename, 'w+b') as f:
            f.write(b'MIDX')
            if offsets:
                if hashsize == 32:
                    ver = midx.MIDX_VERSION_OFFSETS_256
                else:
                    ver = midx.MIDX_VERSION_OFFSETS
            else:
                ver = midx.MIDX_VERSION_256 if hashsize == 32 else midx.MIDX_VERSION
            f.write(struct.pack('!II', ver, bits))
            assert(f.tell() == 12)

            extra_size = 5 * total if offsets else 0
            f.truncate(12 + 4 * entries + hashsize * total + 4 * total + extra_size)
            f.flush()
            fsync(f.fileno())

            with mmap_readwrite(f, close=False) as fmap:
                merge_into(fmap, bits, total, inp, bool(offsets))
            f.seek(0, os.SEEK_END)
            f.write(b'\0'.join(allfilenames))
            f.flush()
            fsync(f.fileno())

    # This is just for testing (if you enable this, don't clear inp above)
    # if 0:
    #     p = midx.open_midx(outfilename)
    #     assert(len(p.idxnames) == len(infilenames))
    #     log(repr(p.idxnames) + '\n')
    #     assert(len(p) == total)
    #     for pe, e in p, git.idxmerge(inp, final_progress=False):
    #         pin = next(pi)
    #         assert(i == pin)
    #         assert(p.exists(i))

    return total, outfilename


def do_midx(outdir, outfilename, infilenames, prefixstr, prout, hashsize,
            auto=False, force=False, print_names=False, offsets=False):
    rv = _do_midx(outdir, outfilename, infilenames, prefixstr,
                  hashsize, auto=auto, force=force, offsets=offsets)
    if rv and print_names:
        prout.write(rv[1] + b'\n')


def do_midx_dir(path, outfilename, prout, hashsize, auto=False, force=False,
                max_files=-1, print_names=False, offsets=False):
    already = {}
    sizes = {}
    if force and not auto:
        midxs = []   # don't use existing midx files
    else:
        midxs = []
        contents = {}
        for mname in glob.glob(b'%s/*.midx' % path):
            m = _maybe_open_midx(mname, hashsize, rm_broken=auto or force)
            if not m:
                continue
            with m:
                midxs.append(mname)
                contents[mname] = [(b'%s/%s' % (path,i)) for i in m.idxnames]
                sizes[mname] = len(m)

        # sort the biggest+newest midxes first, so that we can eliminate
        # smaller (or older) redundant ones that come later in the list
        midxs.sort(key=lambda ix: (-sizes[ix], -xstat.stat(ix).st_mtime_ns))

        for mname in midxs:
            any = 0
            for iname in contents[mname]:
                if not already.get(iname):
                    already[iname] = 1
                    any = 1
            if not any:
                debug1('%r is redundant\n' % mname)
                unlink(mname)
                already[mname] = 1

    midxs = [k for k in midxs if not already.get(k)]
    idxs = [k for k in glob.glob(b'%s/*.idx' % path) if not already.get(k)]

    for iname in idxs:
        with git.open_idx(iname, hashsize) as i:
            sizes[iname] = len(i)

    all_idxes = [(sizes[n],n) for n in (midxs + idxs)]
    if not all_idxes:
        return

    # FIXME: what are the optimal values?  Does this make sense?
    desired_hwm = 1 if force else 5
    desired_lwm = 1 if force else 2
    existed = dict((name,1) for sz,name in all_idxes)
    debug1('midx: %d indexes; want no more than %d.\n'
           % (len(all_idxes), desired_hwm))
    if len(all_idxes) <= desired_hwm:
        debug1('midx: nothing to do.\n')
    while len(all_idxes) > desired_hwm:
        all_idxes.sort()
        part1 = [name for sz,name in all_idxes[:len(all_idxes)-desired_lwm+1]]
        part2 = all_idxes[len(all_idxes)-desired_lwm+1:]
        all_idxes = list(do_midx_group(path, outfilename, part1, hashsize,
                                       auto=auto, force=force, max_files=max_files,
                                       offsets=offsets)) \
                                 + part2
        if len(all_idxes) > desired_hwm:
            debug1('\nStill too many indexes (%d > %d).  Merging again.\n'
                   % (len(all_idxes), desired_hwm))

    if print_names:
        for sz,name in all_idxes:
            if not existed.get(name):
                prout.write(name + b'\n')


def do_midx_group(outdir, outfilename, infiles, hashsize,
                  auto=False, force=False, max_files=-1, offsets=False):
    groups = list(_group(infiles, max_files))
    gprefix = ''
    for n,sublist in enumerate(groups):
        if len(groups) != 1:
            gprefix = 'Group %d: ' % (n+1)
        rv = _do_midx(outdir, outfilename, sublist, gprefix,
                      hashsize, auto=auto, force=force, offsets=offsets)
        if rv:
            yield rv


def main(argv):
    o = options.Options(optspec)
    opt, flags_, extra = o.parse_bytes(argv[1:])
    opt.output = argv_bytes(opt.output) if opt.output else None

    if extra and (opt.auto or opt.force):
        o.fatal("you can't use -f/-a and also provide filenames")
    if opt.check and (not extra and not opt.auto):
        o.fatal("if using --check, you must provide filenames or -a")

    if 0 <= opt.max_files < 5:
        o.fatal('--max-files must be greater than 4')
    elif opt.max_files < 0:
        maxf = min(resource.getrlimit(resource.RLIMIT_NOFILE))
        if maxf < 5:
            o.fatal('--max-files must be greater than 4')
        # Add a safety margin if we can, with a max of 32
        opt.max_files = max(5, maxf - min(32, maxf))

    if opt.dir:
        path = argv_bytes(opt.dir)
        hashsize = 32 if opt.sha256 else 20
    else:
        if opt.sha256:
            o.fatal('--sha256 is not allowed with a repo')
        with LocalRepo() as repo:
            path = repo.packdir()
            hashsize = repo.hashsize

    extra = [argv_bytes(x) for x in extra]

    if opt.check:
        # check existing midx files
        if extra:
            midxes = extra
        else:
            debug1('midx: scanning %s\n' % path)
            midxes = glob.glob(os.path.join(path, b'*.midx'))
        for name in midxes:
            check_midx(name, hashsize)
        if not saved_errors:
            log('All tests passed.\n')
    else:
        if extra:
            sys.stdout.flush()
            do_midx(path, opt.output, extra, b'',
                    byte_stream(sys.stdout), hashsize,
                    auto=opt.auto, force=opt.force,
                    print_names=opt.print, offsets=opt.offsets)
        elif opt.auto or opt.force:
            sys.stdout.flush()
            debug1('midx: scanning %s\n' % path_msg(path))
            do_midx_dir(path, opt.output, byte_stream(sys.stdout), hashsize,
                        auto=opt.auto, force=opt.force,
                        max_files=opt.max_files, offsets=opt.offsets)
        else:
            o.fatal("you must use -f or -a or provide input filenames")
