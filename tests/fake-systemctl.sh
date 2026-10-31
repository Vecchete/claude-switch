#!/usr/bin/env bash
# Stands in for systemctl: logs what the installer asks of it.
printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_LOG"
