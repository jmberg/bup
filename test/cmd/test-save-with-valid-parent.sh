#!/usr/bin/env bash
. ./wvtest-bup.sh || exit $?
. test/cmd/lib.sh || exit $?

set -o pipefail

top="$(WVPASS pwd)" || exit $?
tmpdir="$(WVPASS wvmktempdir)" || exit $?

export BUP_DIR="$tmpdir/bup"
export GIT_DIR="$tmpdir/bup"

bup() { "$top/bup" "$@"; }
compare-trees() { "$top/test/cmd/compare-trees" "$@"; }

WVPASS cd "$tmpdir"

# Make sure that we can explicitly save a path whose parent is up to
# date.

WVSTART "save path with up to date parent"
WVPASS bup init

WVPASS mkdir -p src/a src/b
WVPASS touch src/a/1 src/b/2
WVPASS bup index -u src
WVPASS bup save -n src src

WVPASS bup save -n src src/b
WVPASS bup restore -C restore "src/latest/$(pwd)/"
WVPASS test ! -e restore/src/a
WVPASS "$top/test/cmd/compare-trees" -c src/b/ restore/src/b/

WVPASS bup save -n src src/a/1
WVPASS rm -r restore
WVPASS bup restore -C restore "src/latest/$(pwd)/"
WVPASS test ! -e restore/src/b
WVPASS "$top/test/cmd/compare-trees" -c src/a/ restore/src/a/

WVPASS rm -rf "$tmpdir"
