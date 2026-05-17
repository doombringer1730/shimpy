import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shimpy.initramfs import detect_fs_type, parse_partition_table
from shimpy.util import BuildError


class TestParsePartitionTable(unittest.TestCase):
    # cgpt show output: start_lba, size_lba, part_num, Label: "name"
    # ROOT-A: start=10240*512=5242880, size=524288*512=268435456
    CGPT_OUTPUT = (
        "       start        size    part  contents\n"
        "          34        2014       1  Label: \"STATE\"\n"
        "        2048        8192       2  Label: \"KERN-A\"\n"
        "       10240      524288       3  Label: \"ROOT-A\"\n"
        "      534528        8192       4  Label: \"KERN-B\"\n"
    )

    @patch("shimpy.initramfs.run_output")
    def test_parses_all_partitions(self, mock_run):
        mock_run.return_value = self.CGPT_OUTPUT
        parts = parse_partition_table(Path("fake.bin"))
        self.assertIn("ROOT-A", parts)
        self.assertIn("KERN-A", parts)
        self.assertIn("STATE", parts)

    @patch("shimpy.initramfs.run_output")
    def test_partition_fields(self, mock_run):
        mock_run.return_value = self.CGPT_OUTPUT
        parts = parse_partition_table(Path("fake.bin"))
        root_a = parts["ROOT-A"]
        self.assertEqual(root_a["num"], 3)
        self.assertEqual(root_a["start"], 5242880)
        self.assertEqual(root_a["size"], 268435456)

    @patch("shimpy.initramfs.run_output")
    def test_raises_for_missing_partition(self, mock_run):
        mock_run.return_value = self.CGPT_OUTPUT
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
