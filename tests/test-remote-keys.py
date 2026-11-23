#!/usr/bin/env python3
"""Tests of remote-keys.py, the service of root that presses Ctrl+X Ctrl+Q in a Claude Code session outside tmux. A
stand-in Claude Code runs on a pseudo-terminal of its own and logs each read of its terminal. Every request goes over
a socket, handed to the service as systemd hands it over. As any user every check runs up to the press, which the
kernel refuses without CAP_SYS_ADMIN. As root with CAP_SYS_ADMIN the keys reach the stand-in, and the checks that need
a second user run too, which docker run --user root --cap-add SYS_ADMIN gives.
Usage: test-remote-keys.py REMOTE_KEYS_PY"""
import fcntl
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import termios
import time

HELPER = pathlib.Path(sys.argv[1]).resolve()
ROOT = os.geteuid() == 0
CAPABLE = ROOT and int(next(line.split()[1] for line in open("/proc/self/status")
                            if line.startswith("CapEff:")), 16) >> 21 & 1 == 1
# As root, the sessions and the one who asks run as a user of their own.
USER = 4242 if ROOT else os.getuid()
OTHER = 4243
failures = []
passes = 0

STANDIN = '''
import os, signal, sys, time, tty
mode = sys.argv[2]
if mode in ("raw", "background"):
    tty.setraw(0)
if mode == "background":
    child = os.fork()
    if child == 0:
        os.setpgid(0, 0)
        time.sleep(300)
        os._exit(0)
    os.setpgid(child, child)
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    os.tcsetpgrp(0, child)
with open(sys.argv[1], "ab", buffering=0) as log:
    log.write(f"ready {os.getpid()}\\n".encode())
    if mode in ("background", "detached"):
        time.sleep(300)
    while True:
        data = os.read(0, 64)
        if not data:
            break
        log.write(f"{data.hex()} {time.monotonic()}\\n".encode())
'''
CLIENT = '''
import socket, sys
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.connect(sys.argv[1])
connection.sendall(sys.argv[2].encode())
print(connection.makefile().readline().strip())
'''


def check(name, condition):
    global passes
    if condition:
        passes += 1
    else:
        failures.append(name)
        print("FAIL", name)


def as_user(user):
    return {"user": user, "group": user, "extra_groups": []} if ROOT else {}


class Bench:
    def __init__(self, root):
        self.root = root
        os.chmod(root, 0o755)
        self.state = root / "state"
        self.logs = root / "logs"
        self.logs.mkdir()
        os.chmod(self.logs, 0o777)
        self.processes = []
        self.masters = []

    def program(self, name):
        path = self.root / name
        if not path.exists():
            path.write_text(f"#!{sys.executable} -I\n{STANDIN}")
            path.chmod(0o755)
        return path

    def wait_ready(self, log):
        for _ in range(100):
            lines = log.read_text().splitlines() if log.exists() else []
            if lines and lines[0].startswith("ready "):
                pid = int(lines[0].split()[1])
                return pid, pathlib.Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[19]
            time.sleep(0.05)
        raise RuntimeError("the stand-in didn't start")

    def session(self, name="claude", mode="raw", owner=USER, under_root=False):
        """Starts a stand-in on a pseudo-terminal of its own: its pid, its start and its log. UNDER_ROOT puts a root
        process on its terminal, the leader of the terminal's session."""
        program = self.program(name)
        log = self.logs / f"{len(self.processes)}.log"
        log.touch()
        os.chmod(log, 0o666)
        master, slave = os.openpty()
        if ROOT and owner is not None:
            os.chown(os.ttyname(slave), owner, -1)
        pid = os.fork()
        if pid == 0:
            try:
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
                if under_root:
                    child = os.fork()
                    if child:
                        os.setpgid(child, child)
                        os.tcsetpgrp(slave, child)
                        os.waitpid(child, 0)
                        os._exit(0)
                    os.setpgid(0, 0)
                for fd in (0, 1, 2):
                    os.dup2(slave, fd)
                os.close(slave)
                os.close(master)
                if ROOT:
                    os.setgroups([])
                    os.setgid(USER)
                    os.setuid(USER)
                os.execv(str(program), [str(program), str(log), mode])
            finally:
                os._exit(127)
        os.close(slave)
        self.masters.append(master)
        self.processes.append(pid)
        return (*self.wait_ready(log), log)

    def detached(self):
        """A stand-in with no terminal."""
        log = self.logs / f"{len(self.processes)}.log"
        log.touch()
        os.chmod(log, 0o666)
        process = subprocess.Popen([str(self.program("claude")), str(log), "detached"], start_new_session=True,
                                   stdin=subprocess.DEVNULL, **as_user(USER))
        self.processes.append(process.pid)
        return (*self.wait_ready(log), log)

    def ask(self, request, user=USER):
        """Hands one connection to the service, as systemd does: its answer, and what it logged."""
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o755)
            path = os.path.join(folder, "socket")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            os.chmod(path, 0o666)
            server.listen(1)
            client = subprocess.Popen([sys.executable, "-I", "-c", CLIENT, path, request], stdout=subprocess.PIPE,
                                      text=True, **as_user(user))
            connection, _ = server.accept()
            helper = subprocess.run([sys.executable, "-I", "-S", str(HELPER)], stdin=connection.fileno(),
                                    stdout=connection.fileno(), stderr=subprocess.PIPE, text=True, timeout=30,
                                    env={"RUNTIME_DIRECTORY": str(self.state), "PATH": os.environ.get("PATH", "")})
            connection.close()
            server.close()
            answer, _ = client.communicate(timeout=30)
        return answer.strip(), helper.stderr

    def close(self):
        for pid in self.processes:
            try:
                os.killpg(os.getpgid(pid), 9)
            except OSError:
                pass
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        for master in self.masters:
            os.close(master)
        for pid in self.processes:
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass


