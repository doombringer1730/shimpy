import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from shimpy.rootfs import default_release as _default_release, _debootstrap_tool, build_rootfs
from shimpy.util import BuildError


class TestDefaultRelease(unittest.TestCase):
    def test_debian_amd64(self):
        self.assertEqual(_default_release("debian", "amd64"), "bookworm")

    def test_ubuntu_amd64(self):
        self.assertEqual(_default_release("ubuntu", "amd64"), "noble")

    def test_unknown_distro(self):
        with self.assertRaises(BuildError):
            _default_release("gentoo", "amd64")


class TestDebootstrapTool(unittest.TestCase):
    @patch("shutil.which", return_value=None)
    def test_raises_when_neither_found(self, _):
        with self.assertRaises(BuildError):
            _debootstrap_tool()

    @patch("shutil.which", side_effect=lambda t: "/usr/bin/debootstrap" if t == "debootstrap" else None)
    def test_returns_debootstrap(self, _):
        self.assertEqual(_debootstrap_tool(), "debootstrap")

    @patch("shutil.which", side_effect=lambda t: "/usr/bin/mmdebstrap" if t == "mmdebstrap" else None)
    def test_prefers_mmdebstrap(self, _):
        self.assertEqual(_debootstrap_tool(), "mmdebstrap")


class TestBuildRootfs(unittest.TestCase):
    @patch("shimpy.rootfs.configure_rootfs")
    @patch("shimpy.rootfs._bootstrap_debian")
    def test_debian_calls_debootstrap(self, mock_bootstrap, mock_configure):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            build_rootfs(
                target=Path(d) / "rootfs",
                distro="debian",
                release="bookworm",
                arch="amd64",
                extra_packages=["vim"],
                hostname="shimpy-test",
                username="shimpy",
                password="shimpy",
                verbose=False,
            )
        mock_bootstrap.assert_called_once()
        args = mock_bootstrap.call_args
        self.assertEqual(args[0][1], "bookworm")
        self.assertEqual(args[0][2], "amd64")
        self.assertIn("vim", args[0][3])

    @patch("shimpy.rootfs.configure_rootfs")
    @patch("shimpy.rootfs._bootstrap_ubuntu")
    def test_ubuntu_calls_ubuntu_bootstrap(self, mock_bootstrap, mock_configure):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            build_rootfs(
                target=Path(d) / "rootfs",
                distro="ubuntu",
                release="noble",
                arch="amd64",
                extra_packages=[],
                hostname="shimpy-test",
                username="shimpy",
                password="shimpy",
                verbose=False,
            )
        mock_bootstrap.assert_called_once()

    @patch("shimpy.rootfs.configure_rootfs")
    @patch("shimpy.rootfs._bootstrap_debian")
    def test_uses_default_release_when_none(self, mock_bootstrap, mock_configure):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            build_rootfs(
                target=Path(d) / "rootfs",
                distro="debian",
                release=None,
                arch="amd64",
                extra_packages=[],
                hostname="shimpy-test",
                username="shimpy",
                password="shimpy",
                verbose=False,
            )
        args = mock_bootstrap.call_args
        self.assertEqual(args[0][1], "bookworm")

    def test_raises_for_unknown_distro(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(BuildError):
                build_rootfs(
                    target=Path(d) / "rootfs",
                    distro="gentoo",
                    release=None,
                    arch="amd64",
                    extra_packages=[],
                    hostname="shimpy-test",
                    username="shimpy",
                    password="shimpy",
                    verbose=False,
                )


if __name__ == "__main__":
    unittest.main()
