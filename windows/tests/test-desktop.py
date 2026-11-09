#!/usr/bin/env python3
"""Tests of the Windows branches of windows/desktop.py and of windows/localstorage.py on a scratch profile: which
folder is Desktop's, the settings that go over to the next account and what an account finds again when it comes
back, an open Desktop that holds its cookie database, and a quit that ends a stand-in Desktop. The real Claude Desktop
is never touched: every process a test ends is a stand-in it started, and the runs that would call taskkill get a fake
one. Desktop's Local Storage is a LevelDB database these tests write and read with their own code, after LevelDB's
formats, never with localstorage.py's. Windows only, see tests/test-desktop.py for the rest.
Usage: test-desktop.py [DESKTOP_PY], windows/desktop.py by default, with localstorage.py beside it."""
import contextlib
import functools
import importlib.util
import json
import os
import pathlib
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time

if os.name != "nt":
    print("skipped: the Windows branches of desktop.py run on Windows only")
    sys.exit(0)

HERE = pathlib.Path(__file__).resolve().parent
HELPER = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent / "desktop.py").resolve()
ACCOUNT_A = "aaaaaaaa-0000-4000-8000-0000000000a1"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-0000000000b1"
ACCOUNT_C = "cccccccc-0000-4000-8000-0000000000c1"
ORG_A = "aaaaaaaa-0000-4000-8000-00000000a0a0"
ORG_B = "bbbbbbbb-0000-4000-8000-00000000b0b0"
# An organization A was in before: no account goes there.
ORG_OLD = "aaaaaaaa-0000-4000-8000-00000000a0ff"
SESSION_A = "local_aaaaaaaa-1111-4111-8111-111111111111"
SESSION_B = "local_bbbbbbbb-1111-4111-8111-111111111111"
# A session of no account the tests know.
SESSION_X = "local_eeeeeeee-1111-4111-8111-111111111111"
SCOPE_A, SCOPE_B = f"{ACCOUNT_A}/{ORG_A}", f"{ACCOUNT_B}/{ORG_B}"
# Environments of A and of B as the defaults of the Code tab's presets name them.
ENV_A, ENV_B = "bridge:env_01AAAAAAAAAAAAAAAAAAAAAAAA", "cloud:environment:env_01BBBBBBBBBBBBBBBBBBBBBBBB"
SESSION = ("sessionKey", "sessionKeyLC", "routingHint", "lastActiveOrg")
COLUMNS = ["creation_utc", "host_key", "top_frame_site_key", "name", "value", "encrypted_value", "path", "expires_utc",
           "is_secure", "is_httponly", "last_access_utc", "has_expires", "is_persistent", "priority", "samesite",
           "source_scheme", "source_port", "last_update_utc", "source_type", "has_cross_site_ancestor"]
WEB = ("Local Storage", "Session Storage", "IndexedDB", "WebStorage")
INDEXEDDB = ("IndexedDB/https_claude.ai_0.indexeddb.leveldb", "IndexedDB/https_claude.ai_0.indexeddb.blob")
NO_WINDOW = subprocess.CREATE_NO_WINDOW
BLOCK = 32768
ORIGIN = b"_https://claude.ai\x00"
LEVELDB_FILES = ["000002.log", "000005.ldb", "000006.log", "CURRENT", "LOCK", "MANIFEST-000004"]
failures = []
passes = 0

# What Desktop's Local Storage holds when the tests start, A signed in: first what a compaction put in the table,
# then what Desktop wrote to the log after it. Text beyond Latin-1 is kept in UTF-16, and a JavaScript string may hold
# half of a surrogate pair.
SIDEBAR = {"sort": "recent", "sections": ["pinned", "mine \ud83d"]}
GROUPS = {"groups": ["work ✓"]}
DRAFT = {"text": "A's draft ✓"}
RESULT = {"state": "done", "summary": "A's result"}
FILTER_A = f"LSS-persisted.code-sessions-status-filter.{ACCOUNT_A}"
ORDER_A, ORDER_B = (f"LSS-persisted.code-projects-order.{account}" for account in (ACCOUNT_A, ACCOUNT_B))
DRAFT_A, DRAFT_B = f"composer-draft:pane:{ACCOUNT_A}:{SESSION_A}", f"composer-draft:pane:{ACCOUNT_B}:{SESSION_B}"
IN_TABLE = {
    "dframe-store": {"state": {"codeSidebarByScope": {SCOPE_A: SIDEBAR}, "customGroupsByScope": {SCOPE_A: GROUPS}},
                     "version": 3},
    "dframe-pinned-epitaxy-project-ids": ["project-shared"],
    ORDER_A: {"value": ["project-1", "project-2"]},
    FILTER_A: {"value": "running"},
    "LSS-persisted.starred-local-code-sessions": {"value": [SESSION_A]},
    f"epitaxy-session-result:{SESSION_A}": RESULT,
    DRAFT_A: {"text": "an older draft"},
    f"consentStoreStitched:v3:{ACCOUNT_A}": "stitched",
    # 90 characters without a run of 4 seen before, which Snappy keeps as one long literal.
    "antalytics_anonymous_id": "".join(map(chr, range(0x21, 0x7B))),
}
IN_LOG = {
    DRAFT_A: DRAFT,
    f"customize-connectors-rows:{ACCOUNT_A}:{ORG_A}": {"rows": ["github", "drive"]},
    f"frame-pinned-valve:{ORG_A}:{ACCOUNT_A}": "1792875570827",
    f"persisted.ccd-session-presets.{ACCOUNT_A}": {"state": {"presets": [{"name": "fast", "model": "opus"}]}},
    f"persisted.ccd-session-presets.{ACCOUNT_A}.{ORG_A}": {"state": {
        "selected": "fast", "placeDefaults": {"local::C:\\work": {"effort": "high"}, ENV_A: {"effort": "low"}}}},
    f"persisted.ccd-session-presets.{ACCOUNT_B}.{ORG_B}": {"state": {
        "selected": "slow", "pinnedPreset": "slow",
        "placeDefaults": {"local::C:\\work": {"effort": "low"}, ENV_B: {"modelId": "b-model"}}}},
    f"persisted.ccd-session-presets.{ACCOUNT_A}.{ORG_OLD}": {"state": {"selected": "old"}},
    "LSS-persisted.dframe-code-sections": {"value": {SCOPE_A: {"open": ["today"]}}},
    f"LSS-persisted.ultrathinkAvailable.claude-x.{ACCOUNT_A}": {"value": True},
    f"LSS-persisted.autoResumeRateLimit.{ACCOUNT_A}": {"value": {SESSION_A: {"on": True}}},
    f"LSS-persisted.epitaxy-auto-mode-denial-count.{ACCOUNT_A}": {"value": 2},
    f"LSS-persisted.perm-mode-acks.{ACCOUNT_A}": {"value": ["bypassPermissions"]},
    f"LSS-persisted.epitaxy-folder-permission-mode.{ACCOUNT_A}": {"value": {"C:\\work": "bypassPermissions"}},
    "epitaxy.terminalTabs.v1": {"state": {"bySession": {SESSION_A: {"tabs": 2}, SESSION_X: {"tabs": 9}}}},
    "spa:locale": "en-US",
    "ant-text": "pinned \u2713 \u00e9",
}
# Desktop's own files of settings. epitaxyPrefs keeps the persisted preferences of the window as Desktop 4.23118 keeps
# them: the name, then the account's id.
DESKTOP_CONFIG = {"preferences": {
    "bypassPermissionsGateByAccount": {ACCOUNT_A: True},
    "bypassPermissionsOptInByAccount": {ACCOUNT_A: True},
    "coworkModelAutoFallbackByAccount": {ACCOUNT_A: False},
    "epitaxyPrefs": {f"code-projects-order.{ACCOUNT_A}": ["project-1", "project-2"],
                     "dframe-code-sections": {SCOPE_A: {"open": ["today"]}},
                     "dframe-local-slice": {"pinnedOrder": ["project-shared"]},
                     f"ultrathinkAvailable.claude-x.{ACCOUNT_A}": True,
                     f"autoResumeRateLimit.{ACCOUNT_A}": {SESSION_A: {"on": True}},
                     f"epitaxy-auto-entry-warning-seen.{ACCOUNT_A}": True},
    "remoteControlPinnedFolders": ["C:\\work"]},
    "mcpServers": {}}
