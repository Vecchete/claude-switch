#!/usr/bin/env bash
# Stands in for mv: once FAKE_MV_TARGET is renamed in place, an interrupt reaches the script that ran it.
/bin/mv "$@" || exit
[ "${!#}" != "${FAKE_MV_TARGET:-}" ] || kill -INT "$PPID"
