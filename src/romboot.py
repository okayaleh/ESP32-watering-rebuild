"""USB-installed ROM cache helper; call after OTA recovery, before app imports.

The root filesystem remains authoritative. Two ROM banks allow interrupted
cache builds to leave the previously committed image untouched. No application
module, configuration or credential is imported by this helper.
"""
import os
import sys
try:
    import hashlib
except ImportError:
    import uhashlib as hashlib
try:
    import binascii
except ImportError:
    import ubinascii as binascii
try:
    import json
except ImportError:
    import ujson as json

CHUNK = 512
STATE = "_rom_state.json"
MOUNT = "/rom"
EXCLUDED = ("boot", "main", "config", "romboot", "secrets", "secret",
            "credentials", "wifi_config")


def _path(root, name):
    return root.rstrip("/") + "/" + name


def _uint(value):
    result = bytes((value & 127,))
    value >>= 7
    while value:
        result = bytes((128 | (value & 127),)) + result
        value >>= 7
    return result


def _hex(digest):
    return binascii.hexlify(digest.digest()).decode()


def _files(root):
    entries = []
    for name in sorted(os.listdir(root)):
        suffix = ".mpy" if name.endswith(".mpy") else ".py" if name.endswith(".py") else None
        if suffix is None:
            continue
        stem = name[:-len(suffix)]
        # Flat, importable module names only; never config.example.py or hidden
        # OTA staging files. Include both source/bytecode siblings so normal
        # MicroPython .py-before-.mpy import precedence is preserved.
        if (not stem or stem.lower() in EXCLUDED or len(name) > 64 or
                stem[0] in "0123456789" or any(c not in
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
                for c in stem)):
            continue
        stat = os.stat(_path(root, name))
        if stat[0] & 0x4000:
            continue
        entries.append((name, stat[6]))
    return entries


def _fingerprint_start():
    digest = hashlib.sha256()
    digest.update(b"Planter-ROM-cache-v1\x00")
    return digest


def _fingerprint_entry(digest, name, size):
    encoded = name.encode("ascii")
    digest.update(_uint(len(encoded)))
    digest.update(encoded)
    digest.update(_uint(size))


def _fingerprint(root, entries):
    digest = _fingerprint_start()
    for name, size in entries:
        _fingerprint_entry(digest, name, size)
        count = 0
        with open(_path(root, name), "rb") as stream:
            while True:
                data = stream.read(CHUNK)
                if not data:
                    break
                count += len(data)
                digest.update(data)
        if count != size:
            raise OSError("Module changed during ROM fingerprint")
    return _hex(digest)


def _file_header(name, size):
    encoded = name.encode("ascii")
    data_header = b"\x02" + _uint(size)
    name_header = _uint(len(encoded)) + encoded
    return b"\x05" + _uint(len(name_header) + len(data_header) + size) + name_header + data_header


def _layout(entries):
    size = sum(len(_file_header(name, length)) + length for name, length in entries)
    length = _uint(size)
    if (3 + len(length) + size) & 1:
        length = b"\x80" + length
    header = b"\xd2\xcd\x31" + length
    return header, len(header) + size


def _checked(result):
    if result is not None and result != 0:
        raise OSError("ROM partition operation failed: %s" % result)


