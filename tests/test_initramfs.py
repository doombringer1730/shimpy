import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shimpy.initramfs import detect_fs_type, parse_partition_table
from shimpy.util import BuildError


class TestParsePartitionTable(unittest.TestCase):
    PARTED_OUTPUT = (
        "BYT;\n"
        "/dev/sda:1073741824B:file:512:512:gpt:Shim Image:;\n"
        "1:17408B:1048575B:1031168B::STATE:;\n"
        "2:1048576B:5242879B:4194304B::KERN-A:;\n"
        "3:5242880B:273678335B:268435456B::ROOT-A:;\n"
        "4:273678336B:277872639B:4194304B::KERN-B:;\n"
    )

    @patch("shimpy.initramfs.run_output")
    def test_parses_all_partitions(self, mock_run):
        mock_run.return_value = self.PARTED_OUTPUT
        parts = parse_partition_table(Path("fake.bin"))
        self.assertIn("ROOT-A", parts)
        self.assertIn("KERN-A", parts)
        self.assertIn("STATE", parts)

    @patch("shimpy.initramfs.run_output")
    def test_partition_fields(self, mock_run):
        mock_run.return_value = self.PARTED_OUTPUT
        parts = parse_partition_table(Path("fake.bin"))
        root_a = parts["ROOT-A"]
        self.assertEqual(root_a["num"], 3)
        self.assertEqual(root_a["start"], 5242880)
        self.assertEqual(root_a["size"], 268435456)

    @patch("shimpy.initramfs.run_output")
    def test_raises_for_missing_partition(self, mock_run):
        mock_run.return_value = self.PARTED_OUTPUT
        parts = parse_partition_table(Path("fake.bin"))
        from shimpy.initramfs import find_partition
        with self.assertRaises(BuildError):
            find_partition(parts, "SHIMPY-ROOT")


class TestDetectFsType(unittest.TestCase):
    @patch("shimpy.initramfs.run_output")
    def test_detects_ext4(self, mock_run):
        mock_run.return_value = "Linux rev 1.0 ext2 filesystem data"
        result = detect_fs_type(Path("fake.img"))
        self.assertIn("ext", result)

    @patch("shimpy.initramfs.run_output")
    def test_detects_squashfs(self, mock_run):
        mock_run.return_value = "Squashfs filesystem, little endian, version 4.0"
        result = detect_fs_type(Path("fake.img"))
        self.assertEqual(result, "squashfs")

    @patch("shimpy.initramfs.run_output")
    def test_raises_on_unknown(self, mock_run):
        mock_run.return_value = "data"
        # Also mock blkid call
        with patch("shimpy.initramfs.run_output", side_effect=["data", ""]):
            with self.assertRaises(BuildError):
                detect_fs_type(Path("fake.img"))


if __name__ == "__main__":
    unittest.main()
