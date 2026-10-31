#!/usr/bin/env bash
printf '%s\n' "$*" >> "${FAKE_STATE:?}/notify"
[ "${FAKE_NOTIFY_FAIL:-0}" = 0 ] || exit 1
