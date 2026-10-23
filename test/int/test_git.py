
from binascii import hexlify, unhexlify
from functools import partial
from time import localtime
import struct, os
import pytest

from pytest import raises
from wvpytest import *
import buptest

from bup import git, path
from bup.compat import environ
from bup.config import ConfigError
from bup.helpers import OBJECT_EXISTS, log, mkdirp


bup_exe = path.exe()


def exc(*cmd): return buptest.exc(cmd)
def exo(*cmd): return buptest.exo(cmd).out

hashsize = 32 if os.environb.get(b'BUP_TEST_SHA256', b'') == b'1' else 20


def init_repo(bupdir):
    git.init_repo(bupdir, objectformat='sha256' if hashsize == 32 else 'sha1')


def local_writer(repo_dir):
    return git.PackWriter(store=git.LocalPackStore(repo_dir=repo_dir,
                                                   hashsize=hashsize))


def test_git_version_detection():
    # Test version types from git's tag history
    # reset in case a previous test in this process set it
    git._git_great = None
    for expected, ver in \
        (('insufficient', b'git version 0.99'),
         ('insufficient', b'git version 0.99.1'),
         ('insufficient', b'git version 0.99.7a'),
         ('insufficient', b'git version 1.0rc1'),
         ('insufficient', b'git version 1.0.1'),
         ('insufficient', b'git version 1.4.2.1'),
         ('insufficient', b'git version 1.7.1'),
         ('insufficient', b'git version 1.7.2-rc0'),
         ('suitable', b'git version 1.7.2'),
         ('suitable', b'git version 1.7.2.1'),
         ('suitable', b'git version 2.14.0-rc0'),
         ('suitable', b'git version 2.14.0 (something ...)'),
         ('suitable', b'git version 111.222.333.444-rc555'),
         ('unrecognized', b'huh?')):
        assert expected == git.is_suitable_git(ver_str=ver)
        try:
            if expected == 'insufficient':
                with pytest.raises(SystemExit):
                    git.require_suitable_git(ver)
            elif expected == 'suitable':
                git.require_suitable_git(ver_str=ver)
            elif expected == 'unrecognized':
                with pytest.raises(git.GitError):
                    git.require_suitable_git(ver)
            else:
                assert False
        finally:
            git._git_great = None
        try:
            environ[b'BUP_GIT_VERSION_IS_FINE'] = b'true'
            git.require_suitable_git(ver_str=ver)
        finally:
            del environ[b'BUP_GIT_VERSION_IS_FINE']
            git._git_great = None


def test_mangle():
    afile  = 0o100644
    afile2 = 0o100770
    alink  = 0o120000
    adir   = 0o040000
    adir2  = 0o040777
    assert git.mangle_name(b'a', adir2, adir) == b'a'
    assert git.mangle_name(b'.bup', adir2, adir) == b'.bup.bupl'
    assert git.mangle_name(b'a.bupa', adir2, adir) == b'a.bupa.bupl'
    WVPASSEQ(git.mangle_name(b'b.bup', alink, alink), b'b.bup.bupl')
    WVPASSEQ(git.mangle_name(b'b.bu', alink, alink), b'b.bu')
    WVPASSEQ(git.mangle_name(b'f', afile, afile2), b'f')
    WVPASSEQ(git.mangle_name(b'f.bup', afile, afile2), b'f.bup.bupl')
    WVPASSEQ(git.mangle_name(b'f.bup', afile, adir), b'f.bup.bup')
    WVPASSEQ(git.mangle_name(b'f', afile, adir), b'f.bup')

    WVPASSEQ(git.demangle_name(b'f.bup', afile), (b'f', git.BUP_CHUNKED))
    WVPASSEQ(git.demangle_name(b'f.bupl', afile), (b'f', git.BUP_NORMAL))
    WVPASSEQ(git.demangle_name(b'f.bup.bupl', afile), (b'f.bup', git.BUP_NORMAL))

    WVPASSEQ(git.demangle_name(b'.bupm', afile), (b'', git.BUP_NORMAL))
    WVPASSEQ(git.demangle_name(b'.bupm', adir), (b'', git.BUP_CHUNKED))

    # for safety, we ignore .bup? suffixes we don't recognize.  Future
    # versions might implement a .bup[a-z] extension as something other
    # than BUP_NORMAL.
    WVPASSEQ(git.demangle_name(b'f.bupa', afile), (b'f.bupa', git.BUP_NORMAL))


