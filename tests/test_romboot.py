import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import romboot

try:
    from mpremote.romfs import VfsRomWriter
except ImportError:
    # The workspace installs development packages locally; other machines use
    # the mpremote dependency in requirements-dev.txt.
    writer_path = ROOT / ".tools/python/mpremote/romfs.py"
    writer_spec = importlib.util.spec_from_file_location("romfs_reference_writer", writer_path)
    writer_module = importlib.util.module_from_spec(writer_spec)
    writer_spec.loader.exec_module(writer_module)
    VfsRomWriter = writer_module.VfsRomWriter


class PowerCut(BaseException):
    pass


class Partition(bytearray):
    def __init__(self, label, address, size=262144):
        super().__init__(b"\xff" * size)
        self.label, self.address = label, address
        self.erases = []
        self.writes = []
        self.reads = []
        self.fail_write = None
        self.fail_erase = None
        self.corrupt_read = False
        self.on_write = None

    def info(self):
        return (1, 0, self.address, len(self), self.label, False)

    def ioctl(self, operation, argument):
        if operation == 5:
            return 4096
        if operation == 3:
            return 0
        if operation == 6:
            if self.fail_erase == len(self.erases):
                raise PowerCut()
            self.erases.append(argument)
            start = argument * 4096
            assert start + 4096 <= len(self)
            self[start:start + 4096] = b"\xff" * 4096
            return 0
        raise AssertionError(operation)

    def writeblocks(self, block, data, offset):
        assert 0 < len(data) <= 512
        if self.fail_write == len(self.writes):
            raise PowerCut()
        start = block * 4096 + offset
        assert start + len(data) <= len(self)
        # Extended writes cannot turn erased bits back to one without erase.
        assert all((before & after) == after
                   for before, after in zip(self[start:start + len(data)], data))
        self[start:start + len(data)] = data
        self.writes.append((block, offset, len(data)))
        if self.on_write:
            self.on_write()

    def readblocks(self, block, buffer, offset):
        assert 0 < len(buffer) <= 512
        start = block * 4096 + offset
        buffer[:] = self[start:start + len(buffer)]
        if self.corrupt_read:
            buffer[0] ^= 1
        self.reads.append((block, offset, len(buffer)))


def decode_uint(data, cursor):
    value = 0
    while True:
        byte = data[cursor]
        cursor += 1
        value = value * 128 + (byte & 127)
        if byte < 128:
            return value, cursor


class RomFilesystem:
    def __init__(self, data):
        self.data = data
        if bytes(data[:3]) != b"\xd2\xcd\x31":
            raise OSError("Invalid ROM magic")
        size, cursor = decode_uint(data, 3)
        end = cursor + size
        if end > len(data) or end % 2:
            raise OSError("Invalid ROM size")
        self.length = end
        self.files = {}
        while cursor < end:
            kind, cursor = decode_uint(data, cursor)
            record_size, cursor = decode_uint(data, cursor)
            record_end = cursor + record_size
            if kind != 5 or record_end > end:
                raise OSError("Invalid file record")
            name_size, cursor = decode_uint(data, cursor)
            name = bytes(data[cursor:cursor + name_size]).decode("ascii")
            cursor += name_size
            kind, cursor = decode_uint(data, cursor)
            file_size, cursor = decode_uint(data, cursor)
            if kind != 2 or cursor + file_size != record_end:
                raise OSError("Invalid data record")
            self.files[name] = bytes(data[cursor:record_end])
            cursor = record_end


class Vfs:
    def __init__(self, size=262144, count=2):
        self.banks = (Partition("romfs", 0x290000, size),
                      Partition("romfs_b", 0x290000 + size, size))
        self.count = count
        self.mounted = None
        self.calls = []
        self.format_error = False
        self.mount_error = False

    def rom_ioctl(self, operation, *args):
        if operation == 1:
            return self.count
        if operation == 2:
            return self.banks[args[0]]
        raise AssertionError(operation)

    def VfsRom(self, data):
        if self.format_error:
            raise OSError("ROM parser rejected image")
        return RomFilesystem(data)

    def umount(self, path):
        assert path == "/rom"
        self.calls.append(("umount", path))
        if self.mounted is None:
            raise OSError(22)
        self.mounted = None

    def mount(self, filesystem, path):
        assert path == "/rom"
        self.calls.append(("mount", path))
        if self.mount_error:
            raise OSError("Mount failed")
        self.mounted = filesystem


class RomBootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vfs = Vfs()
        self.paths = ["", "/lib", "/rom", "/rom/lib", "/rom"]
        self.write("alpha.mpy", b"M\x06\x03\x1f" + bytes(range(256)) * 4)

    def write(self, name, data):
        (self.root / name).write_bytes(data)

    def prepare(self):
        return romboot.prepare(str(self.root), self.vfs, self.paths)

    def state(self):
        return json.loads((self.root / romboot.STATE).read_text())

    def test_image_exactly_matches_mpremote_writer_at_varuint_boundaries(self):
        for size in (0, 1, 125, 126, 127, 128, 16380, 16383, 16384):
            with self.subTest(size=size):
                self.write("alpha.mpy", bytes(range(251)) * (size // 251) + bytes(range(size % 251)))
                self.write("zeta.py", b"sample = 1\n")
                self.assertTrue(self.prepare())
                reference = VfsRomWriter()
                for name, unused in romboot._files(str(self.root)):
                    reference.mkfile(name, (self.root / name).read_bytes())
                expected = bytes(reference.finalise())
                state = self.state()
                bank = self.vfs.banks[state["active"]]
                self.assertEqual(bytes(bank[:state["length"]]), expected)
                self.assertEqual(state["length"], len(expected))
                self.assertEqual(state["sha256"], hashlib.sha256(expected).hexdigest())
                self.assertEqual(self.vfs.mounted.files["alpha.mpy"], (self.root / "alpha.mpy").read_bytes())

    def test_only_flat_application_modules_are_cached(self):
        excluded = ("boot.py", "boot.mpy", "main.py", "main.mpy", "config.py",
                    "config.mpy", "config.example.py", "romboot.py", "romboot.mpy",
                    "secrets.py", "credentials.mpy", "wifi_config.py", "wifi.json",
                    ".hidden.py", "bad-name.py", "9invalid.py", "index.html")
        for name in excluded:
            self.write(name, b"DO NOT CACHE")
        (self.root / "directory.py").mkdir()
        self.write("alpha.py", b"source wins\n")
        self.assertTrue(self.prepare())
        self.assertEqual(set(self.vfs.mounted.files), {"alpha.py", "alpha.mpy"})
        before = self.state()
        self.write("config.py", b"CHANGED SECRET")
        self.assertTrue(self.prepare())
        self.assertEqual(self.state(), before)

    def test_identical_boot_validates_hash_without_erases_or_rewrites(self):
        self.assertTrue(self.prepare())
        before = self.state()
        counts = [(len(b.erases), len(b.writes), len(b.reads)) for b in self.vfs.banks]
        self.assertEqual(before["active"], 0)
        self.assertTrue(self.prepare())
        self.assertEqual(self.state(), before)
        for index, bank in enumerate(self.vfs.banks):
            self.assertEqual((len(bank.erases), len(bank.writes)), counts[index][:2])
        self.assertGreater(len(self.vfs.banks[0].reads), counts[0][2])
        self.assertEqual(self.paths, ["/rom", "", "/lib"])

    def test_changes_and_rollback_alternate_banks_preserving_previous(self):
        original = (self.root / "alpha.mpy").read_bytes()
        self.prepare()
        old_state = self.state()
        old_image = bytes(self.vfs.banks[0])
        self.write("alpha.mpy", b"replacement")
        self.prepare()
        self.assertEqual(self.state()["active"], 1)
        self.assertEqual(bytes(self.vfs.banks[0]), old_image)
        self.assertNotEqual(self.state()["fingerprint"], old_state["fingerprint"])
        self.write("alpha.mpy", original)
        self.prepare()
        self.assertEqual(self.state()["active"], 0)
        self.assertEqual(self.state()["fingerprint"], old_state["fingerprint"])

    def test_added_removed_and_same_size_changed_modules_invalidate_cache(self):
        self.prepare()
        fingerprint = self.state()["fingerprint"]
        original = (self.root / "alpha.mpy").read_bytes()
        self.write("alpha.mpy", original[:-1] + bytes((original[-1] ^ 1,)))
        self.prepare()
        self.assertNotEqual(self.state()["fingerprint"], fingerprint)
        self.write("beta.py", b"new = True\n")
        self.prepare()
        self.assertIn("beta.py", self.vfs.mounted.files)
        (self.root / "beta.py").unlink()
        self.prepare()
        self.assertNotIn("beta.py", self.vfs.mounted.files)

    def test_corrupt_active_bank_rebuilds_other_bank(self):
        self.prepare()
        before = self.state()
        self.vfs.banks[0][20] ^= 1
        self.prepare()
        self.assertEqual(self.state()["active"], 1)
        self.assertEqual(self.state()["fingerprint"], before["fingerprint"])
        self.assertEqual(self.state()["sha256"], before["sha256"])

    def test_power_cut_during_erase_or_write_preserves_committed_bank(self):
        for phase in ("erase", "write"):
            with self.subTest(phase=phase):
                self.vfs = Vfs()
                for filename in (romboot.STATE, romboot.STATE + ".prev"):
                    (self.root / filename).unlink(missing_ok=True)
                self.prepare()
                state = self.state()
                image = bytes(self.vfs.banks[0])
                self.write("alpha.mpy", (self.root / "alpha.mpy").read_bytes() + b"change")
                setattr(self.vfs.banks[1], "fail_" + phase, 0 if phase == "erase" else 2)
                with self.assertRaises(PowerCut):
                    self.prepare()
                self.assertEqual(self.state(), state)
                self.assertEqual(bytes(self.vfs.banks[0]), image)
                self.assertIsNone(self.vfs.mounted)
                setattr(self.vfs.banks[1], "fail_" + phase, None)
                self.prepare()
                self.assertEqual(self.state()["active"], 1)

    def test_readback_or_format_failure_never_commits_marker(self):
        self.prepare()
        before = self.state()
        self.write("alpha.mpy", b"changed")
        self.vfs.banks[1].corrupt_read = True
        with self.assertRaisesRegex(OSError, "readback"):
            self.prepare()
        self.assertEqual(self.state(), before)
        self.vfs.banks[1].corrupt_read = False
        self.vfs.format_error = True
        with self.assertRaisesRegex(OSError, "parser"):
            self.prepare()
        self.assertEqual(self.state(), before)
        self.assertIsNone(self.vfs.mounted)

    def test_interrupted_atomic_marker_rename_recovers_previous(self):
        self.prepare()
        before = self.state()
        self.write("alpha.mpy", b"changed")
        rename = os.rename
        def fail_final(source, target):
            if str(source).endswith(romboot.STATE + ".tmp"):
                raise PowerCut()
            return rename(source, target)
        with patch.object(romboot.os, "rename", fail_final):
            with self.assertRaises(PowerCut):
                self.prepare()
        self.assertFalse((self.root / romboot.STATE).exists())
        self.assertEqual(romboot._read_state(str(self.root / romboot.STATE)), before)
        self.assertIsNone(self.vfs.mounted)
        self.prepare()
        self.assertEqual(self.state()["active"], 1)

    def test_corrupt_current_marker_uses_previous_and_preserves_it_on_failure(self):
        self.prepare()
        before = self.state()
        (self.root / romboot.STATE).rename(self.root / (romboot.STATE + ".prev"))
        self.write(romboot.STATE, b'{"active":false}')
        self.write("alpha.mpy", b"changed")
        self.vfs.banks[1].fail_write = 0
        with self.assertRaises(PowerCut):
            self.prepare()
        self.assertEqual(romboot._read_state(str(self.root / romboot.STATE)), before)
        self.vfs.banks[1].fail_write = None
        self.prepare()
        self.assertEqual(self.state()["active"], 1)
        self.assertEqual(json.loads((self.root / (romboot.STATE + ".prev")).read_text()), before)

    def test_changed_source_during_build_is_not_committed(self):
        self.prepare()
        before = self.state()
        self.write("alpha.mpy", b"new source")
        def change_once():
            self.vfs.banks[1].on_write = None
            self.write("alpha.mpy", b"bad source")
        self.vfs.banks[1].on_write = change_once
        with self.assertRaisesRegex(OSError, "changed"):
            self.prepare()
        self.assertEqual(self.state(), before)
        self.assertIsNone(self.vfs.mounted)

    def test_oversized_image_refused_before_partition_erase(self):
        self.vfs = Vfs(size=4096)
        self.write("alpha.mpy", b"x" * 4096)
        with self.assertRaisesRegex(OSError, "exceeds"):
            self.prepare()
        self.assertFalse(any(bank.erases for bank in self.vfs.banks))
        self.assertFalse((self.root / romboot.STATE).exists())

    def test_stock_and_single_bank_firmware_return_false_without_changes(self):
        original_paths = self.paths[:]
        original_files = sorted(os.listdir(self.root))
        for vfs in (types.SimpleNamespace(), Vfs(count=1)):
            self.assertFalse(romboot.prepare(str(self.root), vfs, self.paths))
            self.assertEqual(self.paths, original_paths)
            self.assertEqual(sorted(os.listdir(self.root)), original_files)
            if isinstance(vfs, Vfs):
                self.assertEqual(vfs.calls, [])
                self.assertFalse(any(bank.erases for bank in vfs.banks))

    def test_running_rom_application_refuses_any_mutation(self):
        module = types.SimpleNamespace(__file__="/rom/alpha.mpy")
        with patch.dict(sys.modules, {"romboot_test_live_module": module}):
            with self.assertRaisesRegex(RuntimeError, "fresh boot"):
                self.prepare()
        self.assertEqual(self.vfs.calls, [])
        self.assertFalse(any(bank.erases for bank in self.vfs.banks))
        self.assertFalse((self.root / romboot.STATE).exists())

    def test_unexpected_partition_label_refuses_erases(self):
        self.vfs.banks[1].label = "vfs"
        with self.assertRaisesRegex(OSError, "identity"):
            self.prepare()
        self.assertEqual(self.vfs.calls, [])
        self.assertFalse(any(bank.erases for bank in self.vfs.banks))

    def test_overlapping_partitions_refuse_erases(self):
        self.vfs.banks[1].address = self.vfs.banks[0].address
        with self.assertRaisesRegex(OSError, "overlap"):
            self.prepare()
        self.assertEqual(self.vfs.calls, [])
        self.assertFalse(any(bank.erases for bank in self.vfs.banks))

    def test_failure_to_unmount_never_writes_partition(self):
        with patch.object(self.vfs, "umount", side_effect=OSError(16)):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertFalse(any(bank.erases for bank in self.vfs.banks))
        self.assertFalse((self.root / romboot.STATE).exists())

    def test_source_stream_reads_are_bounded_to_512_bytes(self):
        original_open = open
        read_sizes = []
        class BoundedReader:
            def __init__(self, path, mode):
                self.stream = original_open(path, mode)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def read(self, count):
                self.assert_bound(count)
                return self.stream.read(count)
            def assert_bound(self, count):
                if not 0 < count <= 512:
                    raise AssertionError("unbounded source read")
                read_sizes.append(count)
        def bounded_open(path, mode="r"):
            if str(path).endswith(".mpy") and mode == "rb":
                return BoundedReader(path, mode)
            return original_open(path, mode)
        with patch("builtins.open", bounded_open):
            self.prepare()
        self.assertGreater(len(read_sizes), 2)
        self.assertLessEqual(max(size for bank in self.vfs.banks for _, _, size in bank.writes), 512)


if __name__ == "__main__":
    unittest.main()
