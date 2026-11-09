#!/usr/bin/env python3
"""Breaks one guard of the Windows port at a time, in a copy, and runs the Windows suite that must catch it. A mutant
that stays green is a guard no test holds. Each one undoes something only Windows does, seen on the PC on 09/11/2026.
tests/mutants.py does the same for the Linux version. The suites end a stand-in Desktop only, never the real one: every
run that could reach taskkill gets a fake one. Windows only.

Usage: py -3 -I windows\\tests\\mutants.py [-j JOBS] [NAME...]
"""
import argparse
import concurrent.futures
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

if os.name != "nt":
    print("skipped: the mutants of the Windows branches run on Windows only")
    sys.exit(0)

HERE = pathlib.Path(__file__).resolve().parent
WINDOWS = HERE.parent
TARGETS = {"switch": WINDOWS / "claude-switch.py", "desktop": WINDOWS / "desktop.py", "host": WINDOWS / "chrome-host.py"}

# (name, target, suite, text of the target, what replaces it)
MUTANTS = [
    # Where Desktop's folder is, and what an open Desktop holds.
    ("left-folder-taken", "desktop", "desktop",
     '    candidates = [folder for folder in candidates if folder.is_dir() and not any(folder.glob(".user-data-left*"))]\n',
     "    candidates = [folder for folder in candidates if folder.is_dir()]\n"),
    ("cookies-read-while-open", "desktop", "desktop",
     "    if WINDOWS and running() is not None:\n        return open_on_windows(account, config, who)\n", ""),
    ("status-reads-cookies", "desktop", "desktop",
     "    if WINDOWS and pid is not None:\n        account = config.get(ACCOUNT)\n",
     "    if False:\n        account = config.get(ACCOUNT)\n"),
    ("any-folder-is-ours", "desktop", "desktop",
     '        if folder and "--type=" in command and same_folder(folder.group(1) or folder.group(2), DESKTOP):\n',
     '        if folder and "--type=" in command:\n'),
    # Closing Desktop.
    ("inside-not-refused", "desktop", "desktop",
     "    if inside_desktop():\n        raise Refused(INSIDE,", "    if False:\n        raise Refused(INSIDE,"),
    ("quit-from-inside", "desktop", "desktop",
     '    if inside_desktop():\n        print("Claude Desktop runs this terminal, quit it by hand.", file=sys.stderr)\n'
     "        return 1\n", ""),
    ("forced-at-once", "desktop", "desktop",
     "    if end_process(pid, force=False):\n        gone_within(min(seconds, 5))\n", ""),
    ("tray-not-forced", "desktop", "desktop",
     "    if lock_held():\n        end_process(pid, force=True)\n", "    if False:\n        end_process(pid, force=True)\n"),
    ("helper-left", "desktop", "desktop",
     '    for helper in folder_helpers(windows_processes("claude.exe") or []):\n'
     '        end_process(helper["ProcessId"], force=True)\n', ""),
    ("files-not-waited", "desktop", "desktop", "    while busy := busy_files():\n", "    while busy := []:\n"),
    # The preferences of each account.
    ("web-not-saved", "desktop", "desktop", "        save_web(current)\n", ""),
    ("web-not-restored", "desktop", "desktop", "    staged = stage_web(account)\n", "    staged = []\n"),
    ("no-web-repair", "desktop", "desktop", "    if WINDOWS:\n        repair_web()\n", ""),
    ("swap-not-undone", "desktop", "desktop",
     "    except BaseException:\n        unswap_web(aside)\n        raise\n    return aside\n",
     "    except BaseException:\n        raise\n    return aside\n"),
    # The command.
    ("credman-ignored", "switch", "switch",
     '    return WINDOWS and dig(load_or_none(CONFIG), "cachedGrowthBookFeatures", "tengu_windows_credman") is True\n',
     "    return False\n"),
    ("remote-control-in-desktop", "switch", "switch",
     '            and not DESKTOP_PROGRAM.search(process.get("ExecutablePath") or "")]\n', "]\n"),
    ("any-claude-is-a-server", "switch", "switch",
     'REMOTE_CONTROL = re.compile(r"\\sremote-control(\\s|$)")', 'REMOTE_CONTROL = re.compile(r".")'),
    # The pipe of the Chrome host.
    ("pending-not-followed", "host", "chrome",
     "    threading.Thread(target=follow_pending, daemon=True).start()\n    while True:\n        try:\n"
     "            client = server.accept()\n",
     "    while True:\n        try:\n            client = server.accept()\n"),
    ("second-host-gives-up", "host", "chrome",
     "        except PermissionError:\n            time.sleep(5)\n", "        except PermissionError:\n            raise\n"),
]


def command(suite, files):
    if suite == "switch":
        return [sys.executable, "-I", str(HERE / "test-switch.py"), str(files["switch"])]
    if suite == "desktop":
        return [sys.executable, "-I", str(HERE / "test-desktop.py"), str(files["desktop"])]
    return [sys.executable, "-I", str(HERE / "test-chrome.py"), str(files["switch"]), str(files["host"])]


def run(mutant):
    name, target, suite, text, replacement = mutant
    source = TARGETS[target].read_text(encoding="utf-8")
    if source.count(text) != 1:
        return name, f"not run: the text to break appears {source.count(text)} times"
    folder = pathlib.Path(tempfile.mkdtemp(prefix=f"claude-switch-mutant.{name}."))
    try:
        mutated = folder / TARGETS[target].name
        mutated.write_text(source.replace(text, replacement), encoding="utf-8", newline="\n")
        result = subprocess.run(command(suite, dict(TARGETS, **{target: mutated})), capture_output=True, text=True,
                                encoding="utf-8", timeout=900, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                                creationflags=subprocess.CREATE_NO_WINDOW)
        return name, "killed" if result.returncode != 0 else "SURVIVED"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-j", "--jobs", type=int, default=4)
    parser.add_argument("names", nargs="*")
    arguments = parser.parse_args()
    chosen = [mutant for mutant in MUTANTS if not arguments.names or mutant[0] in arguments.names]
    with concurrent.futures.ThreadPoolExecutor(arguments.jobs) as pool:
        results = list(pool.map(run, chosen))
    for name, outcome in results:
        print(f"{outcome:<10} {name}" if outcome in ("killed", "SURVIVED") else f"{name}: {outcome}")
    killed = sum(outcome == "killed" for _, outcome in results)
    print(f"mutants: {killed} of {len(results)} killed")
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
