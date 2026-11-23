#!/usr/bin/env python3
"""Stands in for a Claude Code session in a terminal, on Windows. Started in a console of its own with no window
(CREATE_NO_WINDOW), it shows the prompt of Claude Code there with the cursor after it and TEXT typed at it, writes the
record Claude Code keeps in SESSIONS, and logs in LOG each key it reads, with the status its record has at that moment
and when it came. Enter after /remote-control, or Ctrl+X then Ctrl+Q within 3 seconds, sets bridgeSessionId in its
record, as /remote-control and the key claude-switch binds to it do in Claude Code, unless --stuck. The key leaves TEXT
as it is. With --no-console it is started with no console at all (DETACHED_PROCESS): it only writes its record and
waits, a session of a terminal whose console nothing can read. Windows only.

Usage: fake-session.py SESSIONS NAME LOG [--status STATUS] [--typed TEXT] [--entrypoint NAME] [--no-bridge] [--stuck]
                       [--no-console]
"""
import argparse
import ctypes
import json
import msvcrt
import os
import pathlib
import time
from ctypes import wintypes


def creation_time():
    """When this process started, as Claude Code writes it in procStart on Windows."""
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    times = [wintypes.FILETIME() for _ in range(4)]
    kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(entry) for entry in times))
    return str(times[0].dwHighDateTime << 32 | times[0].dwLowDateTime)


def show(text):
    """TEXT in this console from where its cursor is, which stays after it."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.HANDLE)
    kernel.WriteConsoleW.argtypes = (wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                     wintypes.LPVOID)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)
    written = wintypes.DWORD()
    kernel.WriteConsoleW(handle, text, len(text), ctypes.byref(written), None)
    kernel.CloseHandle(handle)


def read(path):
    """The record at PATH. The test replaces it now and then, so a read that meets the replace tries again."""
    for _ in range(100):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
    return {}


def put(path, data):
    """DATA in PATH, written aside and renamed over it, so a reader never finds it half written."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    for _ in range(100):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sessions")
    parser.add_argument("name")
    parser.add_argument("log")
    parser.add_argument("--status", default="idle")
    parser.add_argument("--typed", default="")
    parser.add_argument("--entrypoint", default="cli")
    parser.add_argument("--no-bridge", action="store_true")
    parser.add_argument("--stuck", action="store_true")
    parser.add_argument("--no-console", action="store_true")
    arguments = parser.parse_args()
    record = pathlib.Path(arguments.sessions) / f"{os.getpid()}.json"
    if not arguments.no_console:
        show(chr(0x276F) + chr(0xA0) + arguments.typed)
    data = {"pid": os.getpid(), "procStart": creation_time(), "kind": "interactive", "status": arguments.status,
            "name": arguments.name, "entrypoint": arguments.entrypoint}
    if not arguments.no_bridge:
        data["bridgeSessionId"] = "session_old"
    put(record, data)
    while arguments.no_console:
        time.sleep(60)
    typed, chord = "", None
    with open(arguments.log, "a", encoding="utf-8") as log:
        while True:
            key = msvcrt.getwch()
            log.write(json.dumps({"key": key, "status": read(record).get("status"), "at": time.time()}) + "\n")
            log.flush()
            # Claude Code waits 3 seconds for the second key of a chord, and a key a binding takes isn't typed.
            if key == "\x18":
                chord = time.monotonic()
                continue
            if key == "\x11" and chord is not None and time.monotonic() - chord <= 3:
                chord = None
                if not arguments.stuck:
                    put(record, dict(read(record), bridgeSessionId="session_new"))
                continue
            chord = None
            if key != "\r":
                typed += key
                continue
            if typed == "/remote-control" and not arguments.stuck:
                put(record, dict(read(record), bridgeSessionId="session_new"))
            typed = ""


if __name__ == "__main__":
    main()
