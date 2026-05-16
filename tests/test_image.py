import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from shimpy.image import (
    _align_up,
    _find_partition_num,
    copy_shim,
    extend_image,
    write_checksum,
)


class TestAlignUp(unittest.TestCase):
    def test_already_aligned(self):
        self.assertEqual(_align_up(1024 * 1024, 1024 * 1024), 1024 * 1024)

    def test_needs_alignment(self):
        self.assertEqual(_align_up(1, 1024 * 1024), 1024 * 1024)

    def test_zero(self):
        self.assertEqual(_align_up(0, 1024 * 1024), 0)


class TestCopyShim(unittest.TestCase):
    def test_copies_file(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "shim.bin"
            dst = Path(d) / "out.bin"
            src.write_bytes(b"\x00" * 1024)
            copy_shim(src, dst)
            self.assertEqual(dst.read_bytes(), src.read_bytes())


class TestExtendImage(unittest.TestCase):
    def test_extends_file(self):
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "image.bin"
            img.write_bytes(b"\x00" * (2 * 1024 * 1024))
            start = extend_image(img, 4)
            self.assertGreater(img.stat().st_size, 2 * 1024 * 1024)
            self.assertEqual(start % (1024 * 1024), 0)

    def test_returns_aligned_start(self):
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "image.bin"
            img.write_bytes(b"\x00" * (1024 * 1024))
            start = extend_image(img, 4)
            self.assertEqual(start, 1024 * 1024)


class TestWriteChecksum(unittest.TestCase):
    def test_writes_correct_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "shimpy-test.bin"
            data = b"hello shimpy"
            img.write_bytes(data)
            out = write_checksum(img)
            expected = hashlib.sha256(data).hexdigest()
            content = out.read_text()
            self.assertIn(expected, content)
            self.assertIn("shimpy-test.bin", content)


class TestFindPartitionNum(unittest.TestCase):
    PARTED_OUTPUT = (
        "BYT;\n"
        "/dev/loop0:8388608B:loopback:512:512:gpt:Loopback device:;\n"
        "1:1048576B:2097151B:1048576B::STATE:;\n"
        "2:2097152B:3145727B:1048576B::ROOT-A:;\n"
        "3:3145728B:4194303B:1048576B::SHIMPY-ROOT:;\n"
    )

    @patch("shimpy.image.run_output")
    def test_finds_correct_partition(self, mock_run_output):
        mock_run_output.return_value = self.PARTED_OUTPUT
        num = _find_partition_num(Path("fake.bin"), "SHIMPY-ROOT")
        self.assertEqual(num, 3)

    @patch("shimpy.image.run_output")
    def test_raises_for_missing_partition(self, mock_run_output):
        mock_run_output.return_value = self.PARTED_OUTPUT
        from shimpy.util import BuildError
        with self.assertRaises(BuildError):
            _find_partition_num(Path("fake.bin"), "KERN-A")


if __name__ == "__main__":
    unittest.main()
