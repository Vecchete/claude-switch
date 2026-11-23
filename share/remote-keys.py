#!/usr/bin/env -S python3 -I -S
"""Presses Ctrl+X then Ctrl+Q, or Ctrl+X then Ctrl+R, in the terminal of a Claude Code session, for the user the
session belongs to. claude-switch binds Ctrl+X Ctrl+Q to /remote-control and Ctrl+X Ctrl+R to /after-switch, and has
them pressed in a session outside tmux once it switched to another claude.ai account: the first where the switch took
Remote Control, then the second. Only CAP_SYS_ADMIN can put keys in a terminal that isn't one's own (TIOCSTI), so
this runs as root: systemd starts it for each connection to the claude-switch-remote socket, with the connection as
its standard input and output.

The request is one line, PID, START and the letter of the second key: the pid of the session, its start time, field
22 of /proc/PID/stat, and r for Ctrl+R, nothing or q for Ctrl+Q. The answer is one line: pressed, or refused and
why. It presses those two keys and nothing else, and only when
- the process runs as the user who asks, read from the socket, is called claude and started at START,
- its controlling terminal is a pseudo-terminal of that user, where it is the foreground,
- the terminal is in raw mode, as Claude Code keeps it while it reads keys,
- every process on that terminal runs as that user, so no program of another user, a root shell the session runs
  under for one, ever reads a key it presses,
- it didn't press the same keys in that session in the last 20 seconds.
"""
import fcntl
import os
import re
import socket
import stat
import struct
import sys
import termios
import time

FIRST = b"\x18"
SECOND = {"q": b"\x11", "r": b"\x12"}
# Keys that arrive together read as a paste. Claude Code waits 3 seconds for the second key of a chord.
GAP = 0.3
AGAIN = 20
# The majors of Unix98 pseudo-terminals, /dev/pts/N.
PTS = range(136, 144)
STATE = os.environ.get("RUNTIME_DIRECTORY") or "/run/claude-switch-remote"
REQUEST = re.compile(rb"([0-9]{1,10}) ([0-9]{1,20})(?: ([qr]))?\n?")


class Refused(Exception):
    pass


def read(folder, name):
    fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC, dir_fd=folder)
    try:
        data = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return data.decode("ascii", "replace")
            data += chunk
    finally:
        os.close(fd)


def process(pid, terminal=None):
    """What /proc says of PID: its name, process group, controlling terminal, the foreground group there, its start
    and its user ids, all of one process even when its pid is taken again meanwhile. With TERMINAL, only its terminal
    when that is another. None when it's gone."""
    try:
        folder = os.open(f"/proc/{pid}", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        text = read(folder, "stat")
        fields = text[text.rfind(")") + 2:].split()
        found = {"name": text[text.find("(") + 1:text.rfind(")")], "pgrp": int(fields[2]), "tty": int(fields[4]),
                 "tpgid": int(fields[5]), "start": fields[19]}
        if terminal is not None and found["tty"] != terminal:
            return found
        uids = next(line.split()[1:5] for line in read(folder, "status").splitlines() if line.startswith("Uid:"))
        found["uids"] = {int(uid) for uid in uids}
        return found
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    finally:
        os.close(folder)


def users_on(terminal):
    """The user ids of every process whose controlling terminal is TERMINAL."""
    users = set()
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            found = process(entry, terminal)
            if found is not None and found["tty"] == terminal:
                users |= found["uids"]
    return users


def same(pid, start, terminal):
    """True while PID is the process that started at START, and the foreground on TERMINAL."""
    found = process(pid, terminal)
    return found is not None and found["start"] == start and found["tty"] == terminal \
        and found["tpgid"] == found["pgrp"]


def terminal_of(uid, pid, start):
    """The terminal of the session, open, once the keys may go there. Raises Refused."""
    found = process(pid)
    if found is None or found["start"] != start:
        raise Refused("process")
    if found["uids"] != {uid}:
        raise Refused("user")
    if found["name"] != "claude":
        raise Refused("claude")
    major, minor = os.major(found["tty"]), os.minor(found["tty"])
    if major not in PTS:
        raise Refused("terminal")
    if found["tpgid"] != found["pgrp"]:
        raise Refused("foreground")
    try:
        fd = os.open(f"/dev/pts/{(major - PTS[0]) * 256 + minor}", os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        raise Refused("terminal") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISCHR(info.st_mode) or (os.major(info.st_rdev), os.minor(info.st_rdev)) != (major, minor) \
                or info.st_uid != uid:
            raise Refused("terminal")
        if termios.tcgetattr(fd)[3] & (termios.ICANON | termios.ECHO):
            raise Refused("mode")
        if users_on(found["tty"]) != {uid}:
            raise Refused("others")
    except BaseException:
        os.close(fd)
        raise
    return fd, found["tty"]


def press(fd, terminal, uid, pid, start, second):
    """Presses Ctrl+X then SECOND in the session, on the terminal terminal_of opened. Raises Refused."""
    try:
        for index, key in enumerate((FIRST, second)):
            if index:
                time.sleep(GAP)
                if users_on(terminal) != {uid}:
                    raise Refused("others")
            if not same(pid, start, terminal):
                raise Refused("foreground")
            try:
                fcntl.ioctl(fd, termios.TIOCSTI, key)
            except OSError:
                raise Refused("kernel") from None
    finally:
        os.close(fd)


def answer(uid, request):
    match = REQUEST.fullmatch(request)
    if match is None:
        print(f"refused (request) for uid {uid}", file=sys.stderr)
        return "refused request"
    pid, start, letter = int(match.group(1)), match.group(2).decode(), (match.group(3) or b"q").decode()
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    with open(os.path.join(STATE, "lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name in os.listdir(STATE):
            path = os.path.join(STATE, name)
            try:
                if name != "lock" and time.time() - os.stat(path).st_mtime > 3600:
                    os.unlink(path)
            except OSError:
                pass
        mark = os.path.join(STATE, f"{uid}-{pid}-{start}-{letter}")
        try:
            if os.path.exists(mark) and time.time() - os.stat(mark).st_mtime < AGAIN:
                raise Refused("again")
            fd, terminal = terminal_of(uid, pid, start)
            # From here on it counts as pressed, whatever the kernel says.
            try:
                press(fd, terminal, uid, pid, start, SECOND[letter])
            finally:
                open(mark, "w").close()
        except Refused as refusal:
            print(f"refused ({refusal.args[0]}) for uid {uid}, pid {pid}", file=sys.stderr)
            return f"refused {refusal.args[0]}"
    print(f"pressed Ctrl+X Ctrl+{letter.upper()} for uid {uid}, pid {pid}", file=sys.stderr)
    return "pressed"


def main():
    connection = socket.socket(fileno=0)
    try:
        connection.settimeout(5)
        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                              struct.calcsize("3i")))
        request = b""
        while not request.endswith(b"\n") and len(request) <= 64:
            chunk = connection.recv(64)
            if not chunk:
                break
            request += chunk
        connection.sendall(answer(uid, request).encode() + b"\n")
    except OSError:
        return 1
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