PRESETS = {"byAccount": {ACCOUNT_A: {"presets": [{"name": "fast"}], "defaultPreset": "fast"},
                         ACCOUNT_B: {"presets": [], "placeDefaults": {ENV_B: {"modelId": "b-model"},
                                                                      "local::C:\\work": {"effort": "low"}}}}}
TOGGLES = {"owners": {ACCOUNT_A: ["github:create_issue"]}, "version": 1}
REMOTE = {"identities": {f"cccccccc-0000-4000-8000-00000000c0c0:{ACCOUNT_A}":
                         {"folders": {"C:\\work": {"environmentId": "env_A"}}}}}

# Loads desktop.py, swaps in what TEST_PLAN names, then runs it as desktop.py would run.
RUNNER = r'''
import importlib.util, json, os, signal, sys
spec = importlib.util.spec_from_file_location("desktop", sys.argv[1])
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)
plan = json.loads(os.environ.get("TEST_PLAN", "{}"))
if plan.get("folder"):
    print(desktop.DESKTOP)
    sys.exit(0)
if "file_free" in plan:
    print(json.dumps(desktop.file_free(plan["file_free"])))
    sys.exit(0)
if "processes" in plan:
    desktop.windows_processes = lambda name=None: plan["processes"]
if "inside" in plan:
    desktop.inside_desktop = lambda: plan["inside"]
if "files_free_seconds" in plan:
    desktop.FILES_FREE_SECONDS = plan["files_free_seconds"]
if "end" in plan:
    end = plan["end"]
    def end_process(pid, force):
        with open(end["log"], "a") as log:
            log.write(json.dumps([pid, force]) + "\n")
        if pid == end.get("stand_in") and force in end.get("kills_on", []):
            os.kill(pid, signal.SIGTERM)
            return True
        return False
    desktop.end_process = end_process
if "fail_replace" in plan:
    real = os.replace
    def replace(source, target):
        if str(source).endswith(plan["fail_replace"]):
            raise PermissionError(13, "held by a test", str(source))
        return real(source, target)
    os.replace = replace
if plan.get("starts_after_save"):
    save_web = desktop.save_web
    def saved_then_started(account):
        save_web(account)
        desktop.running = lambda: 4242
    desktop.save_web = saved_then_started
sys.exit(desktop.main(sys.argv[2:]))
'''

# A stand-in that holds files the way Desktop does: "lock" as Chromium holds lockfile (others may read it, and it goes
# when the handle closes), "exclusive" as it holds its cookie database.
HOLD = r'''
import _winapi, sys, time
lock = sys.argv[1] == "lock"
handles = [_winapi.CreateFile(path, 0x40000000 | 0x80000000, 1 if lock else 0, 0, 2 if lock else 3,
                              0x04000000 if lock else 0, 0) for path in sys.argv[2:]]
print("held", flush=True)
time.sleep(300)
'''


def check(name, condition):
    global passes
    if condition:
        passes += 1
    else:
        failures.append(name)
        print("FAIL", name)


def dig(value, *path):
    """VALUE[path...], None when a step isn't there."""
    for step in path:
        try:
            value = value[step]
        except (TypeError, KeyError, IndexError):
            return None
    return value


def js(view, key):
    """The JSON value of KEY in a view of Local Storage, None when it has none."""
    try:
        return json.loads(view[key])
    except (TypeError, KeyError, ValueError):
        return None


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# LevelDB, written and read after its formats (db/log_format.md, db/write_batch.cc, table/format.cc and
# db/version_edit.cc of github.com/google/leveldb), with nothing of localstorage.py.

def crc32c(data):
    """CRC-32C bit by bit, as the standard defines it, so it shares nothing with the table localstorage.py uses."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


def mask(crc):
    return ((crc >> 15 | crc << 17) + 0xA282EAD8) & 0xFFFFFFFF


@functools.lru_cache(maxsize=None)
def checksum(kind, piece):
    """The checksum of a record of a log: the masked CRC-32C of its kind and its bytes."""
    return mask(crc32c(bytes([kind]) + piece))


def varint(value):
    out = bytearray()
    while value > 0x7F:
        out.append(value & 0x7F | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def read_varint(data, at):
    value, shift = 0, 0
    while data[at] & 0x80:
        value |= (data[at] & 0x7F) << shift
        at, shift = at + 1, shift + 7
    return value | data[at] << shift, at + 1


def raw(text):
    """TEXT as Chromium keeps a key or a value of Local Storage: 1 then Latin-1, or 0 then UTF-16LE, half of a
    surrogate pair as it is."""
    if all(ord(char) < 256 for char in text):
        return b"\x01" + text.encode("latin-1")
    return b"\x00" + text.encode("utf-16-le", errors="surrogatepass")


def text(data):
    if data[:1] == b"\x01":
        return data[1:].decode("latin-1")
    return data[1:].decode("utf-16-le", errors="surrogatepass")


def stored(value):
    """VALUE as the window stores it: text as it is, anything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def frame(position, payload):
    """PAYLOAD as LevelDB's log::Writer writes it from POSITION of a log: in records that never cross a block, a block
    with less than a header left ending in zeros."""
    out, first = bytearray(), True
    while True:
        left = BLOCK - (position + len(out)) % BLOCK
        if left < 7:
            out += bytes(left)
            continue
        piece, payload = payload[:left - 7], payload[left - 7:]
        kind = (1 if first else 4) if not payload else (2 if first else 3)
        out += struct.pack("<IHB", checksum(kind, piece), len(piece), kind) + piece
        first = False
        if not payload:
            return bytes(out)


def batch(sequence, writes):
    """A WriteBatch: its first sequence, its count, then each write, 1 for a value, 0 for a deletion."""
    out = bytearray(struct.pack("<QI", sequence, len(writes)))
    for key, value in writes:
        if value is None:
            out += b"\x00" + varint(len(key)) + key
        else:
            out += b"\x01" + varint(len(key)) + key + varint(len(value)) + value
    return bytes(out)


