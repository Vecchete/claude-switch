#!/usr/bin/env python3
"""Breaks one guard of the Windows port at a time, in a copy, and runs the Windows suite that must catch it. A mutant
that stays green is a guard no test holds. Each one undoes something only Windows does, seen on the PC on 09/11/2026.
tests/mutants.py does the same for the Linux version. The suites end a stand-in Desktop only, never the real one: every
run that could reach taskkill gets a fake one. desktop.py and localstorage.py are copied together, as desktop.py loads
localstorage.py from its own folder. Windows only.

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
TARGETS = {"switch": WINDOWS / "claude-switch.py", "desktop": WINDOWS / "desktop.py",
           "localstorage": WINDOWS / "localstorage.py", "host": WINDOWS / "chrome-host.py"}
TOGETHER = ("desktop", "localstorage")

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
    # What goes over to the next account.
    ("nothing-carried", "desktop", "desktop",
     "    carried = carry_over(current, account)\n", '    carried = ""\n'),
    ("bypass-gate-carried", "desktop", "desktop",
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "ultrathinkAvailable", "autoResumeRateLimit")',
     'NOT_CARRIED = ("epitaxy-auto-", "ultrathinkAvailable", "autoResumeRateLimit")'),
    ("auto-mode-carried", "desktop", "desktop",
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "ultrathinkAvailable", "autoResumeRateLimit")',
     'NOT_CARRIED = ("bypassPermissions", "ultrathinkAvailable", "autoResumeRateLimit")'),
    ("what-claude-found-carried", "desktop", "desktop",
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "ultrathinkAvailable", "autoResumeRateLimit")',
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "autoResumeRateLimit")'),
    ("auto-resume-carried", "desktop", "desktop",
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "ultrathinkAvailable", "autoResumeRateLimit")',
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "ultrathinkAvailable")'),
    ("connections-carried", "desktop", "desktop",
     'CARRIED = ("LSS-persisted.", "persisted.ccd-session-presets.")',
     'CARRIED = ("LSS-persisted.", "persisted.ccd-session-presets.", "customize-connectors-rows:")'),
    ("presets-not-carried", "desktop", "desktop",
     'CARRIED = ("LSS-persisted.", "persisted.ccd-session-presets.")', 'CARRIED = ("LSS-persisted.",)'),
    ("named-keys-not-carried", "desktop", "desktop",
     "            if retargetable(key, mapping):\n", '            if "." not in key and retargetable(key, mapping):\n'),
    ("own-environments-dropped", "desktop", "desktop",
     "            own = copy.deepcopy(item) if BOUND.search(key) else bound_part(item)\n", "            own = None\n"),
    ("own-environments-dropped-deeper", "desktop", "desktop",
     "            own = copy.deepcopy(item) if BOUND.search(key) else bound_part(item)\n",
     "            own = copy.deepcopy(item) if BOUND.search(key) else None\n"),
    ("every-own-entry-kept", "desktop", "desktop",
     "            own = copy.deepcopy(item) if BOUND.search(key) else bound_part(item)\n",
     "            own = copy.deepcopy(item)\n"),
    ("scopes-replaced-whole", "desktop", "desktop",
     "                carried = carried_value(value.get(new), value[key])\n",
     "                carried = copy.deepcopy(value[key])\n"),
    ("settings-replaced-whole", "desktop", "desktop",
     "                    merged = carried_value(kept_data, data)\n", "                    merged = data\n"),
    ("organization-of-the-login-ignored", "desktop", "desktop",
     '            if oauth["accountUuid"] == account and UUID.fullmatch(oauth["organizationUuid"]):\n',
     "            if False:\n"),
    ("one-organization-not-mapped", "desktop", "desktop",
     "        if old and new:\n", "        if old and new and old != new:\n"),
    ("ids-partly-mapped", "desktop", "desktop",
     "    return bool(ids) and all(found in mapping for found in ids)\n",
     "    return any(found in mapping for found in ids)\n"),
    ("files-not-carried", "desktop", "desktop",
     "    for name in CARRIED_FILES if mapping else ():\n", "    for name in ():\n"),
    ("layout-lost", "desktop", "desktop",
     "            write_file(path, json.dumps(data, indent=indent.group(1) if indent else None, ensure_ascii=False)\n",
     "            write_file(path, json.dumps(data, ensure_ascii=False)\n"),
    ("half-written-file-left", "desktop", "desktop",
     "    finally:\n        temporary.unlink(missing_ok=True)\n", "    finally:\n        pass\n"),
    ("one-file-stops-the-rest", "desktop", "desktop",
     '        except OSError as error:\n            failed.append(f"Not carried over, left as it was: {name} ({error}).")\n',
     '        except ValueError as error:\n            failed.append(f"Not carried over, left as it was: {name} ({error}).")\n'),
    # What an account finds again when it comes back.
    ("copy-not-saved", "desktop", "desktop", "        save_web(current)\n", ""),
    ("nothing-put-back", "desktop", "desktop",
     "    if kept:\n        own = {target, organization(target), *sessions_of(target)} - {None}\n",
     "    if False:\n        own = {target, organization(target), *sessions_of(target)} - {None}\n"),
    ("key-of-nobody-put-back", "desktop", "desktop", "                if owned(key, own):\n", "                if True:\n"),
    ("entries-not-put-back", "desktop", "desktop",
     "            if isinstance(data, dict) and restore_entries(data, as_json(value), own):\n", "            if False:\n"),
    ("entry-of-nobody-put-back", "desktop", "desktop",
     "            elif OWNED.fullmatch(key) and owned(key, own):\n", "            elif OWNED.fullmatch(key):\n"),
    ("put-back-over-carried", "desktop", "desktop",
     "            now = changes.get(key, live.get(key))\n", "            now = live.get(key)\n"),
    ("sessions-unknown", "desktop", "desktop",
     '    return {found for path in (DESKTOP / "claude-code-sessions" / account).rglob("local_*.json")\n'
     "            for found in UUID.findall(path.stem)}\n", "    return set()\n"),
    ("unreadable-copy-stops-all", "desktop", "desktop",
     "            problems.append(f\"Not put back, its saved copy can't be read: the window's storage ({error}).\")\n",
     "            raise\n"),
    ("databases-not-put-back", "desktop", "desktop", "    for unit in missing:\n", "    for unit in []:\n"),
    ("copy-drops-lost-databases", "desktop", "desktop",
     "    for unit in RESTORED:\n        if not any((fresh / path).exists() for path in unit):\n",
     "    for unit in ():\n        if not any((fresh / path).exists() for path in unit):\n"),
    # Desktop starting in the middle of a switch.
    ("written-while-open", "desktop", "desktop",
     "    if changes and running() is not None:\n"
     '        raise Refused(RUNNING, "Claude Desktop started during the switch, nothing changed.")\n', ""),
    ("databases-put-back-while-open", "desktop", "desktop",
     "    if missing and running() is not None:\n", "    if False:\n"),
    ("refusal-swallowed", "desktop", "desktop", "        except Refused:\n            raise\n", ""),
    # Reading Desktop's Local Storage as LevelDB does.
    ("deletions-ignored", "localstorage", "desktop",
     "    def _put(self, key, sequence, value):\n        if key not in self.values or sequence >= self.values[key][0]:\n",
     "    def _put(self, key, sequence, value):\n"
     "        if value is not None and (key not in self.values or sequence >= self.values[key][0]):\n"),
    ("tables-not-read", "localstorage", "desktop",
     "            for key, sequence, value in table_entries(path.read_bytes()):\n"
     "                self._put(key, sequence, value)\n", "            list(table_entries(path.read_bytes()))\n"),
    ("old-log-read", "localstorage", "desktop",
     "if path.stem.isdigit() and (int(path.stem) >= log_number or int(path.stem) == previous))",
     "if path.stem.isdigit())"),
    ("deleted-table-kept", "localstorage", "desktop", "                live.discard(number)\n", "                pass\n"),
    ("compact-pointer-misread", "localstorage", "desktop",
     "            elif tag == 5:\n                _, at = varint(record, at)\n                size, at = varint(record, at)\n"
     "                at += size\n", "            elif tag == 5:\n                _, at = varint(record, at)\n"),
    ("log-checksums-not-read", "localstorage", "desktop",
     "            records, _ = read_log(data, verify=True)\n", "            records, _ = read_log(data, verify=False)\n"),
    ("table-checksums-not-read", "localstorage", "desktop",
     '    if masked(crc32c(data[offset:offset + size + 1])) != struct.unpack_from("<I", data, offset + size + 1)[0]:\n',
     "    if False:\n"),
    ("checksum-without-kind", "localstorage", "desktop",
     "    return masked(crc32c(payload, crc32c(bytes([kind]))))\n", "    return masked(crc32c(payload))\n"),
    ("short-copy-misread", "localstorage", "desktop",
     "            length, offset = 4 + ((tag >> 2) & 7), ((tag >> 5) << 8) | data[at]\n",
     "            length, offset = 5 + ((tag >> 2) & 7), ((tag >> 5) << 8) | data[at]\n"),
    ("long-copy-misread", "localstorage", "desktop",
     '            length, offset = 1 + (tag >> 2), int.from_bytes(data[at:at + 2], "little")\n',
     '            length, offset = 2 + (tag >> 2), int.from_bytes(data[at:at + 2], "little")\n'),
    ("long-literal-misread", "localstorage", "desktop",
     "                count = length - 59\n", "                count = length - 58\n"),
    ("utf16-misread", "localstorage", "desktop",
     '        return raw[1:].decode("utf-16-le", errors="surrogatepass")\n', '        return raw[1:].decode("latin-1")\n'),
    # Writing it as LevelDB does.
    ("torn-log-written", "localstorage", "desktop", "        if any(data[end:]):\n", "        if False:\n"),
    ("sequence-reused", "localstorage", "desktop", "        sequence = self.last + 1\n", "        sequence = self.last\n"),
    ("record-kinds-wrong", "localstorage", "desktop",
     "        kind = (FULL if not body else FIRST) if first else (LAST if not body else MIDDLE)\n",
     "        kind = FULL if not body else FIRST\n"),
    ("block-end-not-padded", "localstorage", "desktop",
     "        if left < 7:\n            out += bytes(left)\n            continue\n", ""),
    ("surrogates-refused", "localstorage", "desktop",
     '        return b"\\x00" + text.encode("utf-16-le", errors="surrogatepass")\n',
     '        return b"\\x00" + text.encode("utf-16-le")\n'),
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
        files = dict(TARGETS, **{target: mutated})
        for other in TOGETHER if target in TOGETHER else ():
            if other != target:
                files[other] = folder / TARGETS[other].name
                shutil.copy2(TARGETS[other], files[other])
        result = subprocess.run(command(suite, files), capture_output=True, text=True, encoding="utf-8", timeout=900,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
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