def test_encode():
    s = b'hello world'
    packb = b''.join(git._encode_packobj(b'blob', s))
    packt = b''.join(git._encode_packobj(b'tree', s))
    packc = b''.join(git._encode_packobj(b'commit', s))
    packlb = b''.join(git._encode_packobj(b'blob', s * 200))
    WVPASSEQ(git._decode_packobj(packb), (b'blob', s))
    WVPASSEQ(git._decode_packobj(packt), (b'tree', s))
    WVPASSEQ(git._decode_packobj(packc), (b'commit', s))
    WVPASSEQ(git._decode_packobj(packlb), (b'blob', s * 200))
    def encode_pobj(n):
        return b''.join(git._encode_packobj(b'blob', s, compression_level=n))
    WVEXCEPT(ValueError, encode_pobj, -2)
    WVEXCEPT(ValueError, encode_pobj, 10)
    WVEXCEPT(ValueError, encode_pobj, b'x')


def test_packs(tmpdir):
    environ[b'BUP_DIR'] = bupdir = tmpdir + b'/bup'
    init_repo(bupdir)
    git.verbose = 1

    with local_writer(bupdir) as w:
        w.new_blob(os.urandom(100))
        w.new_blob(os.urandom(100))
        w.abort()

    with local_writer(bupdir) as w:
        hashes = []
        nobj = 1000
        for i in range(nobj):
            hashes.append(w.new_blob(b'%d' % i))
        log('\n')
        nameprefix = w.close()
    print(repr(nameprefix))
    WVPASS(os.path.exists(nameprefix + b'.pack'))
    WVPASS(os.path.exists(nameprefix + b'.idx'))

    with git.open_idx(nameprefix + b'.idx', hashsize) as r:
        print(repr(r.fanout))

        for i in range(nobj):
            WVPASS(r.find_offset(hashes[i]) > 0)
        WVPASS(r.exists(hashes[99]))
        WVFAIL(r.exists(b'\0' * hashsize))

        pi = iter(r)
        for h in sorted(hashes):
            WVPASSEQ(hexlify(next(pi)), hexlify(h))

        WVFAIL(r.find_offset(b'\0' * hashsize))

    with git.PackIdxList(bupdir + b'/objects/pack', hashsize) as r:
        WVPASS(r.exists(hashes[5]))
        WVPASS(r.exists(hashes[6]))
        WVFAIL(r.exists(b'\0' * hashsize))


def test_git_sha256_version_detection():
    for expected, ver in \
        ((False, b'git version 1.9.0'),
         (False, b'git version 2.28.0'),
         (True, b'git version 2.29.0'),
         (True, b'git version 2.29.0.windows.1'),
         (True, b'git version 2.34.1'),
         (True, b'git version 3.0.0')):
        assert expected == git.git_sha256_supported(ver_str=ver)
    with pytest.raises(git.GitError):
        git.git_sha256_supported(ver_str=b'huh?')


def test_pack_name_lookup(tmpdir):
    environ[b'BUP_DIR'] = bupdir = tmpdir + b'/bup'
    init_repo(bupdir)
    git.verbose = 1
    packdir = git.repo(b'objects/pack', repo_dir=bupdir)

    idxnames = []
    hashes = []

    for start in range(0,28,2):
        with local_writer(bupdir) as w:
            for i in range(start, start+2):
                hashes.append(w.new_blob(b'%d' % i))
            log('\n')
            idxnames.append(os.path.basename(w.close() + b'.idx'))

    with git.PackIdxList(packdir, hashsize) as r:
        WVPASSEQ(len(r.packs), 2)
        for e,idxname in enumerate(idxnames):
            for i in range(e*2, (e+1)*2):
                WVPASSEQ(idxname, r.exists(hashes[i], want_source=True).pack)


