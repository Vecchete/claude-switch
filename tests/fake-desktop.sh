#!/usr/bin/env bash
# Stands in for desktop.py: logs what claude-switch asks of it, and answers with the statuses it is given.
# FAKE_DESKTOP_SWITCH lists the status of each switch in turn, "3,0" for instance.
if [ -e /proc/self/fd/9 ]; then
    printf 'store lock inherited by %s\n' "$1" >> "$FAKE_DESKTOP_LOG"
fi
printf '%s\n' "$*" >> "$FAKE_DESKTOP_LOG"
case $1 in
    switch)
        count=$(grep -c '^switch' "$FAKE_DESKTOP_LOG")
        status=$(printf '%s' "${FAKE_DESKTOP_SWITCH:-0}" | cut -d, -f"$count")
        [ "${status:-0}" != 0 ] || printf 'Claude Desktop is on %s.\n' "$2"
        exit "${status:-0}"
        ;;
    quit) exit "${FAKE_DESKTOP_QUIT:-0}" ;;
    start) exit 0 ;;
esac
exit 2
