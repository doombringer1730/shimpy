"""
Shim patching.

On x86 dedede boards the ChromeOS kernel boots directly to ROOT-A with no
external initramfs in KERN-A. shimpy injects its chainloader as /sbin/init
into ROOT-A using debugfs (bypassing ChromeOS-specific ext4 feature flags
by temporarily clearing them from the superblock).

On ARM boards (future), KERN-A contains a gzip-compressed CPIO initramfs
that can be patched directly. Not yet implemented.

Boot chain (x86):
  depthcharge → KERN-A kernel → ROOT-A /sbin/init (shimpy) → SHIMPY-ROOT → Linux
"""

import gzip
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from .util import BuildError, run, run_output, step, info, warn

DATA_DIR = Path(__file__).parent.parent / "data"
INIT_SCRIPT = DATA_DIR / "shimpy-init.sh"


# ---------------------------------------------------------------------------
# Partition table
# ---------------------------------------------------------------------------

def parse_partition_table(image: Path) -> dict[str, dict]:
    """Return dict keyed by partition name with offset/size in bytes.

    Uses cgpt which reads only the GPT headers (fast on large images).
    """
    raw = run_output(["cgpt", "show", str(image)])
    partitions: dict[str, dict] = {}

    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[2].isdigit():
            continue
        label_match = re.search(r'Label:\s+"([^"]+)"', line)
        if not label_match:
            continue
        num = int(parts[2])
        start_lba = int(parts[0])
        size_lba = int(parts[1])
        name = label_match.group(1)
        partitions[name] = {
            "num": num,
            "start": start_lba * 512,
            "end": (start_lba + size_lba) * 512,
            "size": size_lba * 512,
        }

    return partitions


def find_partition(partitions: dict, name: str) -> dict:
    if name not in partitions:
        available = list(partitions.keys())
        raise BuildError(
            f"Partition '{name}' not found in shim image.\n"
            f"Available partitions: {available}\n"
            "Ensure the shim is a valid ChromeOS RMA shim for a dedede board."
        )
    return partitions[name]


# ---------------------------------------------------------------------------
# Raw partition extraction / write-back
# ---------------------------------------------------------------------------

def extract_partition(image: Path, offset: int, size: int, dest: Path) -> None:
    bs = 4096
    skip = offset // bs
    count = (size + bs - 1) // bs
    run(["dd",
         f"if={image}",
         f"of={dest}",
         f"bs={bs}",
         f"skip={skip}",
         f"count={count}",
         "status=none"])


def write_partition(image: Path, offset: int, src: Path) -> None:
    bs = 4096
    seek = offset // bs
    run(["dd",
         f"if={src}",
         f"of={image}",
         f"bs={bs}",
         f"seek={seek}",
         "conv=notrunc",
         "status=none"])


# ---------------------------------------------------------------------------
# ROOT-A injection (x86)
# ---------------------------------------------------------------------------

def _inject_into_ext4(partition_path: Path, verbose: bool) -> None:
    # ChromeOS ext4 partitions use vendor-specific ro_compat bits (0xff000000)
    # that debugfs refuses to open. Temporarily clear them from the superblock,
    # inject via debugfs, then restore the original value.
    if not shutil.which("debugfs"):
        raise BuildError(
            "debugfs is required to patch a ChromeOS ext4 ROOT-A.\n"
            "Install with: sudo apt-get install e2fsprogs"
        )

    SB_OFFSET = 1024
    RO_COMPAT_OFFSET = 100
    CHROMEOS_BITS = 0xff000000

    with open(partition_path, 'r+b') as f:
        f.seek(SB_OFFSET + RO_COMPAT_OFFSET)
        (orig_ro_compat,) = struct.unpack('<I', f.read(4))
        f.seek(SB_OFFSET + RO_COMPAT_OFFSET)
        f.write(struct.pack('<I', orig_ro_compat & ~CHROMEOS_BITS))
    info(f"cleared ChromeOS ro_compat bits (0x{orig_ro_compat:08x} -> 0x{orig_ro_compat & ~CHROMEOS_BITS:08x})")

    try:
        run(["debugfs", "-w", str(partition_path), "-R",
             "rename /sbin/init /sbin/init.orig"], check=False)
        run(["debugfs", "-w", str(partition_path), "-R",
             f"write {INIT_SCRIPT} /sbin/init"])
        run(["debugfs", "-w", str(partition_path), "-R",
             "set_inode_field /sbin/init i_mode 0100755"])
        info("injected shimpy-init.sh -> /sbin/init (via debugfs)")
    finally:
        with open(partition_path, 'r+b') as f:
            f.seek(SB_OFFSET + RO_COMPAT_OFFSET)
            f.write(struct.pack('<I', orig_ro_compat))