def test_long_index(tmpdir):
    environ[b'BUP_DIR'] = bupdir = tmpdir + b'/bup'
    init_repo(bupdir)
    idx = git.PackIdxV2Writer(hashsize)
    obj_bin = struct.pack(f'!IIIII{hashsize-20}x',
            0x00112233, 0x44556677, 0x88990011, 0x22334455, 0x66778899)
    obj2_bin = struct.pack(f'!IIIII{hashsize-20}x',
            0x11223344, 0x55667788, 0x99001122, 0x33445566, 0x77889900)
    obj3_bin = struct.pack(f'!IIIII{hashsize-20}x',
            0x22334455, 0x66778899, 0x00112233, 0x44556677, 0x88990011)
    pack_bin = struct.pack(f'!IIIII{hashsize-20}x',
            0x99887766, 0x55443322, 0x11009988, 0x77665544, 0x33221100)
    idx.add(obj_bin, 1, 0xfffffffff)
    idx.add(obj2_bin, 2, 0xffffffffff)
    idx.add(obj3_bin, 3, 0xff)
    name = tmpdir + b'/tmp.idx'
    idx.write(name, pack_bin)
    with git.PackIdxV2(name, open(name, 'rb'), hashsize=hashsize) as i:
        WVPASSEQ(i.find_offset(obj_bin), 0xfffffffff)
        WVPASSEQ(i.find_offset(obj2_bin), 0xffffffffff)
        WVPASSEQ(i.find_offset(obj3_bin), 0xff)


def check_establish_default_repo_variant(tmpdir, f, is_establish):
    environ[b'BUP_DIR'] = bupdir = tmpdir + b'/bup'
    # establish_default_repo() falls back to BUP_DIR when given None
    valid = None if is_establish else bupdir
    orig_cwd = os.getcwd()
    try:
        os.chdir(tmpdir)
        init_repo(bupdir)
        if is_establish:
            WVPASSEQ(True, f(valid))
            WVPASSEQ(True, f(valid, must_exist=True))
            WVPASSEQ(True, f(valid, must_exist=False))
        else:
            WVPASSEQ(None, f(valid))

        os.rename(bupdir + b'/objects/pack',
                  bupdir + b'/objects/pack.tmp')
        with open(bupdir + b'/objects/pack', 'wb'): pass
        if is_establish:
            WVPASSEQ(False, f(valid))
            WVPASSEQ(False, f(valid, must_exist=False))
            with pytest.raises(SystemExit) as ex_info:
                f(valid, must_exist=True)
            WVPASSEQ(14, ex_info.value.code)
        else:
            with pytest.raises(SystemExit) as ex_info:
                f(valid)
            WVPASSEQ(14, ex_info.value.code)

        os.unlink(bupdir + b'/objects/pack')
        os.rename(bupdir + b'/objects/pack.tmp',
                  bupdir + b'/objects/pack')
        if is_establish:
            WVPASSEQ(False, f(b'nonexistant.bup'))
            WVPASSEQ(False, f(b'nonexistant.bup', must_exist=False))
            with pytest.raises(SystemExit) as ex_info:
                f(b'nonexistant.bup', must_exist=True)
            WVPASSEQ(15, ex_info.value.code)
        else:
            with pytest.raises(SystemExit) as ex_info:
                f(b'nonexistant.bup')
            WVPASSEQ(15, ex_info.value.code)
    finally:
        os.chdir(orig_cwd)

def test_establish_default_repo(tmpdir):
    check_establish_default_repo_variant(tmpdir, git.establish_default_repo, True)

def test_check_repo_or_die(tmpdir):
    check_establish_default_repo_variant(tmpdir, git.check_repo_or_die, False)


