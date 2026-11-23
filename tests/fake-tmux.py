#!/usr/bin/env python3
"""Stands in for tmux. It answers display-message and capture-pane for the panes of $FAKE_TMUX/panes.json, and logs
every call in $FAKE_TMUX/log with the status the session of the pane had at that moment. An Enter sent to a pane that
reconnects sets bridgeSessionId in the record of its session, as /remote-control does in Claude Code.

panes.json: {"%1": {"pane_pid": PID, "x": 2, "y": 10, "mode": "0", "line": "the cursor line", "record": "PATH",
"reconnects": true}, ...}
"""
import json
import os
import pathlib
import sys

state = pathlib.Path(os.environ["FAKE_TMUX"])
panes = json.loads((state / "panes.json").read_text()) if (state / "panes.json").exists() else {}
arguments = sys.argv[1:]
target = arguments[arguments.index("-t") + 1] if "-t" in arguments else None
pane = panes.get(target)
record = pathlib.Path(pane["record"]) if pane and pane.get("record") else None
status = json.loads(record.read_text()).get("status") if record is not None and record.exists() else None
with open(state / "log", "a") as log:
    log.write(json.dumps({"args": arguments, "status": status}) + "\n")
if pane is None:
    print(f"can't find pane: {target}", file=sys.stderr)
    sys.exit(1)
if arguments[0] == "display-message":
    values = {"pane_pid": pane["pane_pid"], "cursor_x": pane["x"], "cursor_y": pane["y"], "pane_in_mode": pane.get("mode", "0")}
    text = arguments[-1]
    for name, value in values.items():
        text = text.replace("#{" + name + "}", str(value))
    print(text)
elif arguments[0] == "capture-pane":
    print(pane["line"])
elif arguments[0] == "send-keys" and arguments[-1] == "Enter" and pane.get("reconnects") and record is not None:
    session = json.loads(record.read_text())
    session["bridgeSessionId"] = "session_new"
    record.write_text(json.dumps(session))
