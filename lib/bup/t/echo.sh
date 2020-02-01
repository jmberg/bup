#!/bin/bash

for arg in "$@" ; do
    echo -n "$arg" ; echo -ne "\x00"
done
