#!/bin/sh

self="$(dirname "$0")"
lib="$(realpath "$self/../lib")"
cmd="$(realpath "$self")"
ARGS="-p --branch --source=$lib,$cmd --omit=*/lib/bup/t/*"
RCARG="--rcfile=$self/../t/tmp/tox.ini"
MODE=run
if ! [ -z "$BUP_COV_MODE" ] ; then
    MODE="$BUP_COV_MODE"
    ARGS=""
fi

if [ "$1" = "-c" ] || ( [ "$MODE" = "run" ] && [ "$1" = "" ] ) ; then
    tmp=$(mktemp) || exit 2
    trap "rm -f \"$tmp\"" EXIT
    if [ "$1" = "-c" ] ; then
        echo "$2" > "$tmp"
    else
        cat > "$tmp"
    fi
    exec @bup_coverage@ run $ARGS $RCARG "$tmp"
fi

exec @bup_coverage@ $MODE $ARGS $RCARG "$@"