def _inject_into_squashfs(partition_path: Path, verbose: bool) -> None:
    if not shutil.which("unsquashfs") or not shutil.which("mksquashfs"):
        raise BuildError(
            "squashfs-tools (unsquashfs, mksquashfs) are required to patch a squashfs ROOT-A.\n"
            "Install with: sudo apt-get install squashfs-tools"
        )

    info_out = run_output(["unsquashfs", "-s", str(partition_path)])
    comp = "gzip"
    block_size = 131072
    for line in info_out.splitlines():
        if line.startswith("Compression"):
            comp = line.split()[-1].lower()
        m = re.search(r"Block size\s+(\d+)", line)
        if m:
            block_size = int(m.group(1))

    with tempfile.TemporaryDirectory(prefix="shimpy-squash-") as work_str:
        work = Path(work_str)
        extracted = work / "root"
        run(["unsquashfs", "-d", str(extracted), str(partition_path)], verbose=verbose)

        init_path = extracted / "sbin" / "init"
        init_path.parent.mkdir(parents=True, exist_ok=True)
        if init_path.exists() and not init_path.is_symlink():
            shutil.copy2(init_path, extracted / "sbin" / "init.orig")
        shutil.copy2(INIT_SCRIPT, init_path)
        init_path.chmod(0o755)

        repacked = work / "roota-repacked.squashfs"
        run(["mksquashfs", str(extracted), str(repacked),
             "-comp", comp, "-b", str(block_size), "-noappend"], verbose=verbose)
        shutil.copy2(repacked, partition_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_fs_type(path: Path) -> str:
    """Detect filesystem type of a partition image. Returns 'ext4' or 'squashfs'."""
    magic = run_output(["file", "-b", str(path)])
    if "squashfs" in magic.lower():
        return "squashfs"
    if "ext" in magic.lower():
        return "ext4"
    blkid_out = run_output(["blkid", "-o", "value", "-s", "TYPE", str(path)], check=False)
    if "squashfs" in blkid_out.lower():
        return "squashfs"
    if "ext" in blkid_out.lower():
        return "ext4"
    raise BuildError(
        f"Could not detect filesystem type of {path}\n"
        f"file output: {magic}\n"
        "Expected ext4 or squashfs."
    )


def patch_shim(image: Path, verbose: bool = False) -> None:
    step("Parsing shim partition table")
    parts = parse_partition_table(image)
    info(f"found {len(parts)} partitions: {list(parts.keys())}")

    root_a = find_partition(parts, "ROOT-A")
    info(f"ROOT-A: offset={root_a['start']} size={root_a['size']} bytes")

    step("Extracting ROOT-A partition")
    with tempfile.NamedTemporaryFile(suffix=".img", delete=False, prefix="shimpy-roota-") as f:
        roota_path = Path(f.name)

    try:
        extract_partition(image, root_a["start"], root_a["size"], roota_path)

        fs = detect_fs_type(roota_path)
        info(f"ROOT-A filesystem: {fs}")

        step("Injecting shimpy chainloader into ROOT-A")
        if fs == "ext4":
            _inject_into_ext4(roota_path, verbose)
        else:
            _inject_into_squashfs(roota_path, verbose)

        step("Writing patched ROOT-A back to shim image")
        if roota_path.stat().st_size > root_a["size"]:
            raise BuildError("Patched ROOT-A is larger than original slot.")
        write_partition(image, root_a["start"], roota_path)
        info("ROOT-A patched successfully")

    finally:
        roota_path.unlink(missing_ok=True)
