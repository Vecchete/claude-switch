#!/usr/bin/env bash
# Stands in for chrome-host.py: logs what claude-switch asks of it.
printf '%s\n' "$*" >> "$FAKE_WEB_LOG"
