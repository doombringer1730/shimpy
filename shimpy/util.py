import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path


REQUIRED_TOOLS = [
    "parted", "cgpt", "sgdisk", "losetup", "mkfs.ext4", "debugfs", "dd", "truncate",
    "cpio", "mount", "umount", "blkid", "file",
]

OPTIONAL_TOOLS = {
    "debootstrap": "required for --distro debian",
    "mmdebstrap": "alternative to debootstrap",
    "unsquashfs": "required if shim ROOT-A is squashfs",
    "mksquashfs": "required if shim ROOT-A is squashfs",
}


class BuildError(Exception):
    pass


def check_tools(extra: list[str] | None = None) -> None:
    tools = REQUIRED_TOOLS + (extra or [])
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        raise BuildError(
            f"Missing required tools: {', '.join(missing)}\n"
            "Install them and try again:\n"
            f"  sudo apt-get install {' '.join(missing)}"
        )


def check_root() -> None:
    if os.geteuid() != 0:
        raise BuildError(
            "shimpy build requires root privileges (for losetup, mount, debootstrap).\n"
            "Run with: sudo shimpy build ..."
        )


def run(
    cmd: list,
    *,
    check: bool = True,
    capture: bool = False,
    verbose: bool = False,
    input: bytes | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    if verbose:
        print(f"  + {' '.join(cmd)}", flush=True)
    kwargs: dict = {"check": check, "cwd": cwd}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    if input is not None:
        kwargs["input"] = input
    return subprocess.run(cmd, **kwargs)


def run_output(cmd: list, *, verbose: bool = False) -> str:
    result = run(cmd, capture=True, verbose=verbose)
    return result.stdout.strip()


@contextmanager
def loop_device(path: Path, *, partscan: bool = False, read_only: bool = False):
    cmd = ["losetup", "--find", "--show"]
    if partscan:
        cmd.append("--partscan")
    if read_only:
        cmd.append("--read-only")
    cmd.append(str(path))
    result = run(cmd, capture=True)
    dev = result.stdout.strip()
    try:
        yield dev
    finally:
        run(["losetup", "--detach", dev], check=False)


@contextmanager
def loop_partition(path: Path, offset: int, size: int, *, read_only: bool = False):
    cmd = [
        "losetup", "--find", "--show",
        "--offset", str(offset),
        "--sizelimit", str(size),
    ]
    if read_only:
        cmd.append("--read-only")
    cmd.append(str(path))
    result = run(cmd, capture=True)
    dev = result.stdout.strip()
    try:
        yield dev
    finally:
        run(["losetup", "--detach", dev], check=False)


@contextmanager
def mounted(device: str, mountpoint: Path, *, options: str | None = None):
    mountpoint.mkdir(parents=True, exist_ok=True)
    cmd = ["mount"]
    if options:
        cmd.extend(["-o", options])
    cmd.extend([device, str(mountpoint)])
    run(cmd)
    try:
        yield mountpoint
    finally:
        run(["umount", "-l", str(mountpoint)], check=False)


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def info(msg: str) -> None:
    print(f"    {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"    WARNING: {msg}", file=sys.stderr, flush=True)