def test_new_commit(tmpdir):
    environ[b'BUP_DIR'] = bupdir = tmpdir + b'/bup'
    init_repo(bupdir)
    git.verbose = 1

    with local_writer(bupdir) as w:
        tree = os.urandom(hashsize)
        parent = os.urandom(hashsize)
        author_name = b'Author'
        author_mail = b'author@somewhere'
        adate_sec = 1439657836
        cdate_sec = adate_sec + 1
        committer_name = b'Committer'
        committer_mail = b'committer@somewhere'
        adate_tz_sec = cdate_tz_sec = None
        commit = w.new_commit(tree, parent,
                              b'%s <%s>' % (author_name, author_mail),
                              adate_sec, adate_tz_sec,
                              b'%s <%s>' % (committer_name, committer_mail),
                              cdate_sec, cdate_tz_sec,
                              b'There is a small mailbox here')
        adate_tz_sec = -60 * 60
        cdate_tz_sec = 120 * 60
        commit_off = w.new_commit(tree, parent,
                                  b'%s <%s>' % (author_name, author_mail),
                                  adate_sec, adate_tz_sec,
                                  b'%s <%s>' % (committer_name, committer_mail),
                                  cdate_sec, cdate_tz_sec,
                                  b'There is a small mailbox here')

    commit_items = git.get_commit_items(hexlify(commit), git.catpipe(bupdir).get)
    local_author_offset = localtime(adate_sec).tm_gmtoff
    local_committer_offset = localtime(cdate_sec).tm_gmtoff
    WVPASSEQ(tree, unhexlify(commit_items.tree))
    WVPASSEQ(1, len(commit_items.parents))
    WVPASSEQ(parent, unhexlify(commit_items.parents[0]))
    WVPASSEQ(author_name, commit_items.author_name)
    WVPASSEQ(author_mail, commit_items.author_mail)
    WVPASSEQ(adate_sec, commit_items.author_sec)
    WVPASSEQ(local_author_offset, commit_items.author_offset)
    WVPASSEQ(committer_name, commit_items.committer_name)
    WVPASSEQ(committer_mail, commit_items.committer_mail)
    WVPASSEQ(cdate_sec, commit_items.committer_sec)
    WVPASSEQ(local_committer_offset, commit_items.committer_offset)

    commit_items = git.get_commit_items(hexlify(commit_off), git.catpipe(bupdir).get)
    WVPASSEQ(tree, unhexlify(commit_items.tree))
    WVPASSEQ(1, len(commit_items.parents))
    WVPASSEQ(parent, unhexlify(commit_items.parents[0]))
    WVPASSEQ(author_name, commit_items.author_name)
    WVPASSEQ(author_mail, commit_items.author_mail)
    WVPASSEQ(adate_sec, commit_items.author_sec)
    WVPASSEQ(adate_tz_sec, commit_items.author_offset)
    WVPASSEQ(committer_name, commit_items.committer_name)
    WVPASSEQ(committer_mail, commit_items.committer_mail)
    WVPASSEQ(cdate_sec, commit_items.committer_sec)
    WVPASSEQ(cdate_tz_sec, commit_items.committer_offset)