def keys(log):
    """What the stand-in read, one read a line."""
    return [line.split()[0] for line in log.read_text().splitlines()[1:]]


def read_at(log):
    return [float(line.split()[1]) for line in log.read_text().splitlines()[1:]]


def main():
    with tempfile.TemporaryDirectory(prefix="claude-switch-remote-keys.") as folder:
        bench = Bench(pathlib.Path(folder))
        try:
            run(bench)
        finally:
            bench.close()
    if not CAPABLE:
        print("The presses themselves ran only up to the kernel: run this as root with CAP_SYS_ADMIN for the rest.")
    print(f"{passes} passed, {len(failures)} failed")
    return 1 if failures else 0


def run(bench):
    answer, said = bench.ask("not a request\n")
    check("a request it can't read: refused", answer == "refused request")
    check("the journal says what it refused", "refused (request)" in said)

    pid, start, log = bench.session()
    answer, _ = bench.ask(f"{pid} 1\n")
    check("a process that started at another time: refused", answer == "refused process")

    pid, start, log = bench.session(name="vim")
    answer, _ = bench.ask(f"{pid} {start}\n")
    check("a program that isn't claude: refused", answer == "refused claude")

    pid, start, log = bench.detached()
    answer, _ = bench.ask(f"{pid} {start}\n")
    check("a session with no terminal: refused", answer == "refused terminal")

    pid, start, log = bench.session(mode="background")
    answer, _ = bench.ask(f"{pid} {start}\n")
    check("a session that isn't the foreground of its terminal: refused", answer == "refused foreground")
    answer, _ = bench.ask(f"{pid} {start}\n")
    check("refused before its terminal is even opened, so the next ask isn't held back", answer == "refused foreground")

    pid, start, log = bench.session(mode="cooked")
    answer, _ = bench.ask(f"{pid} {start}\n")
    check("a terminal in line mode, not a program that reads keys: refused", answer == "refused mode")
    time.sleep(0.5)
    check("nothing reaches a session it refused", keys(log) == [])

    pid, start, log = bench.session()
    answer, said = bench.ask(f"{pid} {start}\n")
    if CAPABLE:
        check("the session of the user who asks: pressed", answer == "pressed")
        for _ in range(40):
            if len(keys(log)) >= 2:
                break
            time.sleep(0.05)
        check("Ctrl+X, then Ctrl+Q, each on its own, and nothing else", keys(log) == ["18", "11"])
        times = read_at(log)
        check("the second key a moment after the first, so they don't read as a paste",
              len(times) == 2 and times[1] - times[0] >= 0.25)
        check("the journal says where it pressed", f"pressed Ctrl+X Ctrl+Q for uid {USER}, pid {pid}" in said)
    else:
        check("every check passes up to the press, which the kernel refuses", answer == "refused kernel")
    answer, _ = bench.ask(f"{pid} {start}\n")
    check("the same session again at once: refused", answer == "refused again")

    if not ROOT:
        print("Skipped as another user than root: a session of another user, a terminal of another user, a root "
              "process on the terminal.")
        return
    pid, start, log = bench.session()
    answer, _ = bench.ask(f"{pid} {start}\n", user=OTHER)
    check("a session of another user: refused", answer == "refused user")
    pid, start, log = bench.session(owner=None)
    answer, _ = bench.ask(f"{pid} {start}\n")
    check("a terminal that isn't the user's: refused", answer == "refused terminal")
    pid, start, log = bench.session(under_root=True)
    answer, _ = bench.ask(f"{pid} {start}\n")
    check("a root process on the terminal, which would read the keys once the session ends: refused",
          answer == "refused others")
    time.sleep(0.5)
    check("nothing reaches the terminal of a root process", keys(log) == [])


if __name__ == "__main__":
    sys.exit(main())