def batches(data):
    """The batches of a log as LevelDB's log::Reader reads them, every record checked against its checksum. Raises
    ValueError on what LevelDB would drop."""
    found, pending, at = [], None, 0
    while at < len(data):
        left = BLOCK - at % BLOCK
        if left < 7:
            if any(data[at:at + left]):
                raise ValueError(f"the end of the block at {at} is not zeros")
            at += left
            continue
        crc, length, kind = struct.unpack_from("<IHB", data, at)
        piece = data[at + 7:at + 7 + length]
        if len(piece) < length or crc != checksum(kind, piece):
            raise ValueError(f"the record at {at} is cut short or fails its checksum")
        at += 7 + length
        if kind == 1 and pending is None:
            found.append(piece)
        elif kind == 2 and pending is None:
            pending = piece
        elif kind == 3 and pending is not None:
            pending += piece
        elif kind == 4 and pending is not None:
            found.append(pending + piece)
            pending = None
        else:
            raise ValueError(f"a record of kind {kind} out of place at {at}")
    if pending is not None:
        raise ValueError("the log ends inside a record")
    return found


def writes_of(data):
    """The first sequence of a batch and its writes, the value None for a deletion."""
    sequence, count = struct.unpack_from("<QI", data)
    at, writes = 12, []
    for _ in range(count):
        kind = data[at]
        size, at = read_varint(data, at + 1)
        key, at = data[at:at + size], at + size
        value = None
        if kind == 1:
            size, at = read_varint(data, at)
            value, at = data[at:at + size], at + size
        elif kind != 0:
            raise ValueError(f"a write of kind {kind}")
        writes.append((key, value))
    if at != len(data):
        raise ValueError("a batch with bytes after its writes")
    return sequence, writes


def log_ending_at(end):
    """A log of one batch that ends at END of its first block."""
    key = ORIGIN + raw("ant-padding")
    length = end - 7 - 12 - 1 - len(varint(len(key))) - len(key) - 3
    return frame(0, batch(1, [(key, raw("x" * (length - 1)))]))


def snappy(data):
    """DATA in Snappy's format, in literals, short and long, and copies with one and two byte offsets, as Chromium
    compresses the blocks of its tables."""
    out, seen, start, at = bytearray(varint(len(data))), {}, 0, 0

    def literal(end):
        for low in range(start, end, 256):
            piece = data[low:min(low + 256, end)]
            out.extend(bytes([len(piece) - 1 << 2]) if len(piece) <= 60 else bytes([60 << 2, len(piece) - 1]))
            out.extend(piece)

    while at + 4 <= len(data):
        earlier = seen.get(data[at:at + 4])
        seen[data[at:at + 4]] = at
        if earlier is None:
            at += 1
            continue
        length = 4
        while at + length < len(data) and length < 64 and data[earlier + length] == data[at + length]:
            length += 1
        literal(at)
        offset = at - earlier
        if length <= 11 and offset < 2048:
            out += bytes([offset >> 8 << 5 | length - 4 << 2 | 1, offset & 0xFF])
        else:
            out += bytes([length - 1 << 2 | 2]) + struct.pack("<H", offset)
        at = start = at + length
    literal(len(data))
    return bytes(out)


def snappy_kinds(data):
    """The kinds of the elements of a Snappy stream: "short" and "long" literals, 1 and 2 copies with one and two byte
    offsets."""
    _, at = read_varint(data, 0)
    kinds = set()
    while at < len(data):
        tag = data[at]
        if tag & 3:
            kinds.add(tag & 3)
            at += (2, 3, 5)[(tag & 3) - 1]
        elif tag >> 2 < 60:
            kinds.add("short")
            at += 2 + (tag >> 2)
        else:
            kinds.add("long")
            at += 3 + data[at + 1]
    return kinds


def table(entries):
    """A LevelDB table of ENTRIES, (key, sequence, value or None for a deletion): a data block compressed with Snappy,
    an empty metaindex, the index, then the footer. Each block ends with its compression and the masked CRC-32C of both.
    Returns the file, its smallest and largest internal keys, and its compressed data block."""
    out = bytearray()

    def block(contents, compression):
        handle = varint(len(out)) + varint(len(contents))
        out.extend(contents + bytes([compression]) + struct.pack("<I", mask(crc32c(contents + bytes([compression])))))
        return handle

    def entries_block(items):
        body = bytearray()
        for key, value in items:
            body += varint(0) + varint(len(key)) + varint(len(value)) + key + value
        return bytes(body) + struct.pack("<II", 0, 1)

    internal = [(key + struct.pack("<Q", sequence << 8 | (value is not None)), value or b"")
                for key, sequence, value in sorted(entries, key=lambda entry: (entry[0], -entry[1]))]
    compressed = snappy(entries_block(internal))
    data = block(compressed, 1)
    metaindex = block(entries_block([]), 0)
    index = block(entries_block([(internal[-1][0], data)]), 0)
    footer = metaindex + index
    return (bytes(out) + footer + bytes(40 - len(footer)) + struct.pack("<Q", 0xDB4775248B80FB57),
            internal[0][0], internal[-1][0], compressed)


def edit(*fields):
    """A VersionEdit of the MANIFEST: each field's tag, then its numbers and its length-prefixed keys."""
    out = bytearray()
    for tag, *values in fields:
        out += varint(tag)
        for value in values:
            out += varint(len(value)) + value if isinstance(value, bytes) else varint(value)
    return bytes(out)