def test_list_refs(tmpdir):
    environ[b'BUP_DIR'] = bupdir = tmpdir + b'/bup'
    src = tmpdir + b'/src'
    mkdirp(src)
    with open(src + b'/1', 'wb+') as f:
        f.write(b'something\n')
    with open(src + b'/2', 'wb+') as f:
        f.write(b'something else\n')
    init_repo(bupdir)
    list_refs = partial(git.list_refs, repo_dir=bupdir)
    emptyset = frozenset()
    WVPASSEQ(frozenset(list_refs()), emptyset)
    WVPASSEQ(frozenset(list_refs(limit_to_tags=True)), emptyset)
    WVPASSEQ(frozenset(list_refs(limit_to_heads=True)), emptyset)
    exc(bup_exe, b'index', src)
    exc(bup_exe, b'save', b'-n', b'src', b'--strip', src)
    src_hash = exo(b'git', b'--git-dir', bupdir,
                   b'rev-parse', b'src').strip().split(b'\n')
    assert(len(src_hash) == 1)
    src_hash = unhexlify(src_hash[0])
    tree_hash = unhexlify(exo(b'git', b'--git-dir', bupdir,
                              b'rev-parse',
                              b'src:').strip().split(b'\n', maxsplit=1)[0])
    blob_hash = unhexlify(exo(b'git', b'--git-dir', bupdir,
                              b'rev-parse',
                              b'src:1').strip().split(b'\n', maxsplit=1)[0])
    WVPASSEQ(frozenset(list_refs()),
             frozenset([(b'refs/heads/src', src_hash)]))
    WVPASSEQ(frozenset(list_refs(limit_to_tags=True)), emptyset)
    WVPASSEQ(frozenset(list_refs(limit_to_heads=True)),
             frozenset([(b'refs/heads/src', src_hash)]))
    exc(b'git', b'--git-dir', bupdir, b'tag', b'commit-tag', b'src')
    WVPASSEQ(frozenset(list_refs()),
             frozenset([(b'refs/heads/src', src_hash),
                        (b'refs/tags/commit-tag', src_hash)]))
    WVPASSEQ(frozenset(list_refs(limit_to_tags=True)),
             frozenset([(b'refs/tags/commit-tag', src_hash)]))
    WVPASSEQ(frozenset(list_refs(limit_to_heads=True)),
             frozenset([(b'refs/heads/src', src_hash)]))
    exc(b'git', b'--git-dir', bupdir, b'tag', b'tree-tag', b'src:')
    exc(b'git', b'--git-dir', bupdir, b'tag', b'blob-tag', b'src:1')
    os.unlink(bupdir + b'/refs/heads/src')
    expected_tags = frozenset([(b'refs/tags/commit-tag', src_hash),
                               (b'refs/tags/tree-tag', tree_hash),
                               (b'refs/tags/blob-tag', blob_hash)])
    WVPASSEQ(frozenset(list_refs()), expected_tags)
    WVPASSEQ(frozenset(list_refs(limit_to_heads=True)), frozenset([]))
    WVPASSEQ(frozenset(list_refs(limit_to_tags=True)), expected_tags)


def test_cat_pipe(tmpdir):
    environ[b'BUP_DIR'] = bupdir = tmpdir + b'/bup'
    src = tmpdir + b'/src'
    mkdirp(src)
    with open(src + b'/1', 'wb+') as f:
        f.write(b'something\n')
    with open(src + b'/2', 'wb+') as f:
        f.write(b'something else\n')
    init_repo(bupdir)
    exc(bup_exe, b'index', src)
    oidx = exo(bup_exe, b'save', b'-cn', b'src', b'--strip',
               src).strip()
    typ = exo(b'git', b'--git-dir', bupdir,
              b'cat-file', b'-t', b'src').strip()
    size = int(exo(b'git', b'--git-dir', bupdir,
                       b'cat-file', b'-s', b'src'))

    info = git.catpipe(bupdir).get(b'src')
    assert (oidx, typ, size) == info[:-1]
    data = b''.join(info[3])
    assert data.startswith(b'tree ')
    assert b'\nauthor ' in  data
    assert b'\ncommitter ' in  data

    info = git.catpipe(bupdir).get(b'src', include_data=False)
    assert (oidx, typ, size) == info[:-1]
    assert info[3] is None


def _create_idx(d, i):
    idx = git.PackIdxV2Writer(hashsize)
    # add 255 vaguely reasonable entries
    for s in range(255):
        oid = struct.pack(f'B{hashsize-10}xBxxxxxxBB', 0xff, 0xff, i, s)
        idx.add(oid, s, 100 * s)
    packbin = struct.pack(f'B{hashsize-1}x', i)
    packname = os.path.join(d, b'pack-%s.idx' % hexlify(packbin))
    idx.write(packname, packbin)


