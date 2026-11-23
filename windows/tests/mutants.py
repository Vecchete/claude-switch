#!/usr/bin/env python3
"""Breaks one guard of the Windows port at a time, in a copy, and runs the Windows suite that must catch it. A mutant
that stays green is a guard no test holds. Each one undoes something only Windows does, seen on the PC on 09/11/2026,
or a guard of the gateway keys, of automatic switching and of their Tab completion, which tests/test-keys.sh and
tests/test-auto.sh can't check on Windows.
tests/mutants.py does the same for the Linux version. The suites end a stand-in Desktop only, never the real one: every
run that could reach taskkill gets a fake one. desktop.py and localstorage.py are copied together, as desktop.py loads
localstorage.py from its own folder, and a mutant of completion.ps1 runs in a copy of the PowerShell files and of the
extension's manifest, which test-powershell.ps1 reads too. Windows only.

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
           "localstorage": WINDOWS / "localstorage.py", "host": WINDOWS / "chrome-host.py",
           "completion": WINDOWS / "completion.ps1", "usage": WINDOWS / "usage.py"}
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
     "    if inside_desktop():\n        raise inside_refusal(who)\n    raise Refused(RUNNING,",
     "    if False:\n        raise inside_refusal(who)\n    raise Refused(RUNNING,"),
    ("quit-from-inside", "desktop", "desktop",
     '    if inside_desktop():\n        print("This runs inside Claude Desktop: close it by hand.", file=sys.stderr)\n'
     "        return 1\n", ""),
    ("inside-never-said", "desktop", "desktop",
     "            return 0 if inside_desktop() else 1\n", "            return 1\n"),
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
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "perm-mode-ack", "ultrathinkAvailable", "autoResumeRateLimit")',
     'NOT_CARRIED = ("epitaxy-auto-", "perm-mode-ack", "ultrathinkAvailable", "autoResumeRateLimit")'),
    ("auto-mode-carried", "desktop", "desktop",
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "perm-mode-ack", "ultrathinkAvailable", "autoResumeRateLimit")',
     'NOT_CARRIED = ("bypassPermissions", "perm-mode-ack", "ultrathinkAvailable", "autoResumeRateLimit")'),
    ("permission-acks-carried", "desktop", "desktop",
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "perm-mode-ack", "ultrathinkAvailable", "autoResumeRateLimit")',
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "ultrathinkAvailable", "autoResumeRateLimit")'),
    ("what-claude-found-carried", "desktop", "desktop",
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "perm-mode-ack", "ultrathinkAvailable", "autoResumeRateLimit")',
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "perm-mode-ack", "autoResumeRateLimit")'),
    ("auto-resume-carried", "desktop", "desktop",
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "perm-mode-ack", "ultrathinkAvailable", "autoResumeRateLimit")',
     'NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "perm-mode-ack", "ultrathinkAvailable")'),
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
    ("local-storage-not-put-back", "desktop", "desktop", "    for unit in missing:\n", "    for unit in []:\n"),
    ("copy-drops-lost-databases", "desktop", "desktop",
     "    for unit in KEPT:\n        if not any((fresh / path).exists() for path in unit):\n",
     "    for unit in ():\n        if not any((fresh / path).exists() for path in unit):\n"),
    # IndexedDB and WebStorage, which go with the sign-in.
    ("databases-not-swapped", "desktop", "desktop",
     "    if not WINDOWS or not source.is_dir():\n        return []\n", "    if True:\n        return []\n"),
    ("no-repair", "desktop", "desktop", "    if WINDOWS:\n        repair_web()\n", ""),
    ("swap-not-undone", "desktop", "desktop",
     "    except BaseException:\n        unswap_web(aside)\n        raise\n    return aside\n",
     "    except BaseException:\n        raise\n    return aside\n"),
    ("config-left-aside", "desktop", "desktop",
     "        drop_staged(staged)\n        temporary.unlink(missing_ok=True)\n", "        drop_staged(staged)\n"),
    # Desktop starting in the middle of a switch.
    ("written-while-open", "desktop", "desktop",
     "    if changes and running() is not None:\n"
     '        raise Refused(RUNNING, "Claude Desktop started during the switch, nothing changed.")\n', ""),
    ("local-storage-put-back-while-open", "desktop", "desktop",
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
    ("ultra-not-named", "switch", "switch",
     '    return "ultra" if plan == "max" and tier == ULTRA_TIER else str(plan)\n', "    return str(plan)\n"),
    ("login-tier-first", "switch", "switch",
     '    tier = alt(dig(data, "oauthAccount", "organizationRateLimitTier"), dig(login, "rateLimitTier"))\n',
     '    tier = alt(dig(login, "rateLimitTier"), dig(data, "oauthAccount", "organizationRateLimitTier"))\n'),
    ("profile-plan-ignored", "switch", "switch",
     '    plan = organization[len("claude_"):] if isinstance(organization, str) and organization.startswith("claude_") '
     'else ""\n', '    plan = ""\n'),
    ("credman-ignored", "switch", "switch",
     '    return WINDOWS and dig(load_or_none(CONFIG), "cachedGrowthBookFeatures", "tengu_windows_credman") is True\n',
     "    return False\n"),
    ("remote-control-in-desktop", "switch", "switch",
     '            and not DESKTOP_PROGRAM.search(process.get("ExecutablePath") or "")]\n', "]\n"),
    ("any-claude-is-a-server", "switch", "switch",
     'REMOTE_CONTROL = re.compile(r"\\sremote-control(\\s|$)")', 'REMOTE_CONTROL = re.compile(r".")'),
    # The gateway keys, which tests/test-keys.sh can't check on Windows.
    ("key-unkept-switched-over", "switch", "keys",
     '    if live_key()[0] == 2:\n        die("~/.claude/settings.json holds a gateway key no account keeps, nothing changed.',
     '    if False:\n        die("~/.claude/settings.json holds a gateway key no account keeps, nothing changed.'),
    ("key-other-env-dropped", "switch", "keys",
     '    env = {key: "" if key in KEY_ENV else value for key, value in current.items()}\n',
     '    env = {key: "" for key in current if key in KEY_ENV}\n'),
    ("key-entries-taken-out", "switch", "keys",
     '    env = {key: "" if key in KEY_ENV else value for key, value in current.items()}\n',
     "    env = {key: value for key, value in current.items() if key not in KEY_ENV}\n"),
    ("key-empty-is-a-value", "switch", "keys",
     '    return {key: value for key, value in env.items() if key in KEY_ENV and value != ""}\n',
     "    return {key: value for key, value in env.items() if key in KEY_ENV}\n"),
    ("key-link-replaced", "switch", "keys", "    return pathlib.Path(os.path.realpath(SETTINGS))\n", "    return SETTINGS\n"),
    ("key-no-settings-not-created", "switch", "keys",
     "    if not target.exists() or kept_owner(target, aside):\n        replace(aside, target)\n",
     "    if not target.exists():\n        aside.unlink()\n    elif kept_owner(target, aside):\n        replace(aside, target)\n"),
    ("key-unreadable-settings-unsaid", "switch", "keys",
     "    if not isinstance(current, dict):\n        die(\"~/.claude/settings.json can't be read as JSON, nothing changed.\")\n",
     "    if not isinstance(current, dict):\n        settings, current = {}, {}\n"),
    ("key-left-on-login", "switch", "keys",
     "        if key is not None:\n            set_key_env()\n            note_switch()\n",
     "        if key is not None:\n            note_switch()\n"),
    ("key-left-on-other-login", "switch", "keys",
     "    if key is not None:\n        set_key_env()\n    note_switch()\n", "    note_switch()\n"),
    ("key-half-switch-on-read-only", "switch", "keys", "    if key is not None:\n        require_writable_settings()\n", ""),
    ("key-unkept-overwritten-by-key", "switch", "keys",
     "def switch_to_key(file, account):\n    require_kept_key()\n", "def switch_to_key(file, account):\n"),
    ("key-refreshed", "switch", "keys",
     "        # A gateway key has no login to refresh.\n        if is_key(file):\n            continue\n", ""),
    ("key-known-by-token", "switch", "keys",
     '        score = 3 if key == live else 2 if key.get("ANTHROPIC_BASE_URL") == live.get("ANTHROPIC_BASE_URL") else 1\n',
     "        score = 1\n"),
    ("key-headers-block", "switch", "keys", "    return (2, None) if key_in_settings() else (1, None)\n", "    return 2, None\n"),
    ("key-edits-lost", "switch", "keys", '    save_live()\n    save_live_key()\n    if first == "":\n',
     '    save_live()\n    if first == "":\n'),
    ("key-twin-kept", "switch", "keys", "    twin = key_twin(data, file)\n    if twin is not None:\n",
     "    twin = key_twin(data, file)\n    if False:\n"),
    ("key-file-overwritten", "switch", "keys",
     '    file = key_file(name)\n    if file.exists():\n        die(f"Another account is kept as {file.name}, choose another name.")\n',
     "    file = key_file(name)\n"),
    ("key-login-signs-in", "switch", "keys", "        if is_key(wanted):\n", "        if False:\n"),
    ("key-like-bearer", "switch", "keys", '(like_env.get("ANTHROPIC_API_KEY") is not None\n', "(False\n"),
    ("key-add-holds-lock", "switch", "keys", '    unlock_store()\n    token = ""\n', '    token = ""\n'),
    ("key-renew-not-live", "switch", "keys", "    if active:\n        set_key_env(file)\n", "    if active:\n"),
    ("key-remove-leaves-entries", "switch", "keys", "    if active:\n        set_key_env()\n", ""),
    ("key-model-kept-unsaid", "switch", "keys",
     '        warn(f"A running session that started on the model {model} keeps it: /model in it picks another.")\n',
     "        pass\n"),
    ("key-shell-ranks-ignored", "switch", "keys",
     '        if isinstance(account, dict) and filled(alt(account.get(var), "")):\n            break\n', ""),
    ("key-shell-settings-ignored", "switch", "keys", "        if var in settings:\n            continue\n", ""),
    ("key-switch-no-env-warning", "switch", "keys", "    set_key_env(file)\n    note_switch()\n    warn_environment(file)\n",
     "    set_key_env(file)\n    note_switch()\n"),
    ("key-overrides-unsaid", "switch", "keys",
     "    if live is not None:\n        remind_overrides(live)\n    remind_signins()\n", "    remind_signins()\n"),
    ("key-overrides-unranked", "switch", "keys",
     '        rank = 2 if key_token(key_set(dig(account.data, "env"))) != "" else 7\n', "        rank = 7\n"),
    ("key-prompt-forgets-unkept", "switch", "keys", "        remind_unkept_key()\n        if live is not None:\n",
     "        if live is not None:\n"),
    ("key-prompt-marks-login", "switch", "keys", "        live = active_file()\n        files = accounts()\n",
     "        live = live_file()\n        files = accounts()\n"),
    ("key-unkept-marks-login", "switch", "keys", "    return live_file() if status == 1 else None\n",
     "    return live_file()\n"),
    # What a in the prompt asks first.
    ("key-prompt-add-no-key", "switch", "keys", '        elif kind in ("k", "K"):\n', "        elif False:\n"),
    ("key-prompt-add-no-subscription", "switch", "keys", '        if kind in ("s", "S"):\n', "        if False:\n"),
    ("key-prompt-add-name-unchecked", "switch", "keys",
     "            if problem is not None:\n                print(problem)\n                return\n", ""),
    ("key-prompt-add-url-unchecked", "switch", "keys",
     '            if url and not re.match(r"https?://", url, re.IGNORECASE):\n', "            if False:\n"),
    ("key-prompt-add-bearer-for-anthropic", "switch", "keys", 'if url else ["--api-key"])', "if url else [])"),
    # What a in the prompt asks last, and what an add does with the answer.
    ("prompt-add-switch-not-asked", "switch", "keys",
     '        if input("Switch to it once it\'s added? [y/N] ").strip() in ("y", "Y", "yes", "Yes", "YES"):\n'
     '            command.append("--switch")\n', ""),
    ("key-add-switch-ignored", "switch", "keys", '            if options[0] == "--switch":\n                switch = True\n',
     '            if options[0] == "--switch":\n                switch = False\n'),
    ("add-switches-without-asking", "switch", "switch", "    if switch:\n        print(done)\n",
     "    if True:\n        print(done)\n"),
    ("login-switch-passed-on", "switch", "switch",
     '    arguments = [argument for argument in arguments if argument != "--switch"]\n', ""),
    ("login-chrome-left-on-the-new-account", "switch", "switch", "    elif not known and not switch:\n        web_restore()\n",
     ""),
    ("chrome-back-signs-out", "host", "chrome", '        if argv[1] == "--back" and load(account) is None:\n',
     "        if False:\n"),
    # The key shows as it's pasted, an account can be deleted, and an API key's credit is not readable.
    ("key-pasted-unseen", "switch", "keys", '            token = input(f"Gateway key for {label}: ")\n',
     '            token = __import__("getpass").getpass(f"Gateway key for {label}: ")\n'),
    ("delete-not-in-the-prompt", "switch", "keys", '        elif choice in ("d", "D"):\n', "        elif False:\n"),
    ("delete-nothing-unsaid", "switch", "keys", '        print("There is no account to delete.")\n', ""),
    ("delete-unasked", "switch", "keys",
     '    if answer.strip() not in ("y", "Y", "yes", "Yes", "YES"):\n        return\n    try:\n        lock_store()\n'
     "        delete_account(file)\n",
     "    try:\n        lock_store()\n        delete_account(file)\n"),
    ("delete-the-live-login", "switch", "switch",
     "        if file == live_file():\n            die(f\"{account.label} is the account Claude Code is on:",
     "        if False:\n            die(f\"{account.label} is the account Claude Code is on:"),
    ("delete-keeps-saved-sessions", "switch", "switch",
     "            for path in (WEB_SAVED / f\"{uuid}.json\", DESKTOP_SAVED / f\"{uuid}.json\"):\n"
     "                path.unlink(missing_ok=True)\n", ""),
    ("delete-keeps-window-storage", "switch", "switch", '            for suffix in (".web", ".web.tmp", ".web.old"):\n',
     "            for suffix in ():\n"),
    ("delete-takes-a-shared-login", "switch", "switch",
     "                and not any(not isinstance(data, dict) or dig(data, \"oauthAccount\", \"accountUuid\") == uuid for data in kept):\n",
     ":\n"),
    ("delete-past-a-broken-account", "switch", "switch", "not isinstance(data, dict) or dig(data,", "dig(data,"),
    ("delete-a-path-for-any-uuid", "switch", "switch", "        if isinstance(uuid, str) and UUID.fullmatch(uuid) \\\n",
     "        if isinstance(uuid, str) \\\n"),
    ("delete-keeps-readings", "switch", "switch",
     '    for suffix in (".json", ".refused.json"):\n        (STORE / ".usage" / f"{file.stem}{suffix}").unlink(missing_ok=True)\n',
     ""),
    ("delete-keeps-the-hold", "switch", "switch", "    if held:\n        HOLD.unlink(missing_ok=True)\n", ""),
    ("delete-active-key-unsaid", "switch", "keys", "        emptied = forget_key(file)\n",
     "        forget_key(file)\n        emptied = False\n"),
    ("api-key-left-unsaid", "usage", "auto", '"not readable" if anthropic_api_key(account) else "-"', '"-"'),
    ("api-key-note-missing", "switch", "auto", '    if "not readable" in LEFT_OF.values():\n', "    if False:\n"),
    ("temp-files-of-others-removed", "switch", "keys", "    for path in WRITING:\n",
     '    for path in (f"{CREDENTIALS}.switch", f"{CONFIG}.switch", f"{settings_target()}.switch"):\n'),
    ("temp-files-left", "switch", "keys",
     '    for path in (f"{CREDENTIALS}.switch", f"{CONFIG}.switch", f"{settings_target()}.switch"):\n'
     "        with contextlib.suppress(OSError):\n            os.remove(path)\n    recover_refreshes()\n",
     "    recover_refreshes()\n"),
    # What only Windows does to the gateway keys.
    ("key-read-only-attribute-ignored", "switch", "keys",
     "    if WINDOWS and target.is_file() and not os.access(target, os.W_OK):\n", "    if False:\n"),
    ("key-folder-acl-ignored", "switch", "keys", "    if not WINDOWS:\n        return os.access(folder, os.W_OK)\n",
     "    if True:\n        return os.access(folder, os.W_OK)\n"),
    ("key-token-bom-kept", "switch", "keys", '.decode("utf-8-sig", errors="replace")', '.decode("utf-8", errors="replace")'),
    ("settings-bom-unread", "switch", "keys",
     '    return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))\n',
     '    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))\n'),
    ("key-name-unsafe", "switch", "keys", '    return STORE / ("key_" + re.sub(r"[^\\w@.-]", "_", name) + ".json")\n',
     '    return STORE / ("key_" + name + ".json")\n'),
    # Automatic switching, which tests/test-auto.sh can't check on Windows.
    ("auto-hold-ignored", "switch", "auto", "    hold = held_file()\n    best = best_of(files, hold, active)\n",
     "    hold = None\n    best = best_of(files, hold, active)\n"),
    # An account added, and two of the same kind.
    ("auto-added-not-ranked", "switch", "auto",
     '        print(f"{done} Automatic switching ranks it with the others:")\n        lock_store()\n        route_and_say()\n',
     '        print(f"{done} Automatic switching ranks it with the others:")\n'),
    ("auto-added-switch-held", "switch", "auto",
     "            HOLD.unlink(missing_ok=True)\n            print(\"Automatic switching is on: at its next check",
     "            print(\"Automatic switching is on: at its next check"),
    ("auto-tie-active-unsent", "switch", "auto", "    best = best_of(files, hold, active)\n",
     "    best = best_of(files, hold)\n"),
    ("usage-tie-to-what-expires-first", "usage", "auto", "0 if active is not None and file == active else 1", "1"),
    ("auto-hold-outlives-its-room", "switch", "auto",
     "    if hold is not None and best != hold:\n        HOLD.unlink(missing_ok=True)\n", ""),
    ("auto-watch-without-session", "switch", "auto", '        if not auto_on() or usage_helper("sessions")[0] != 0:\n',
     "        if not auto_on():\n"),
    ("auto-watch-when-off", "switch", "auto", '        if not auto_on() or usage_helper("sessions")[0] != 0:\n',
     '        if usage_helper("sessions")[0] != 0:\n'),
    ("auto-watch-reads-too-seldom", "switch", "auto", '"--max-age", "240"', '"--max-age", "300"'),
    ("auto-watch-task-untouched", "switch", "auto", "        if not watch_enable(True):\n", "        if False:\n"),
    ("auto-hook-when-off", "switch", "auto",
     '    raw = sys.stdin.buffer.read(1048576) if sys.stdin is not None else b""\n    if not auto_on():\n        return 0\n',
     '    raw = sys.stdin.buffer.read(1048576) if sys.stdin is not None else b""\n'),
    ("auto-hook-any-unknown", "switch", "auto", "        if not BUDGET_WORDS.search(text.lower()):\n            return 0\n", ""),
    ("auto-hook-wakes-forever", "switch", "auto", "    if not may_wake(session):\n        return 0\n", ""),
    ("auto-hook-refuses-the-next-account", "switch", "auto", "    if switched > failed_at(transcript):\n",
     "    if switched > failed_at(transcript) + 10 ** 15:\n"),
    ("auto-hook-trusts-an-old-error", "switch", "auto",
     "    return moment if moment is not None and moment > STARTED - 60000 else STARTED - 1500\n",
     "    return moment if moment is not None else STARTED - 1500\n"),
    ("auto-hook-says-everything", "switch", "auto", "    sys.stdout = sys.stderr = LOGGING\n", ""),
    ("auto-hook-helpers-speak-to-claude", "switch", "auto",
     "            return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=LOGGING, stderr=subprocess.STDOUT,\n",
     "            return subprocess.run(command, stdin=subprocess.DEVNULL,\n"),
    ("auto-hook-old-entry-kept", "switch", "auto", "        if not hook_current():\n            set_auto_hook(True)\n",
     "        if not hook_in_settings():\n            set_auto_hook(True)\n"),
    ("auto-on-takes-every-failure-hook", "switch", "auto",
     "    kept = [entry for entry in stop_failures(settings) if not own_hook(entry)]\n", "    kept = []\n"),
    ("auto-exec-hook-unknown", "switch", "auto",
     '        if isinstance(arguments, list) and arguments[-1:] == ["hook"] \\\n', "        if False \\\n"),
    ("auto-hook-through-a-shell", "switch", "auto",
     '    return {"type": "command", "command": command, "args": arguments, "asyncRewake": True, "timeout": 120}\n',
     '    return {"type": "command", "command": " ".join([command, *arguments]), "asyncRewake": True, "timeout": 120}\n'),
    ("auto-hook-console-window", "switch", "auto", '    launcher = shutil.which("pyw.exe")\n', "    launcher = None\n"),
    ("auto-switch-time-of-a-login-unnoted", "switch", "auto", "        set_key_env()\n    note_switch()\n",
     "        set_key_env()\n"),
    ("auto-switch-time-of-a-key-unnoted", "switch", "auto", "    set_key_env(file)\n    note_switch()\n",
     "    set_key_env(file)\n"),
    ("auto-order-ignored", "switch", "auto",
     '        order = ORDER_FILE.read_text(encoding="utf-8").splitlines()[0].split()\n', "        order = []\n"),
    ("auto-order-twice", "switch", "auto", '        if kind in order:\n            die(f"{kind} is twice in the order.")\n', ""),
    ("auto-order-not-routed", "switch", "auto", "        if auto_on() and not route():\n", "        if False:\n"),
    ("auto-renewed-key-still-refused", "switch", "auto", "    store_key(file, data)\n    forget_refusal(file)\n",
     "    store_key(file, data)\n"),
    ("auto-list-without-left", "switch", "auto", "    live = active_file()\n    read_left()\n", "    live = active_file()\n"),
    ("auto-desktop-asks", "switch", "auto", '    if AUTO:\n        answer = "yes"\n', '    if False:\n        answer = "yes"\n'),
    # Claude Desktop runs the hook of one of its Code sessions.
    ("auto-desktop-kept-unsaid", "switch", "auto", "    if status == 6:\n        DESKTOP_KEPT = True\n", ""),
    ("auto-desktop-session-woken", "switch", "auto",
     '    if helper is not None and run(helper + ["inside"]) == 0:\n', "    if False:\n"),
    ("auto-log-not-cut-by-the-hook", "switch", "auto", "    make_store()\n    trim_log()\n    HOOK_ERR = sys.stderr\n",
     "    make_store()\n    HOOK_ERR = sys.stderr\n"),
    ("auto-log-not-cut-by-the-watch", "switch", "auto", "    trim_log()\n    route()\n", "    route()\n"),
    ("auto-log-cut-after-the-hook-opens-it", "switch", "auto",
     "    trim_log()\n    HOOK_ERR = sys.stderr\n    LOGGING = open(AUTO_LOG, \"a\", encoding=\"utf-8\", errors=\"replace\", "
     "buffering=1)\n",
     "    HOOK_ERR = sys.stderr\n    LOGGING = open(AUTO_LOG, \"a\", encoding=\"utf-8\", errors=\"replace\", buffering=1)\n"
     "    trim_log()\n"),
    ("auto-hook-program-unknown", "switch", "auto", "for part in [command, *arguments]", "for part in arguments"),
    # What only Windows does in usage.py.
    ("usage-session-by-pid-alone", "usage", "auto",
     "    if running is not None and (started is None or running == str(started)):\n", "    if running is not None:\n"),
    ("usage-ended-process-runs", "usage", "auto",
     "            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:\n"
     "                return None\n", ""),
    ("usage-output-in-the-code-page", "usage", "auto",
     '                stream.reconfigure(encoding="utf-8", errors="replace")\n', "                pass\n"),
    # Remote Control comes back after a switch to another claude.ai account: what the switch starts.
    ("remote-not-remembered", "switch", "auto", "    save_live()\n    remote_save()\n    apply_account(file)\n",
     "    save_live()\n    apply_account(file)\n"),
    ("remote-not-followed", "switch", "auto", "    remote_control_switch(account.label)\n    remote_follow()\n",
     "    remote_control_switch(account.label)\n"),
    ("remote-switch-waits-for-the-sessions", "switch", "auto",
     "                subprocess.Popen(command, creationflags=flags, **quiet)\n",
     "                subprocess.run(command, creationflags=flags, **quiet)\n"),
    ("remote-follower-holds-the-store", "switch", "auto",
     "        # It waits for the sessions for minutes, so it doesn't lock the store.\n",
     "        lock_store()\n"),
    ("remote-back-unsaid", "switch", "auto", '        notify("Remote Control is back", ',
     '        print("Remote Control is back", '),
    ("remote-lost-unsaid", "switch", "auto", '        notify(f"Remote Control is off in ', '        print(f"Remote Control is off in '),
    ("remote-name-free", "switch", "auto", '"auto", "watch", "hook", "remote")', '"auto", "watch", "hook")'),
    # A better account waits while Remote Control runs where nothing can bring it back.
    ("remote-held-ignored", "switch", "auto", "    if ROUTED_HELD:\n        staying, going",
     "    if False:\n        staying, going"),
    ("remote-held-when-needed", "switch", "auto", '    if usage_helper("why", active)[0] == 0:\n        return None\n', ""),
    ("remote-held-for-a-key", "switch", "auto", "or is_key(best) or best == live_file():",
     "or best == live_file():"),
    ("remote-held-said-each-time", "switch", "auto", '        notify_once("remote-held", f"It stays on {staying}",\n',
     '        notify(f"It stays on {staying}",\n'),
    ("remote-held-unsaid-by-auto", "switch", "auto", '        if waiting:\n            print(f"It waits to go there',
     '        if False:\n            print(f"It waits to go there'),
    ("remote-held-unsaid-when-it-stays", "switch", "auto", "        if ROUTED_HELD:\n", "        if False:\n"),
    # What brings Remote Control back types only where it can't do harm, once, and in the console of the session.
    ("remote-saves-every-session", "usage", "auto",
     '        if session is not None and session.get("kind") == "interactive" \\\n'
     '                and isinstance(session.get("bridgeSessionId"), str):\n',
     '        if session is not None and session.get("kind") == "interactive":\n'),
    ("remote-types-into-desktop", "usage", "auto",
     '        return "console" if session.get("entrypoint") == "cli" else None\n', '        return "console"\n'),
    ("remote-held-in-a-console-it-cant-read", "usage", "auto",
     '        return pane is not None and console(session.get("pid"), "line") is not None\n',
     "        return pane is not None\n"),
    ("remote-held-outside-a-terminal-unsaid", "usage", "auto",
     'if not in_its_pane(session) and not through_root(session, ("bound", "free"))]', "if False]"),
    ("remote-types-while-busy", "usage", "auto", '    if session.get("status") == "idle" and press(entry, tries):\n',
     "    if press(entry, tries):\n"),
    ("remote-types-over-text", "usage", "auto", '    if text in PROMPTS and x == len(text) + 1:\n        return "empty"\n',
     '    if True:\n        return "empty"\n'),
    ("remote-tries-at-once", "usage", "auto",
     '    if entry.get("typed") and now - entry["typed"] <= REMOTE_BACK * 1000:\n        return None\n', ""),
    ("remote-tries-without-end", "usage", "auto",
     '    if tries >= REMOTE_TRIES or now - entry.get("since", now) > REMOTE_WAIT * 1000:\n',
     '    if now - entry.get("since", now) > REMOTE_WAIT * 1000:\n'),
    ("remote-before-the-drop", "usage", "auto", '    if not entry.get("off"):\n', "    if False:\n"),
    ("remote-outside-a-console-said-late", "usage", "auto",
     '            return None if entry.get("pane") or through_root(session) else "lost"\n', "            return None\n"),
    ("remote-two-at-once", "usage", "auto", "        if not take(running, wait=False):\n            return 1\n", ""),
    ("remote-enter-with-the-text", "usage", "auto", '    time.sleep(0.5)\n    return send_keys(pane, pid, "Enter")\n',
     '    return send_keys(pane, pid, "Enter")\n'),
    ("remote-console-helper-in-a-console", "usage", "auto", "creationflags=subprocess.DETACHED_PROCESS)",
     "creationflags=subprocess.CREATE_NO_WINDOW)"),
    # Ctrl+X Ctrl+Q, bound to /remote-control beside the keys of the user, pressed at the prompt, a draft included.
    ("remote-key-not-bound", "usage", "auto", "    if kept:\n        remote_binding(write=True)\n",
     "    if False:\n        remote_binding(write=True)\n"),
    ("remote-key-over-a-key-of-the-user", "usage", "auto",
     '            elif key == CHORD or key.startswith(f"{CHORD} ") or (key in ("ctrl+x", "ctrl+q") and action is not None):\n',
     "            elif False:\n"),
    ("remote-key-in-a-broken-file", "usage", "auto",
     "    except (OSError, ValueError):\n        return None\n    blocks = ",
     '    except (OSError, ValueError):\n        data = {"bindings": []}\n    blocks = '),
    ("remote-key-without-the-bindings-of-the-user", "usage", "auto", '    chat["bindings"][CHORD] = COMMAND\n',
     '    chat["bindings"] = {CHORD: COMMAND}\n'),
    ("remote-key-written-crlf", "usage", "auto", '                  newline="\\n") as handle:\n',
     "                  newline=None) as handle:\n"),
    ("remote-key-read-only-flag-copied", "usage", "auto", "        if not WINDOWS:\n            os.chmod(temporary, mode)\n",
     "        os.chmod(temporary, mode)\n"),
    ("remote-key-pressed-unbound", "usage", "auto", "    return bound and where is not None and press_chord(pane, pid)\n",
     "    return where is not None and press_chord(pane, pid)\n"),
    ("remote-typed-before-the-key", "usage", "auto", '    if where == "empty" and (tries or not bound):\n',
     '    if where == "empty":\n'),
    ("remote-never-typed-once-the-key-fails", "usage", "auto", '    if where == "empty" and (tries or not bound):\n',
     '    if where == "empty" and not bound:\n'),
    ("remote-chord-at-once", "usage", "auto", '    time.sleep(0.3)\n    return send_keys(pane, pid, "C-q")\n',
     '    return send_keys(pane, pid, "C-q")\n'),
    ("remote-chord-as-letters", "usage", "auto", "            keys = chr(ord(given.upper()) & 0x1F) if len(given) == 1",
     "            keys = given if len(given) == 1"),
    # The Tab completion of the gateway keys.
    ("completion-key-missing", "completion", "powershell",
     "$candidates = @('list', 'login', 'key', 'rename', 'delete', 'refresh', 'auto', 'help') + @(& $accounts)",
     "$candidates = @('list', 'login', 'rename', 'delete', 'refresh', 'auto', 'help') + @(& $accounts)"),
    ("completion-no-auto", "completion", "powershell",
     "$candidates = @('list', 'login', 'key', 'rename', 'delete', 'refresh', 'auto', 'help') + @(& $accounts)",
     "$candidates = @('list', 'login', 'key', 'rename', 'delete', 'refresh', 'help') + @(& $accounts)"),
    ("completion-no-delete", "completion", "powershell",
     "$candidates = @('list', 'login', 'key', 'rename', 'delete', 'refresh', 'auto', 'help') + @(& $accounts)",
     "$candidates = @('list', 'login', 'key', 'rename', 'refresh', 'auto', 'help') + @(& $accounts)"),
    ("completion-delete-without-accounts", "completion", "powershell",
     "} elseif ($command -eq 'rename' -or $command -eq 'delete') {", "} elseif ($command -eq 'rename') {"),
    ("completion-no-auto-actions", "completion", "powershell", "$candidates = 'on', 'off', 'order'", "$candidates = @()"),
    ("completion-no-kinds", "completion", "powershell", "$candidates = 'ultra', 'max', 'key'", "$candidates = @()"),
    ("completion-keys-after-login", "completion", "powershell", "$candidates = @(& $accounts 'logins')",
     "$candidates = @(& $accounts)"),
    ("completion-key-actions-missing", "completion", "powershell", "$candidates = 'import', 'add', 'renew', 'remove'",
     "$candidates = @()"),
    ("completion-keys-not-renewed", "completion", "powershell",
     "if ($before[2].Extent.Text -eq 'renew' -or $before[2].Extent.Text -eq 'remove') {", "if ($false) {"),
    ("completion-like-missing", "completion", "powershell", "} elseif ($before[-1].Extent.Text -eq '--like') {",
     "} elseif ($false) {"),
    ("completion-strict-mode-unsafe", "completion", "powershell",
     "$isKey = $properties['kind'] -and $account.kind -eq 'key'", "$isKey = $account.kind -eq 'key'"),
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
    if suite == "keys":
        return [sys.executable, "-I", str(HERE / "test-keys.py"), str(files["switch"])]
    if suite == "auto":
        return [sys.executable, "-I", str(HERE / "test-auto.py"), str(files["switch"]), str(files["usage"])]
    if suite == "desktop":
        return [sys.executable, "-I", str(HERE / "test-desktop.py"), str(files["desktop"])]
    if suite == "powershell":
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "test-powershell.ps1"),
                "-Repo", str(files["repo"])]
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
        if suite == "powershell":
            files["repo"] = folder / "repo"
            shutil.copytree(WINDOWS, files["repo"] / "windows", ignore=shutil.ignore_patterns("__pycache__"))
            manifest = pathlib.Path("share", "chrome-extension", "manifest.json")
            (files["repo"] / manifest).parent.mkdir(parents=True)
            shutil.copy2(WINDOWS.parent / manifest, files["repo"] / manifest)
            shutil.copy2(mutated, files["repo"] / "windows" / mutated.name)
        result = subprocess.run(command(suite, files), capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=900,
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