class Machine:
    def __init__(self, root):
        self.root = root
        self.home, self.local, self.roaming = root / "home", root / "local", root / "roaming"
        self.desktop = self.local / "Claude-Data" / "claude_test"
        self.store = self.home / ".claude-accounts"
        for folder in (self.home, self.roaming, self.desktop / "Network", self.store):
            folder.mkdir(parents=True, exist_ok=True)
        # The folder the Store build used before it moved, left with only the marker of the move.
        self.left = self.local / "Packages" / "Claude_test" / "LocalCache" / "Roaming" / "Claude"
        self.left.mkdir(parents=True)
        (self.left / ".user-data-left.claude_test").write_text("")
        self.stand_ins = []
        self.log = root / "taskkill.log"
        # A's organization is the one Desktop keeps its Code sessions under, B's the one its saved login names.
        for account, email, name, more in ((ACCOUNT_A, "a@example.com", "first", {}),
                                           (ACCOUNT_B, "b@example.com", "", {"organizationUuid": ORG_B})):
            (self.store / f"{email}_x.json").write_text(json.dumps(
                {"name": name, "oauthAccount": {"accountUuid": account, "emailAddress": email, **more}, "login": {}}))
        self.add_session(ACCOUNT_A, ORG_A, SESSION_A)
        with self.database() as db:
            db.execute("CREATE TABLE meta(key LONGVARCHAR NOT NULL UNIQUE PRIMARY KEY, value LONGVARCHAR)")
            db.execute("INSERT INTO meta VALUES ('version', '24'), ('last_compatible_version', '24')")
            db.execute(f"CREATE TABLE cookies({', '.join(name + ' ' + ('BLOB' if name == 'encrypted_value' else 'TEXT' if name in ('host_key', 'top_frame_site_key', 'name', 'value', 'path') else 'INTEGER') + ' NOT NULL' for name in COLUMNS)})")
            db.execute("CREATE UNIQUE INDEX cookies_unique_index ON cookies(host_key, top_frame_site_key, "
                       "has_cross_site_ancestor, name, path, source_scheme, source_port)")
        self.add_cookie(".claude.ai", "anthropic-device-id", b"device")
        self.write_config({"locale": "en-US", "userThemeMode": "dark"})
        self.write_json("claude_desktop_config.json", DESKTOP_CONFIG)
        for name, value in (("ccd-session-presets.json", PRESETS), ("mcp-user-tool-toggles.json", TOGGLES),
                            ("remote-control-state.json", REMOTE)):
            (self.desktop / name).write_text(json.dumps(value), encoding="utf-8")
        for path in ("Session Storage/000003.log", f"{INDEXEDDB[0]}/000003.log", f"{INDEXEDDB[1]}/1/00/1",
                     "WebStorage/QuotaManager"):
            (self.desktop / path).parent.mkdir(parents=True, exist_ok=True)
            (self.desktop / path).write_text(f"first {path}")
        self.seed_local_storage()

    def add_session(self, account, organization, session):
        folder = self.desktop / "claude-code-sessions" / account / organization
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{session}.json").write_text("{}")

    def cookie_file(self):
        return self.desktop / "Network" / "Cookies"

    @contextlib.contextmanager
    def database(self):
        """The cookie database, committed and closed after: Windows keeps a file another handle holds where it is."""
        db = sqlite3.connect(self.cookie_file())
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_cookie(self, host, name, blob):
        with self.database() as db:
            db.execute(f"INSERT INTO cookies({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                       (time.time_ns() // 1000, host, "", name, "", blob, "/", 13_500_000_000_000_000, 1, 1,
                        13_400_000_000_000_000, 1, 1, 1, 1, 2, 443, 13_400_000_000_000_000, 0, 0))

    def sign_in(self, account, tag):
        with self.database() as db:
            db.execute(f"DELETE FROM cookies WHERE host_key = '.claude.ai' AND name IN ({', '.join('?' * len(SESSION))})",
                       SESSION)
        for name in SESSION:
            self.add_cookie(".claude.ai", name, f"v10-{tag}-{name}".encode())
        config = self.config()
        config.update({"oauth:tokenCacheV2": f"djEw-{tag}", "lastKnownAccountUuid": account})
        self.write_config(config)

    def session(self):
        with self.database() as db:
            return {row[0]: row[1] for row in db.execute(
                "SELECT name, encrypted_value FROM cookies WHERE host_key = '.claude.ai'") if row[0] in SESSION}

    def config(self):
        return json.loads((self.desktop / "config.json").read_text(encoding="utf-8"))

    def write_config(self, config):
        (self.desktop / "config.json").write_text(json.dumps(config, indent="\t"), encoding="utf-8")

    def read_json(self, name):
        return json.loads((self.desktop / name).read_text(encoding="utf-8"))

    def write_json(self, name, value):
        """A settings file of Desktop, laid out as Desktop lays out claude_desktop_config.json."""
        (self.desktop / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def leveldb(self, folder=None):
        return (folder or self.desktop) / "Local Storage" / "leveldb"

    def saved(self, account):
        return self.store / ".desktop" / f"{account}.web"

    def seed_local_storage(self):
        """Desktop's Local Storage as LevelDB leaves it: IN_TABLE in a table of level 1, IN_LOG in the log after it, then
        a value that ends the log 150 bytes before its block does, so the next write is cut in two. Beside them, the
        LOCK file and a log of before the table, which LevelDB no longer reads. The MANIFEST names one more table, then
        deletes it."""
        folder = self.leveldb()
        folder.mkdir(parents=True)
        entries = [(ORIGIN + raw(key), sequence, raw(stored(value)))
                   for sequence, (key, value) in enumerate(IN_TABLE.items(), 1)]
        self.table_bytes, smallest, largest, compressed = table(entries)
        self.table_kinds = snappy_kinds(compressed)
        self.in_table, self.last_in_table = {key: value for key, _, value in entries}, len(entries)
        manifest = b""
        for record in (edit((1, b"leveldb.BytewiseComparator")),
                       edit((2, 6), (9, 0), (3, 7), (4, len(entries)), (7, 0, 3, 120, smallest, largest),
                            (7, 1, 5, len(self.table_bytes), smallest, largest)),
                       edit((6, 0, 3), (5, 1, largest))):
            manifest += frame(len(manifest), record)
        self.manifest_bytes = manifest
        (folder / "000005.ldb").write_bytes(self.table_bytes)
        (folder / "MANIFEST-000004").write_bytes(manifest)
        (folder / "CURRENT").write_text("MANIFEST-000004\n")
        (folder / "LOCK").write_bytes(b"")
        (folder / "000002.log").write_bytes(frame(0, batch(900, [(ORIGIN + raw(FILTER_A),
                                                                 raw(stored({"value": "stale"})))])))
        (folder / "000006.log").write_bytes(b"")
        self.desktop_writes(IN_LOG)
        size = (folder / "000006.log").stat().st_size
        key = ORIGIN + raw("ant-padding")
        # One record up to 150 bytes before the end of the block: a batch's header, a put, the key, a 3 byte length.
        length = BLOCK - 150 - size - 7 - 12 - 1 - len(varint(len(key))) - len(key) - 3
        self.desktop_writes({"ant-padding": "x" * (length - 1)})

    def desktop_writes(self, changes):
        """CHANGES written as Desktop writes them while it runs: one batch at the end of its log, None deleting a key."""
        log = self.leveldb() / "000006.log"
        data = log.read_bytes()
        _, last = self.local_storage()
        writes = [(ORIGIN + raw(key), None if value is None else raw(stored(value))) for key, value in changes.items()]
        log.write_bytes(data + frame(len(data), batch(last + 1, writes)))

    def local_storage(self, folder=None):
        """What LevelDB finds in Desktop's Local Storage, or in the copy saved in FOLDER: the table, which nothing may
        rewrite, then the batches of the log in their order, each one right after the last sequence before it. Returns
        the keys of claude.ai and their values, and the last sequence. Raises ValueError on what LevelDB would not
        take."""
        leveldb = self.leveldb(folder)
        names = sorted(path.name for path in leveldb.iterdir())
        if names != LEVELDB_FILES:
            raise ValueError(f"the files of the database are {names}")
        if ((leveldb / "000005.ldb").read_bytes() != self.table_bytes
                or (leveldb / "MANIFEST-000004").read_bytes() != self.manifest_bytes
                or (leveldb / "CURRENT").read_text() != "MANIFEST-000004\n"):
            raise ValueError("a file of the database other than its log was rewritten")
        values, last = dict(self.in_table), self.last_in_table
        for data in batches((leveldb / "000006.log").read_bytes()):
            sequence, writes = writes_of(data)
            if sequence != last + 1:
                raise ValueError(f"a batch starts at {sequence}, after {last}")
            last = sequence + len(writes) - 1
            for key, value in writes:
                values[key] = value
        return {text(key[len(ORIGIN):]): text(value) for key, value in values.items()
                if key.startswith(ORIGIN) and value is not None}, last

    def view(self, folder=None):
        """local_storage(FOLDER)'s keys and values, None when LevelDB would not take the database."""
        try:
            return self.local_storage(folder)[0]
        except (OSError, ValueError, struct.error, IndexError) as error:
            print("LevelDB would not take this Local Storage:", error)
            return None

    def tree(self, folder):
        """Every file under FOLDER with its bytes, but lockfile, which Desktop holds open for itself."""
        if not folder.is_dir():
            return None
        return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*"))
                if path.is_file() and path.name != "lockfile"}

    def leftovers(self):
        return sorted(str(path) for folder in (self.desktop, self.store) for path in folder.rglob("*")
                      if path.name.endswith((".switch", ".switching", ".old", ".tmp")))

    def env(self, **extra):
        base = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE", "ANTHROPIC"))}
        return dict(base, USERPROFILE=str(self.home), HOME=str(self.home), LOCALAPPDATA=str(self.local),
                    APPDATA=str(self.roaming), PYTHONDONTWRITEBYTECODE="1", **extra)

    def run(self, *args, plan=None):
        """desktop.py ARGS, through the runner, which swaps in what PLAN names. Every plan that can reach taskkill
        carries a fake one."""
        runner = self.root / "runner.py"
        runner.write_text(RUNNER, encoding="utf-8")
        return subprocess.run([sys.executable, "-I", str(runner), str(HELPER), *args], capture_output=True,
                              text=True, env=self.env(TEST_PLAN=json.dumps(plan or {})), timeout=120,
                              creationflags=NO_WINDOW)

    def hold(self, kind, *paths):
        """A stand-in process holding PATHS, as Desktop holds them."""
        process = subprocess.Popen([sys.executable, "-I", "-c", HOLD, kind, *map(str, paths)], stdout=subprocess.PIPE,
                                   text=True, creationflags=NO_WINDOW)
        self.stand_ins.append(process)
        process.stdout.readline()
        return process

    def stop(self):
        for process in self.stand_ins:
            if process.poll() is None:
                process.kill()
            process.wait()
        self.stand_ins.clear()

    def calls(self):
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        self.log.unlink(missing_ok=True)
        return calls