def _partition_hash(partition, length, block_size):
    digest = hashlib.sha256()
    buffer = bytearray(CHUNK)
    view = memoryview(buffer)
    offset = 0
    while offset < length:
        piece = view[:min(CHUNK, length - offset)]
        _checked(partition.readblocks(offset // block_size, piece, offset % block_size))
        digest.update(piece)
        offset += len(piece)
    return _hex(digest)


def _write_image(root, entries, fingerprint, partition, block_size, header, length):
    # Extended writeblocks never erases. Erase only the inactive bank, then
    # stream <=512-byte writes, without allocating a whole image/erase sector.
    for block in range((length + block_size - 1) // block_size):
        _checked(partition.ioctl(6, block))
    image_hash = hashlib.sha256()
    source_hash = _fingerprint_start()
    offset = 0

    def write(data):
        nonlocal offset
        if len(data) > CHUNK or offset + len(data) > length:
            raise OSError("ROM image exceeds measured size")
        _checked(partition.writeblocks(offset // block_size, data, offset % block_size))
        image_hash.update(data)
        offset += len(data)

    write(header)
    for name, size in entries:
        write(_file_header(name, size))
        _fingerprint_entry(source_hash, name, size)
        count = 0
        with open(_path(root, name), "rb") as stream:
            while True:
                data = stream.read(CHUNK)
                if not data:
                    break
                count += len(data)
                if count > size:
                    raise OSError("Module grew during ROM build")
                source_hash.update(data)
                write(data)
        if count != size:
            raise OSError("Module shrank during ROM build")
    if (offset != length or _hex(source_hash) != fingerprint or
            _files(root) != entries):
        raise OSError("Modules changed during ROM build")
    _checked(partition.ioctl(3, 0))
    digest = _hex(image_hash)
    if _partition_hash(partition, length, block_size) != digest:
        raise OSError("ROM image readback mismatch")
    return digest


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _read(path):
    try:
        with open(path) as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def _valid_state(value):
    if (not isinstance(value, dict) or type(value.get("version")) is not int or
            value["version"] != 1):
        return False
    if type(value.get("active")) is not int or value["active"] not in (0, 1):
        return False
    if type(value.get("length")) is not int or value["length"] < 4:
        return False
    for key in ("fingerprint", "sha256"):
        item = value.get(key)
        if (not isinstance(item, str) or len(item) != 64 or
                any(c not in "0123456789abcdef" for c in item)):
            return False
    return True


def _read_state(path):
    for candidate in (path, path + ".prev"):
        value = _read(candidate)
        if _valid_state(value):
            return value
    return None


def _sync():
    if hasattr(os, "sync"):
        os.sync()


def _atomic_json(path, value):
    # Self-contained: the boot helper must not import application persistence
    # code before the verified ROM is mounted.
    with open(path + ".tmp", "w") as stream:
        json.dump(value, stream)
        stream.flush()
    _sync()
    if _read(path + ".tmp") != value:
        raise OSError("ROM marker readback mismatch")
    if _exists(path):
        if _valid_state(_read(path)):
            if _exists(path + ".prev"):
                os.remove(path + ".prev")
            os.rename(path, path + ".prev")
        else:
            os.remove(path)
    os.rename(path + ".tmp", path)
    _sync()


def prepare(root=".", vfs_module=None, search_path=None):
    """Mount root's verified app image; False means dual-ROM is unsupported.

    All other errors propagate: boot must keep valves closed and retry/recover,
    rather than execute stale cached modules after an incomplete update.
    Call only before importing application modules; never during a live run.
    """
    if vfs_module is None:
        try:
            import vfs as vfs_module
        except ImportError:
            return False
    if not hasattr(vfs_module, "VfsRom") or not hasattr(vfs_module, "rom_ioctl"):
        return False
    if vfs_module.rom_ioctl(1) < 2:
        return False
    banks = (vfs_module.rom_ioctl(2, 0), vfs_module.rom_ioctl(2, 1))
    sizes = []
    blocks = []
    regions = []
    for index, partition in enumerate(banks):
        info = partition.info()
        if info[0] != 1 or info[4] != ("romfs", "romfs_b")[index]:
            raise OSError("Unexpected ROM partition identity")
        block_size = partition.ioctl(5, 0)
        if (block_size != 4096 or info[3] < block_size or info[3] % block_size or
                info[2] % block_size or info[5]):
            raise OSError("Unsupported ROM partition geometry")
        sizes.append(info[3])
        blocks.append(block_size)
        regions.append((info[2], info[2] + info[3]))
    if max(regions[0][0], regions[1][0]) < min(regions[0][1], regions[1][1]):
        raise OSError("ROM cache partitions overlap")
    for module in sys.modules.values():
        if str(getattr(module, "__file__", "")).startswith(MOUNT + "/"):
            raise RuntimeError("ROM cache preparation requires a fresh boot")
    # Firmware may have automatically mounted bank 0. Detach it before reading
    # root modules or writing a bank; no cached application code is executing.
    paths = sys.path if search_path is None else search_path
    for entry in (MOUNT, MOUNT + "/lib"):
        while entry in paths:
            paths.remove(entry)
    try:
        vfs_module.umount(MOUNT)
    except OSError as exc:
        if not exc.args or exc.args[0] not in (2, 22):
            raise
    entries = _files(root)
    if not entries:
        raise OSError("No application modules for ROM cache")
    fingerprint = _fingerprint(root, entries)
    header, length = _layout(entries)
    marker = _path(root, STATE)
    previous = _read_state(marker)
    active = previous["active"] if previous else 1
    ready = False
    if (previous and previous["fingerprint"] == fingerprint and
            previous["length"] == length and length <= sizes[active]):
        ready = (_partition_hash(banks[active], length, blocks[active]) == previous["sha256"])
    if not ready:
        active = 1 - active
        if length > sizes[active]:
            raise OSError("Application exceeds ROM cache partition")
        digest = _write_image(root, entries, fingerprint, banks[active],
                              blocks[active], header, length)
        # Constructor checks ROM format before committing the bank selection.
        filesystem = vfs_module.VfsRom(memoryview(banks[active]))
        _atomic_json(marker, {"version": 1, "active": active,
                     "fingerprint": fingerprint, "sha256": digest, "length": length})
    else:
        filesystem = vfs_module.VfsRom(memoryview(banks[active]))
    vfs_module.mount(filesystem, MOUNT)
    paths.insert(0, MOUNT)
    return True
