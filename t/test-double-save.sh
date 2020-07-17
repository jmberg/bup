#!/usr/bin/env bash
. wvtest-bup.sh || exit $?
. t/lib.sh || exit $?

set -o pipefail

TOP="$(WVPASS pwd)" || exit $?
tmpdir="$(WVPASS wvmktempdir)" || exit $?
export BUP_DIR="$tmpdir/bup"

bup()
{
    "$TOP/bup" "$@"
}

WVPASS cd "$tmpdir"

WVSTART 'double save'
WVPASS bup init
WVPASS mkdir src
WVPASS echo data > src/data
WVPASS bup index src
WVPASS bup save -n ref1 src
WVPASS bup save -n ref2 --strip src

WVPASS bup ls ref1/latest$(pwd)/src/data
WVPASS bup ls ref2/latest/data

WVPASS rm -r "$tmpdir"
