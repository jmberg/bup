from __future__ import absolute_import, print_function
from binascii import hexlify

from wvpytest import *

from bup import git
from hypothesis import given, assume
import hypothesis.strategies as st

@given(tree=st.binary(20, 20),
       parent=st.binary(20, 20),
       author_name=st.one_of(st.none(), st.binary(1)),
       author_mail=st.binary(),
       author_sec=st.integers(),
       author_offs=st.integers(),
       committer_name=st.one_of(st.none(), st.binary(1)),
       committer_mail=st.binary(),
       committer_sec=st.integers(),
       committer_offs=st.integers(),
       message=st.binary())
def test_commit_roundtrip(tree, parent,
                          author_name, author_mail, author_sec, author_offs,
                          committer_name, committer_mail, committer_sec, committer_offs,
                          message):
    # for the names, git requires no \n, no <, and being C no \0
    # since it searches forward for <, it can deal with all else
    # (including empty which we allow as None)
    assume(author_name is None or (not b'\n' in author_name and
                                   not b'<' in author_name and
                                   not b'\x00' in author_name))
    assume(committer_name is None or (not b'\n' in committer_name and
                                      not b'<' in committer_name and
                                      not b'\x00' in committer_name))
    assume(not b'\n' in author_mail and
           not b'\x00' in author_mail)
    assume(not b'\n' in committer_mail and
           not b'\x00' in committer_mail)

    ci = git.CommitInfo(hexlify(tree), [hexlify(parent)],
                        author_name, author_mail, author_sec, author_offs * 60,
                        committer_name, committer_mail, committer_sec, committer_offs * 60,
                        message)

    # generate commit - if author/committer is None omit the separating space
    if ci.author_name is None:
        author = b''
    else:
        author = ci.author_name + b' '
    author += b'<' + ci.author_mail + b'>'
    if ci.committer_name is None:
        committer = b''
    else:
        committer = ci.committer_name + b' '
    committer += b'<' + ci.committer_mail + b'>'
    blob = git.create_commit_blob(tree, parent,
                                  author, ci.author_sec, ci.author_offset,
                                  committer, ci.committer_sec, ci.committer_offset,
                                  ci.message)
    # check that the commit parses back properly
    WVPASSEQ(git.parse_commit(blob), ci)