root = pathlib.Path(tempfile.mkdtemp(prefix="claude-switch-desktop-windows."))
try:
    # LevelDB's formats, as localstorage.py writes them.
    ls = load(HELPER.with_name("localstorage.py"), "localstorage")
    check("CRC-32C: the check value of the standard", crc32c(b"123456789") == 0xE3069283 == ls.crc32c(b"123456789"))
    body = batch(1000, [(ORIGIN + raw("k"), raw("v" * 300))])
    for end in (BLOCK - 7, BLOCK - 6, BLOCK - 3, BLOCK - 1, BLOCK):
        head = log_ending_at(end)
        try:
            found = batches(head + ls.frame(len(head), body))
        except ValueError as error:
            found = [str(error)]
        check(f"a record written {BLOCK - end} bytes before the end of a block: read as LevelDB reads it",
              len(head) == end and found[-1:] == [body])

    m = Machine(root)
    check(f"fixture: the table's block holds short and long literals and both kinds of copies, it holds "
          f"{sorted(map(str, m.table_kinds))}", m.table_kinds == {"short", "long", 1, 2})
    check("fixture: the log ends 150 bytes before its block", (m.leveldb() / "000006.log").stat().st_size == BLOCK - 150)
    check("fixture: LevelDB takes its Local Storage", m.view() is not None)

    # Which folder is Desktop's.
    folder = m.run(plan={"folder": True}).stdout.strip()
    check("folder: Claude-Data, not the one the Store build left", pathlib.Path(folder) == m.desktop)
    shutil.move(str(m.local / "Claude-Data"), str(root / "claude-data-aside"))
    folder = m.run(plan={"folder": True}).stdout.strip()
    check("folder: a folder with only the marker of the move is not Desktop's", pathlib.Path(folder) == m.roaming / "Claude")
    (m.left / ".user-data-left.claude_test").unlink()
    (m.left / "config.json").write_text("{}")
    folder = m.run(plan={"folder": True}).stdout.strip()
    check("folder: the package's copy of %APPDATA% before the move", pathlib.Path(folder) == m.left)
    (m.left / "config.json").unlink()
    (m.left / ".user-data-left.claude_test").write_text("")
    shutil.move(str(root / "claude-data-aside"), str(m.local / "Claude-Data"))

    status = json.loads(m.run("status").stdout)
    check("status: closed, signed out, its folder", not status["running"] and not status["signedIn"]
          and pathlib.Path(status["folder"]) == m.desktop)

    # To B: A's settings go to B, and what is A's alone stays A's.
    m.sign_in(ACCOUNT_A, "A")
    a_session = m.session()
    before, log = m.view(), (m.leveldb() / "000006.log").read_bytes()
    web = {name: m.tree(m.desktop / name) for name in WEB}
    remote = (m.desktop / "remote-control-state.json").read_bytes()
    result = m.run("switch", ACCOUNT_B)
    after = m.view()
    check("to B: switched, asks for one sign-in, says what it carried over",
          result.returncode == 0 and "sign in" in result.stdout and "carried over" in result.stdout)
    check("to B: LevelDB takes its Local Storage, the log only grew",
          after is not None and (m.leveldb() / "000006.log").read_bytes().startswith(log))
    check("to B: A's window storage saved with it", {name: m.tree(m.saved(ACCOUNT_A) / name) for name in WEB} == web)
    sidebar = js(after, "dframe-store")
    check("to B: A's sidebar sections and groups go to B, A keeps its own",
          dig(sidebar, "state", "codeSidebarByScope", SCOPE_B) == SIDEBAR
          and dig(sidebar, "state", "customGroupsByScope", SCOPE_B) == GROUPS
          and dig(sidebar, "state", "codeSidebarByScope", SCOPE_A) == SIDEBAR and dig(sidebar, "version") == 3)
    check("to B: A's project order, out of the table", js(after, ORDER_B) == IN_TABLE[ORDER_A])
    check("to B: the status filter LevelDB has, not the one of a log it no longer reads",
          js(after, f"LSS-persisted.code-sessions-status-filter.{ACCOUNT_B}") == IN_TABLE[FILTER_A])
    check("to B: A's presets, and B keeps the defaults of its own environment",
          js(after, f"persisted.ccd-session-presets.{ACCOUNT_B}") == IN_LOG[f"persisted.ccd-session-presets.{ACCOUNT_A}"]
          and js(after, f"persisted.ccd-session-presets.{ACCOUNT_B}.{ORG_B}") == {"state": {
              "selected": "fast", "placeDefaults": {"local::C:\\work": {"effort": "high"}, ENV_A: {"effort": "low"},
                                                    ENV_B: {"modelId": "b-model"}}}})
    check("to B: the sections the Code tab keeps open",
          dig(js(after, "LSS-persisted.dframe-code-sections"), "value", SCOPE_B) == {"open": ["today"]})
    check("to B: only B's copies were added, the permission mode of each folder among them, not A's connections, what "
          "auto mode and the permission modes warned it of, its sessions' auto resume, nor what claude.ai found A may "
          "use", set(after or {}) - set(before) == {
              ORDER_B, f"LSS-persisted.code-sessions-status-filter.{ACCOUNT_B}", f"persisted.ccd-session-presets.{ACCOUNT_B}",
              f"LSS-persisted.epitaxy-folder-permission-mode.{ACCOUNT_B}"})
    check("to B: A's draft and its consent stay A's, and nothing else changed",
          {key for key in before if dig(after, key) != before[key]} ==
          {"dframe-store", "LSS-persisted.dframe-code-sections", f"persisted.ccd-session-presets.{ACCOUNT_B}.{ORG_B}"})
    preferences = dig(m.read_json("claude_desktop_config.json"), "preferences")
    check("to B: the bypass permissions gate stays A's alone",
          dig(preferences, "bypassPermissionsGateByAccount") == {ACCOUNT_A: True}
          and dig(preferences, "bypassPermissionsOptInByAccount") == {ACCOUNT_A: True})
    check("to B: Desktop's preferences of A go to B, named after it or kept under its scope",
          dig(preferences, "coworkModelAutoFallbackByAccount", ACCOUNT_B) is False
          and dig(preferences, "epitaxyPrefs", f"code-projects-order.{ACCOUNT_B}") == ["project-1", "project-2"]
          and dig(preferences, "epitaxyPrefs", "dframe-code-sections", SCOPE_B) == {"open": ["today"]}
          and dig(preferences, "remoteControlPinnedFolders") == ["C:\\work"])
    check("to B: the pins stay as they are, and what A's own Desktop preferences keep stays A's",
          dig(preferences, "epitaxyPrefs") == {**DESKTOP_CONFIG["preferences"]["epitaxyPrefs"],
                                               f"code-projects-order.{ACCOUNT_B}": ["project-1", "project-2"],
                                               "dframe-code-sections": {SCOPE_A: {"open": ["today"]},
                                                                        SCOPE_B: {"open": ["today"]}}})
    layout = (m.desktop / "claude_desktop_config.json").read_text(encoding="utf-8")
    check("to B: claude_desktop_config.json keeps its layout and the order of its keys",
          layout.startswith('{\n  "preferences": {\n    "bypassPermissionsGateByAccount"') and layout.endswith("}\n"))
    check("to B: A's presets and tool toggles go to B, B keeps the defaults of its own environment",
          dig(m.read_json("ccd-session-presets.json"), "byAccount", ACCOUNT_B) ==
          {"presets": [{"name": "fast"}], "defaultPreset": "fast", "placeDefaults": {ENV_B: {"modelId": "b-model"}}}
          and m.read_json("mcp-user-tool-toggles.json") == {"owners": {ACCOUNT_A: ["github:create_issue"],
                                                                     ACCOUNT_B: ["github:create_issue"]}, "version": 1})
    check("to B: Remote Control's environments stay A's", (m.desktop / "remote-control-state.json").read_bytes() == remote)
    check("to B: the rest of config.json stays", m.config()["userThemeMode"] == "dark")

    # B's Desktop: B changes its groups and its project order, writes a draft, and drops what is A's.
    m.sign_in(ACCOUNT_B, "B")
    b_session = m.session()
    m.add_session(ACCOUNT_B, ORG_B, SESSION_B)
    sidebar = js(m.view(), "dframe-store")
    for scopes in sidebar["state"].values():
        scopes.pop(SCOPE_A, None)
    groups_b = {"groups": ["work", "home"]}
    sidebar["state"]["customGroupsByScope"][SCOPE_B] = groups_b
    m.desktop_writes({DRAFT_A: None, f"epitaxy-session-result:{SESSION_A}": None,
                      f"consentStoreStitched:v3:{ACCOUNT_A}": None, "dframe-store": sidebar,
                      "epitaxy.terminalTabs.v1": {"state": {"bySession": {SESSION_B: {"tabs": 1}}}},
                      ORDER_B: {"value": ["project-3", "project-1"]}, DRAFT_B: {"text": "B's draft"},
                      "spa:locale": None})
    prior = m.view()

    # Back to A: B's settings come with it, and what A lost while away comes back.
    result = m.run("switch", ACCOUNT_A)
    back = m.view()
    check("back to A: switched, says what it carried over and put back",
          result.returncode == 0 and "first" in result.stdout and "carried over" in result.stdout
          and "put back" in result.stdout)
    check("back to A: its sign-in", m.session() == a_session and m.config()["lastKnownAccountUuid"] == ACCOUNT_A)
    check("back to A: its draft as it last wrote it, not the older one of the table", js(back, DRAFT_A) == DRAFT)
    check("back to A: its session's result and its consent",
          js(back, f"epitaxy-session-result:{SESSION_A}") == RESULT
          and dig(back, f"consentStoreStitched:v3:{ACCOUNT_A}") == "stitched")
    check("back to A: its session's terminal tabs, beside B's, not those of a session of nobody it knows",
          js(back, "epitaxy.terminalTabs.v1") == {"state": {"bySession": {SESSION_B: {"tabs": 1}, SESSION_A: {"tabs": 2}}}})
    check("back to A: a shared setting B removed stays removed", back is not None and "spa:locale" not in back)
    sidebar = js(back, "dframe-store")
    check("back to A: the sidebar and the groups as B left them",
          dig(sidebar, "state", "customGroupsByScope", SCOPE_A) == groups_b
          and dig(sidebar, "state", "codeSidebarByScope", SCOPE_A) == SIDEBAR)
    check("back to A: the project order B set", js(back, ORDER_A) == {"value": ["project-3", "project-1"]})
    check("back to A: what is B's alone stays as it was",
          {key: value for key, value in (back or {}).items() if ACCOUNT_B in key or SESSION_B in key}
          == {key: value for key, value in prior.items() if ACCOUNT_B in key or SESSION_B in key})
    check("back to A: B's window storage saved with it", m.view(m.saved(ACCOUNT_B)) == prior)
    preferences = dig(m.read_json("claude_desktop_config.json"), "preferences")
    check("back to A: the bypass permissions gate is A's alone", dig(preferences, "bypassPermissionsGateByAccount") ==
          {ACCOUNT_A: True} and dig(preferences, "bypassPermissionsOptInByAccount") == {ACCOUNT_A: True})
    result = m.run("switch", ACCOUNT_B)
    check("to B again: its sign-in, its draft", result.returncode == 0 and m.session() == b_session
          and js(m.view(), DRAFT_B) == {"text": "B's draft"})
    check("nothing left aside", m.leftovers() == [])

    # A Local Storage it doesn't understand is left as it is, and the switch goes on.
    log = m.leveldb() / "000006.log"
    m.desktop_writes({ORDER_B: {"value": ["project-4"]}})
    good = log.read_bytes()
    log.write_bytes(good + b"\x01torn")
    kept = m.tree(m.leveldb())
    result = m.run("switch", ACCOUNT_A)
    check("a log that doesn't end with its last record: switched, says what it left",
          result.returncode == 0 and m.session() == a_session and "Not carried over" in result.stdout
          and "the window's storage" in result.stdout)
    check("a log that doesn't end with its last record: left as it was", m.tree(m.leveldb()) == kept)
    log.write_bytes(good)
    m.desktop_writes({ORDER_A: {"value": ["project-5"]}})
    good = log.read_bytes()
    log.write_bytes(good[:40] + bytes([good[40] ^ 0x20]) + good[41:])
    kept = m.tree(m.leveldb())
    result = m.run("switch", ACCOUNT_B)
    check("a record that fails its checksum: switched, says what it left",
          result.returncode == 0 and m.session() == b_session and "Not carried over" in result.stdout)
    check("a record that fails its checksum: left as it was", m.tree(m.leveldb()) == kept)
    log.write_bytes(good)
    m.desktop_writes({ORDER_B: {"value": ["project-6"]}})
    shared = m.table_bytes.find(b"shared")
    check("fixture: a value of the table in a literal of its block", shared > 0)
    (m.leveldb() / "000005.ldb").write_bytes(m.table_bytes[:shared] + b"S" + m.table_bytes[shared + 1:])
    kept = m.tree(m.leveldb())
    result = m.run("switch", ACCOUNT_A)
    check("a table block that fails its checksum: switched, says what it left",
          result.returncode == 0 and m.session() == a_session and "Not carried over" in result.stdout)
    check("a table block that fails its checksum: left as it was", m.tree(m.leveldb()) == kept)
    (m.leveldb() / "000005.ldb").write_bytes(m.table_bytes)

    # Desktop started while the sign-in of A was being saved: nothing of Desktop changes, with databases to put back or
    # without.
    m.desktop_writes({ORDER_A: {"value": ["project-7"]}})
    config = m.read_json("claude_desktop_config.json")
    config["preferences"]["epitaxyPrefs"][f"code-projects-order.{ACCOUNT_A}"] = ["project-7"]
    m.write_json("claude_desktop_config.json", config)
    kept = m.tree(m.desktop)
    result = m.run("switch", ACCOUNT_B, plan={"starts_after_save": True})
    check("Desktop started during the switch: refused with 3, nothing of Desktop changed",
          result.returncode == 3 and m.tree(m.desktop) == kept)
    shutil.copytree(m.leveldb(), root / "local-storage-aside")
    shutil.rmtree(m.leveldb())
    kept = m.tree(m.desktop)
    result = m.run("switch", ACCOUNT_B, plan={"starts_after_save": True})
    check("Desktop started during the switch, with Local Storage to put back: refused with 3, nothing changed",
          result.returncode == 3 and m.tree(m.desktop) == kept)
    shutil.move(str(root / "local-storage-aside"), str(m.leveldb()))

    # IndexedDB and WebStorage are the account's: B's come back with its sign-in, even when they went whole, and the
    # copy saved for A keeps its own.
    kept_b = {path: m.tree(m.saved(ACCOUNT_B) / path) for path in (*INDEXEDDB, "WebStorage")}
    kept_a = {path: m.tree(m.saved(ACCOUNT_A) / path) for path in (*INDEXEDDB, "WebStorage")}
    for path in (*INDEXEDDB, "WebStorage"):
        shutil.rmtree(m.desktop / path)
    result = m.run("switch", ACCOUNT_B)
    check("IndexedDB and WebStorage gone: switched", result.returncode == 0 and m.session() == b_session)
    check("IndexedDB and WebStorage gone: B's come back whole",
          {path: m.tree(m.desktop / path) for path in kept_b} == kept_b and all(kept_b.values()))
    check("IndexedDB and WebStorage gone: the copy saved for A keeps its own",
          {path: m.tree(m.saved(ACCOUNT_A) / path) for path in kept_a} == kept_a and all(kept_a.values()))
    # The copy saved for B took the broken table with it, at the last switch from B.
    check("a saved copy it can't read: says so, and A's settings go to B all the same",
          "Not put back" in result.stdout and js(m.view(), ORDER_B) == {"value": ["project-7"]}
          and dig(m.read_json("claude_desktop_config.json"), "preferences", "epitaxyPrefs",
                  f"code-projects-order.{ACCOUNT_B}") == ["project-7"])

    # A saved copy with a record that fails its checksum: nothing of it is read, so nothing comes back from it. B's
    # Desktop emptied its IndexedDB in place before: A's comes back with A all the same, and B keeps its own.
    m.desktop_writes({f"consentStoreStitched:v3:{ACCOUNT_A}": None})
    saved_log = m.leveldb(m.saved(ACCOUNT_A)) / "000006.log"
    data = saved_log.read_bytes()
    saved_log.write_bytes(data[:40] + bytes([data[40] ^ 0x20]) + data[41:])
    (m.desktop / INDEXEDDB[0] / "000003.log").write_text("emptied while on B")
    emptied = {path: m.tree(m.desktop / path) for path in (*INDEXEDDB, "WebStorage")}
    result = m.run("switch", ACCOUNT_A)
    now = m.view()
    check("a saved copy with a record that fails its checksum: switched, says nothing came back from it",
          result.returncode == 0 and m.session() == a_session and "Not put back" in result.stdout)
    check("a saved copy with a record that fails its checksum: nothing of it read",
          now is not None and f"consentStoreStitched:v3:{ACCOUNT_A}" not in now
          and not any(key.startswith("!") for key in now))
    check("IndexedDB emptied in place while on B: A's own back with its sign-in",
          {path: m.tree(m.desktop / path) for path in kept_a} == kept_a)
    check("IndexedDB emptied in place while on B: saved with B as B left it",
          {path: m.tree(m.saved(ACCOUNT_B) / path) for path in emptied} == emptied)

    # A settings file it can't write stays as it was, and the others are carried over.
    m.write_json("ccd-session-presets.json", {"byAccount": {ACCOUNT_A: {"presets": []}}})
    presets = (m.desktop / "ccd-session-presets.json").read_bytes()
    config = m.read_json("claude_desktop_config.json")
    config["preferences"]["coworkModelAutoFallbackByAccount"][ACCOUNT_A] = True
    m.write_json("claude_desktop_config.json", config)
    m.write_json("mcp-user-tool-toggles.json", {"owners": {ACCOUNT_A: ["drive:search"], ACCOUNT_B: []}, "version": 1})
    result = m.run("switch", ACCOUNT_B, plan={"fail_replace": "ccd-session-presets.json.switch"})
    check("a settings file it can't write: switched, names that file alone",
          result.returncode == 0 and m.session() == b_session and "ccd-session-presets.json" in result.stdout
          and "claude_desktop_config.json" not in result.stdout)
    check("a settings file it can't write: left as it was", (m.desktop / "ccd-session-presets.json").read_bytes() == presets)
    check("a settings file it can't write: the others carried over",
          dig(m.read_json("claude_desktop_config.json"), "preferences", "coworkModelAutoFallbackByAccount", ACCOUNT_B)
          is True and dig(m.read_json("mcp-user-tool-toggles.json"), "owners", ACCOUNT_B) == ["drive:search"])
    check("back on B: its own IndexedDB, as it left it", {path: m.tree(m.desktop / path) for path in emptied} == emptied)
    check("nothing left aside after all that", m.leftovers() == [])

    # A switch cut in the middle of the swap left a database of Desktop aside: the next switch puts it back first.
    os.replace(m.desktop / INDEXEDDB[0], m.desktop / f"{INDEXEDDB[0]}.old")
    (m.desktop / "WebStorage.switching").mkdir()
    result = m.run("switch", ACCOUNT_A)
    check("a cut swap: repaired before B is saved", result.returncode == 0 and m.session() == a_session
          and m.tree(m.saved(ACCOUNT_B) / INDEXEDDB[0]) == emptied[INDEXEDDB[0]])
    check("a cut swap: nothing left aside", m.leftovers() == [])

    # A swap that fails is undone whole, and the sign-in stays.
    kept = {path: m.tree(m.desktop / path) for path in (*INDEXEDDB, "WebStorage")}
    result = m.run("switch", ACCOUNT_B, plan={"fail_replace": "https_claude.ai_0.indexeddb.blob.switching"})
    check("a failed swap: refused", result.returncode not in (0, 3, 4, 6))
    check("a failed swap: the sign-in and the databases stay", m.session() == a_session
          and m.config()["lastKnownAccountUuid"] == ACCOUNT_A
          and {path: m.tree(m.desktop / path) for path in kept} == kept)
    check("a failed swap: nothing left aside", m.leftovers() == [])

    # Local Storage gone whole comes back whole from the copy saved for the account.
    shutil.rmtree(m.leveldb())
    result = m.run("switch", ACCOUNT_B)
    check("Local Storage gone: B's comes back whole, says so", result.returncode == 0 and m.session() == b_session
          and "1 put back" in result.stdout and js(m.view(), DRAFT_B) == {"text": "B's draft"})

    # Two accounts of one organization: what A keeps under its scope goes to C's, as to any other account.
    m.run("switch", ACCOUNT_A)
    (m.store / "c@example.com_x.json").write_text(json.dumps({"name": "", "oauthAccount": {
        "accountUuid": ACCOUNT_C, "emailAddress": "c@example.com", "organizationUuid": ORG_A}, "login": {}}))
    result = m.run("switch", ACCOUNT_C)
    check("to C, of A's organization: A's sidebar goes to C's scope", result.returncode == 0
          and dig(js(m.view(), "dframe-store"), "state", "codeSidebarByScope", f"{ACCOUNT_C}/{ORG_A}") == SIDEBAR)
    result = m.run("switch", ACCOUNT_A)
    check("back to A from C, which never signed in: A's sign-in", result.returncode == 0 and m.session() == a_session)

    # Desktop open: it holds its cookie database for itself alone.
    kept = (m.tree(m.desktop), m.tree(m.store))
    stand_in = m.hold("lock", m.desktop / "lockfile")
    database = m.hold("exclusive", m.cookie_file())
    status = json.loads(m.run("status").stdout)
    check("open: status from config.json", status["running"] and status["signedIn"] and status["account"] == ACCOUNT_A)
    result = m.run("switch", ACCOUNT_A)
    check("open, already on A: fine", result.returncode == 0 and "already" in result.stdout)
    result = m.run("switch", ACCOUNT_B, plan={"inside": False})
    check("open: refused with 3", result.returncode == 3)
    result = m.run("switch", ACCOUNT_B, plan={"inside": True})
    check("open and running this switch: refused with 6, with how to bring it along",
          result.returncode == 6 and "claude-switch b@example.com" in result.stderr)
    database.kill()
    database.wait()
    check("open: nothing changed", (m.tree(m.desktop), m.tree(m.store)) == kept)

    # A quit never ends a Desktop it can't tell, nor the one that runs it.
    end = {"log": str(m.log), "stand_in": stand_in.pid}
    result = m.run("quit", "3", plan={"inside": False, "end": dict(end, kills_on=[True])})
    check("quit, no helper names the folder: refused, nothing ended",
          result.returncode == 1 and m.calls() == [] and stand_in.poll() is None)
    helper = {"ProcessId": 999999, "ParentProcessId": stand_in.pid, "ExecutablePath": r"C:\x\Claude.exe",
              "CommandLine": f'"C:\\x\\Claude.exe" --type=renderer --user-data-dir="{m.desktop}"'}
    processes = [{"ProcessId": stand_in.pid, "ParentProcessId": 1, "ExecutablePath": r"C:\x\Claude.exe",
                  "CommandLine": '"C:\\x\\Claude.exe"'}, helper]
    result = m.run("quit", "3", plan={"processes": processes, "inside": True, "end": dict(end, kills_on=[True])})
    check("quit from inside Desktop: refused, nothing ended",
          result.returncode == 1 and "by hand" in result.stderr and m.calls() == [] and stand_in.poll() is None)

    # A Desktop that stays in the tray when its windows close ends with its tree, then the helper left behind.
    result = m.run("quit", "3", plan={"processes": processes, "inside": False, "end": dict(end, kills_on=[True])})
    check("quit, tray: ended", result.returncode == 0 and stand_in.wait(timeout=10) is not None)
    check("quit, tray: windows asked first, then the tree, then the helper",
          m.calls() == [[stand_in.pid, False], [stand_in.pid, True], [999999, True]])
    # A Desktop that quits when its windows close is not forced.
    stand_in = m.hold("lock", m.desktop / "lockfile")
    processes[0]["ProcessId"] = helper["ParentProcessId"] = stand_in.pid
    result = m.run("quit", "3", plan={"processes": processes, "inside": False,
                                      "end": dict(end, stand_in=stand_in.pid, kills_on=[False])})
    check("quit, closes on its own: not forced",
          result.returncode == 0 and m.calls() == [[stand_in.pid, False], [999999, True]])
    # Its files still held by a process that outlives it: the quit says so.
    stand_in = m.hold("lock", m.desktop / "lockfile")
    lock = m.hold("exclusive", m.leveldb() / "LOCK")
    processes[0]["ProcessId"] = helper["ParentProcessId"] = stand_in.pid
    result = m.run("quit", "3", plan={"processes": processes, "inside": False, "files_free_seconds": 1,
                                      "end": dict(end, stand_in=stand_in.pid, kills_on=[True])})
    check("quit, files still held: says which", result.returncode == 1 and "LOCK" in result.stderr)
    m.calls()
    lock.kill()
    lock.wait()
    check("a file nobody holds is free", json.loads(m.run(plan={"file_free": str(m.cookie_file())}).stdout))
    held = m.hold("exclusive", m.cookie_file())
    check("a file Desktop holds is not", not json.loads(m.run(plan={"file_free": str(m.cookie_file())}).stdout))
    held.kill()
    held.wait()
finally:
    try:
        m.stop()
    except NameError:
        pass
    shutil.rmtree(root, ignore_errors=True)

print(f"{passes} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