# assume for now if /proc/self/fd exists, it's the one we expect
@pytest.mark.skipif(not os.path.isdir(b'/proc/self/fd'),
                    reason='no /proc/self/fd/')
def test_midx_close(tmpdir):

    def openfiles():
        for fd in os.listdir(b'/proc/self/fd'):
            try:
                yield os.readlink(os.path.join(b'/proc/self/fd', fd))
            except OSError:
                pass

    environ[b'BUP_DIR'] = bupdir = tmpdir + b'/bup'
    init_repo(bupdir)
    # create a few dummy idxes
    for i in range(10):
        _create_idx(tmpdir, i)
    git.auto_midx(tmpdir, hashsize)
    with git.PackIdxList(tmpdir, hashsize) as l:
        # this doesn't exist (yet)
        oid10 = struct.pack(f'B{hashsize-10}xBxxxxxxBB', 0xff, 0xff, 10, 0)
        WVPASSEQ(None, l.exists(oid10))
        for i in range(10, 15):
            _create_idx(tmpdir, i)
        # delete the midx ...
        # TODO: why do we need to? git.auto_midx() below doesn't?!
        for fn in os.listdir(tmpdir):
            if fn.endswith(b'.midx'):
                os.unlink(os.path.join(tmpdir, fn))
        # and make a new one
        git.auto_midx(tmpdir, hashsize)
        # check it still doesn't exist - we haven't refreshed
        WVPASSEQ(None, l.exists(oid10))
        # check that we still have the midx open, this really
        # just checks more for the kernel API ('deleted' string)
        for fn in openfiles():
            if not b'midx-' in fn:
                continue
            WVPASSEQ(True, b'deleted' in fn)
        # refresh the PackIdxList
        l.refresh()
        # and check that an object in pack 10 exists now
        WVPASSEQ(OBJECT_EXISTS, l.exists(oid10))
        for fn in openfiles():
            if not b'midx-' in fn:
                continue
            # check that we don't have it open anymore
            WVPASSEQ(False, b'deleted' in fn)


def test_idx_exists_crc(tmpdir):
    environ[b'BUP_DIR'] = bupdir = tmpdir + b'/bup'
    git.init_repo(bupdir)
    for i in range(10):
        _create_idx(tmpdir, i)
    oid = struct.pack(f'B{hashsize-10}xBxxxxxxBB', 0xff, 0xff, 1, 7)
    pack = b'pack-%s.idx' % hexlify(struct.pack(f'B{hashsize-1}x', 1))

    def check(l):
        loc = l.exists(oid, want_crc=True)
        WVPASSEQ(loc.crc, 7)
        WVPASSEQ(loc.offset, None)
        loc = l.exists(oid, want_source=True, want_offset=True, want_crc=True)
        WVPASSEQ(loc.pack, pack)
        WVPASSEQ(loc.offset, 700)
        WVPASSEQ(loc.crc, 7)
        WVPASSEQ(None, l.exists(struct.pack(f'B{hashsize-10}xBxxxxxxBB',
                                            0xff, 0xff, 1, 255),
                                want_crc=True))

    with git.PackIdxList(tmpdir, hashsize) as l:
        check(l)
    git.auto_midx(tmpdir, hashsize)
    WVPASS(any(fn.endswith(b'.midx') for fn in os.listdir(tmpdir)))
    with git.PackIdxList(tmpdir, hashsize) as l:
        check(l)

    parse = git.parse_git_int
    with raises(ConfigError, match="unrecognized --type int value ''"):
        parse(b'')
    with raises(ConfigError, match="unrecognized --type int value x"):
        parse(b'x')
    with raises(ConfigError, match="unrecognized --type int value 0.1"):
        parse(b'0.1')
    with raises(ConfigError, match="value .* too large"):
        parse(b'10000000000000000000000000')
    with raises(ConfigError, match="value .* too small"):
        parse(b'-10000000000000000000000000')
    assert 0 == parse(b'0')
    assert 1 == parse(b'1')
    assert 1 == parse(b' 1')
    assert 1024 == parse(b'1k')
    assert 1024**2 == parse(b'1m')
    assert 1024**3 == parse(b'1g')
    assert 1 == parse(b'+1')
    assert 1024 == parse(b'+1k')
    assert 1024**2 == parse(b'+1m')
    assert 1024**3 == parse(b'+1g')
    assert -1 == parse(b'-1')
    assert -1024 == parse(b'-1k')
    assert -1024**2 == parse(b'-1m')
    assert -1024**3 == parse(b'-1g')
    with raises(ConfigError, match="unrecognized --type int value 1q"):
        parse(b'1q')
    assert 43008 == parse(b'42k')


