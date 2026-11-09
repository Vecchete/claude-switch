"""Chromium's Local Storage on disk, a LevelDB database: what it holds, and one batch of writes added to it. desktop.py
uses it on Windows, while Claude Desktop is closed, to carry the settings of one account over to the next.

It never compacts and never rewrites a file of the database: a write is one record appended to the log file LevelDB
replays at its next open. It refuses a database with a record or a table block that fails its checksum, and a log that
doesn't end right after its last record, so a database it doesn't understand is left as it was.

LevelDB's formats: doc/log_format.md, doc/table_format.md and db/version_edit.cc of github.com/google/leveldb.
Chromium keeps a key of an origin as _<origin>, a 0 byte and the key, and starts every key and value with 1 for
Latin-1 text or 0 for UTF-16LE (components/services/storage/dom_storage).
"""
import os
import pathlib
import struct

BLOCK = 32768
FULL, FIRST, MIDDLE, LAST = 1, 2, 3, 4


class Unreadable(Exception):
    """The database is not one this can read or write safely."""


def _crc_table():
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
        table.append(crc)
    return table


CRC = _crc_table()


def crc32c(data, crc=0):
    """CRC-32C, extended from CRC as LevelDB's crc32c::Extend does."""
    crc ^= 0xFFFFFFFF
    for byte in data:
        crc = CRC[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def masked(crc):
    """A CRC-32C as LevelDB stores it, masked (util/crc32c.h)."""
    return (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def checksum(kind, payload):
    """The checksum LevelDB writes for a record of KIND."""
    return masked(crc32c(payload, crc32c(bytes([kind]))))


def varint(data, at):
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, at
        shift += 7


def encode_varint(value):
    out = bytearray()
    while value >= 0x80:
        out.append(value & 0x7F | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def read_log(data, verify):
    """The whole records of a LevelDB log file, and the offset right after the last one. Zeros LevelDB left at the end
    of a block are skipped. Raises Unreadable on a record that fails its checksum when VERIFY."""
    found, pending, at, end = [], None, 0, 0
    while at + 7 <= len(data):
        left = BLOCK - at % BLOCK
        if left < 7:
            at += left
            continue
        stored, length, kind = struct.unpack_from("<IHB", data, at)
        if stored == length == kind == 0:
            at += left
            continue
        payload = data[at + 7:at + 7 + length]
        if len(payload) < length or kind not in (FULL, FIRST, MIDDLE, LAST):
            break
        if verify and checksum(kind, payload) != stored:
            raise Unreadable(f"a record at {at} fails its checksum")
        at += 7 + length
        if kind == FULL:
            found.append(bytes(payload))
            pending, end = None, at
        elif kind == FIRST:
            pending = bytearray(payload)
        elif pending is not None:
            pending += payload
            if kind == LAST:
                found.append(bytes(pending))
                pending, end = None, at
    return found, end


def frame(position, body):
    """BODY as the records of a LevelDB log, written from POSITION in the file."""
    out, first = bytearray(), True
    while True:
        left = BLOCK - (position + len(out)) % BLOCK
        if left < 7:
            out += bytes(left)
            continue
        chunk, body = body[:left - 7], body[left - 7:]
        kind = (FULL if not body else FIRST) if first else (LAST if not body else MIDDLE)
        out += struct.pack("<IHB", checksum(kind, chunk), len(chunk), kind) + chunk
        first = False
        if not body:
            return bytes(out)


def batch_entries(record):
    """The writes of a WriteBatch record: (sequence, key, value), value None for a deletion."""
    sequence, count = struct.unpack_from("<QI", record, 0)
    at = 12
    for index in range(count):
        kind = record[at]
        at += 1
        size, at = varint(record, at)
        key = record[at:at + size]
        at += size
        value = None
        if kind == 1:
            size, at = varint(record, at)
            value = record[at:at + size]
            at += size
        elif kind != 0:
            raise Unreadable(f"a batch holds a write of kind {kind}")
        yield sequence + index, key, value


def encode_batch(sequence, writes):
    body = bytearray(struct.pack("<QI", sequence, len(writes)))
    for key, value in writes:
        if value is None:
            body += b"\x00" + encode_varint(len(key)) + key
        else:
            body += b"\x01" + encode_varint(len(key)) + key + encode_varint(len(value)) + value
    return bytes(body)


def snappy(data):
    size, at = varint(data, 0)
    out = bytearray()
    while at < len(data):
        tag = data[at]
        at += 1
        if tag & 3 == 0:
            length = tag >> 2
            if length >= 60:
                count = length - 59
                length = int.from_bytes(data[at:at + count], "little")
                at += count
            length += 1
            out += data[at:at + length]
            at += length
            continue
        if tag & 3 == 1:
            length, offset = 4 + ((tag >> 2) & 7), ((tag >> 5) << 8) | data[at]
            at += 1
        elif tag & 3 == 2:
            length, offset = 1 + (tag >> 2), int.from_bytes(data[at:at + 2], "little")
            at += 2
        else:
            length, offset = 1 + (tag >> 2), int.from_bytes(data[at:at + 4], "little")
            at += 4
        if not 0 < offset <= len(out):
            raise Unreadable("a compressed block points outside itself")
        for index in range(length):
            out.append(out[len(out) - offset])
    if len(out) != size:
        raise Unreadable("a compressed block has the wrong size")
    return bytes(out)


def table_block(data, offset, size):
    """A block of a table file, checked against the checksum after it, which covers the block and its compression."""
    raw, compression = data[offset:offset + size], data[offset + size]
    if masked(crc32c(data[offset:offset + size + 1])) != struct.unpack_from("<I", data, offset + size + 1)[0]:
        raise Unreadable(f"a table block at {offset} fails its checksum")
    if compression == 1:
        return snappy(raw)
    if compression != 0:
        raise Unreadable(f"a table block uses compression {compression}")
    return raw


def block_entries(block):
    restarts = struct.unpack_from("<I", block, len(block) - 4)[0]
    end, at, key = len(block) - 4 - 4 * restarts, 0, b""
    while at < end:
        shared, at = varint(block, at)
        unshared, at = varint(block, at)
        size, at = varint(block, at)
        key = key[:shared] + block[at:at + unshared]
        at += unshared
        yield key, block[at:at + size]
        at += size


def table_entries(data):
    """The entries of a table file: (key, sequence, value), value None for a deletion."""
    if len(data) < 48 or struct.unpack_from("<Q", data, len(data) - 8)[0] != 0xDB4775248B80FB57:
        raise Unreadable("a table file has no LevelDB footer")
    footer = data[-48:]
    _, at = varint(footer, 0)
    _, at = varint(footer, at)
    offset, at = varint(footer, at)
    size, at = varint(footer, at)
    for _, handle in block_entries(table_block(data, offset, size)):
        offset, at = varint(handle, 0)
        size, at = varint(handle, at)
        for internal, value in block_entries(table_block(data, offset, size)):
            tag = struct.unpack_from("<Q", internal, len(internal) - 8)[0]
            yield internal[:-8], tag >> 8, value if tag & 0xFF == 1 else None


def manifest(folder):
    """The log number, the previous log number, the last sequence and the live table files of the database."""
    name = (folder / "CURRENT").read_text(encoding="ascii").strip()
    log_number = previous = last = 0
    live = set()
    records, _ = read_log((folder / name).read_bytes(), verify=True)
    for record in records:
        at = 0
        while at < len(record):
            tag, at = varint(record, at)
            if tag == 1:
                size, at = varint(record, at)
                at += size
            elif tag == 2:
                log_number, at = varint(record, at)
            elif tag == 3:
                _, at = varint(record, at)
            elif tag == 4:
                last, at = varint(record, at)
            elif tag == 5:
                _, at = varint(record, at)
                size, at = varint(record, at)
                at += size
            elif tag == 6:
                _, at = varint(record, at)
                number, at = varint(record, at)
                live.discard(number)
            elif tag == 7:
                _, at = varint(record, at)
                number, at = varint(record, at)
                _, at = varint(record, at)
                for _ in range(2):
                    size, at = varint(record, at)
                    at += size
                live.add(number)
            elif tag == 9:
                previous, at = varint(record, at)
            else:
                raise Unreadable(f"the MANIFEST holds an edit of kind {tag}")
    return log_number, previous, last, live


def encode_text(text):
    """TEXT as Chromium keeps it: Latin-1 when it can, else UTF-16LE. A lone surrogate of a JavaScript string goes back
    as it came."""
    try:
        return b"\x01" + text.encode("latin-1")
    except UnicodeEncodeError:
        return b"\x00" + text.encode("utf-16-le", errors="surrogatepass")


def decode_text(raw):
    if raw[:1] == b"\x01":
        return raw[1:].decode("latin-1")
    if raw[:1] == b"\x00":
        return raw[1:].decode("utf-16-le", errors="surrogatepass")
    return raw.decode("latin-1")


class Database:
    """A LevelDB database as LevelDB would find it at its next open."""

    def __init__(self, folder):
        self.folder = pathlib.Path(folder)
        log_number, previous, self.last, live = manifest(self.folder)
        self.values = {}
        for number in sorted(live):
            path = next((self.folder / f"{number:06d}{suffix}" for suffix in (".ldb", ".sst")
                         if (self.folder / f"{number:06d}{suffix}").is_file()), None)
            if path is None:
                raise Unreadable(f"the table {number} of the MANIFEST is missing")
            for key, sequence, value in table_entries(path.read_bytes()):
                self._put(key, sequence, value)
        logs = sorted(int(path.stem) for path in self.folder.glob("*.log")
                      if path.stem.isdigit() and (int(path.stem) >= log_number or int(path.stem) == previous))
        self.log = self.folder / f"{logs[-1]:06d}.log" if logs else None
        for number in logs:
            data = (self.folder / f"{number:06d}.log").read_bytes()
            records, _ = read_log(data, verify=True)
            for record in records:
                for sequence, key, value in batch_entries(record):
                    self._put(key, sequence, value)
        self.last = max([self.last, *(sequence for sequence, _ in self.values.values())])

    def _put(self, key, sequence, value):
        if key not in self.values or sequence >= self.values[key][0]:
            self.values[key] = (sequence, value)

    def get(self, key):
        return self.values.get(key, (0, None))[1]

    def origin(self, origin):
        """The keys and values of ORIGIN in Chromium's Local Storage, as text."""
        prefix = f"_{origin}".encode() + b"\x00"
        return {decode_text(key[len(prefix):]): decode_text(value) for key, (_, value) in self.values.items()
                if key.startswith(prefix) and value is not None}

    def write(self, origin, changes):
        """CHANGES, text keys of ORIGIN and their text values (None deletes one), as one batch at the end of the log.
        Returns how many."""
        if not changes:
            return 0
        if self.log is None:
            raise Unreadable("the database has no log to add to")
        data = self.log.read_bytes()
        records, end = read_log(data, verify=True)
        if any(data[end:]):
            raise Unreadable("the log doesn't end with its last record")
        prefix = f"_{origin}".encode() + b"\x00"
        writes = [(prefix + encode_text(key), None if value is None else encode_text(value))
                  for key, value in sorted(changes.items())]
        sequence = self.last + 1
        out = frame(end, encode_batch(sequence, writes))
        with open(self.log, "r+b") as handle:
            handle.seek(end)
            handle.write(out)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
        for index, (key, value) in enumerate(writes):
            self._put(key, sequence + index, value)
        self.last = sequence + len(writes) - 1
        return len(writes)
