# Include in your test script like this:
#
#   #!/usr/bin/env bash
#   . ./wvtest-bup.sh

. ./wvtest.sh

_wvtop="$(pwd -P)"

wvmktempdir ()
{
    local script_name="$(basename $0)"
    mkdir -p "$_wvtop/test/tmp" || exit $?
    mktemp -d "$_wvtop/test/tmp/$script_name-XXXXXXX" || exit $?
}

wvmkmountpt ()
{
    local script_name="$(basename $0)"
    mkdir -p "$_wvtop/test/mnt" || exit $?
    mktemp -d "$_wvtop/test/mnt/$script_name-XXXXXXX" || exit $?
}

# for bupindex/index-cache
export XDG_CACHE_HOME="$(wvmktempdir)" || exit $?