def test_config_get_all(tmpdir):
    cfg_file = os.path.join(tmpdir, b'multi.conf')
    with open(cfg_file, 'wb') as f:
        f.write(b'[bup]\n\ttest = a\n\ttest = b\n\tempty =\n\tone = x\n')
    WVPASSEQ(git.git_config_get_all(cfg_file, b'bup.test'), [b'a', b'b'])
    WVPASSEQ(git.git_config_get_all(cfg_file, b'bup.empty'), [b''])
    WVPASSEQ(git.git_config_get_all(cfg_file, b'bup.one'), [b'x'])
    WVPASSEQ(git.git_config_get_all(cfg_file, b'bup.nosuchkey'), [])
    WVPASSEQ(git.git_config_get_all(os.path.join(tmpdir, b'nosuch.conf'),
                                    b'bup.test'), [])


def test_config(tmpdir):
    cfg_file = os.path.join(os.path.dirname(__file__), 'sample.conf')
    no_such_file = os.path.join(os.path.dirname(__file__), 'nosuch.conf')
    git_config_get = partial(git.git_config_get, cfg_file)
    WVPASSEQ(git_config_get(b'bup.foo'), b'bar')
    WVPASSEQ(git_config_get(b'bup.bup'), b'is great')
    WVPASSEQ(git_config_get(b'bup.end'), b'end')
    WVPASSEQ(git_config_get(b'bup.comments'), None)
    WVPASSEQ(git_config_get(b'bup.;comments'), None)
    WVPASSEQ(git_config_get(b'bup.and'), None)
    WVPASSEQ(git_config_get(b'bup.#and'), None)

    WVPASSEQ(git.git_config_get(no_such_file, b'bup.foo'), None)

    WVEXCEPT(ConfigError, git_config_get, b'bup.isbad', opttype='bool')
    WVEXCEPT(ConfigError, git_config_get, b'bup.isbad', opttype='int')
    WVPASSEQ(git_config_get(b'bup.isbad'), b'ok')
    WVPASSEQ(True, git_config_get(b'bup.istrue1', opttype='bool'))
    WVPASSEQ(True, git_config_get(b'bup.istrue2', opttype='bool'))
    WVPASSEQ(False, git_config_get(b'bup.isfalse1', opttype='bool'))
    WVPASSEQ(False, git_config_get(b'bup.isfalse2', opttype='bool'))
    with raises(ConfigError, match='no output from'):
        git_config_get(b'bup.isempty', opttype='bool')
    WVPASSEQ(None, git_config_get(b'bup.nosuchkey', opttype='bool'))
    WVPASSEQ(1, git_config_get(b'bup.istrue1', opttype='int'))
    WVPASSEQ(0, git_config_get(b'bup.isfalse2', opttype='int'))
    WVPASSEQ(0x777, git_config_get(b'bup.hex', opttype='int'))

    # Make sure get_config respects the repo()
    git_dir = tmpdir + b'/repo'
    init_repo(git_dir)
    git.check_repo_or_die(git_dir)
    exc(b'git', b'--git-dir', git_dir, b'config', b'bup.foo', b'yep')
    assert b'yep' == git.git_config_get(git.repo_config_file(git_dir), b'bup.foo')
