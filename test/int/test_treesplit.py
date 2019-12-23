
from __future__ import absolute_import

from wvpytest import *

from bup import tree


def test_abbreviate():
    l1 = [b"1234", b"1235", b"1236"]
    WVPASSEQ(tree._tree_names_abbreviate(l1), l1)
    l2 = [b"aaaa", b"bbbb", b"cccc"]
    WVPASSEQ(tree._tree_names_abbreviate(l2), [b'a', b'b', b'c'])
    l3 = [b".bupm"]
    WVPASSEQ(tree._tree_names_abbreviate(l3), [b'.b'])
    l4 = [b"..strange..name"]
    WVPASSEQ(tree._tree_names_abbreviate(l4), [b'..s'])
    l5 = [b"justone"]
    WVPASSEQ(tree._tree_names_abbreviate(l5), [b'j'])
