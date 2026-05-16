"""
Shim patching via KERN-A initramfs injection.

Instead of patching ROOT-A (which has ChromeOS-specific ext4 feature flags
that block mounting), we inject shimpy's chainloader directly into the
kernel's initramfs (KERN-A). This is the same approach used by shimboot.

Boot chain:
  depthcharge → KERN-A kernel → initramfs → shimpy /init → SHIMPY-ROOT → Linux
"""

import gzip
import re
import shutil
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
# KERN-A initramfs injection
# ---------------------------------------------------------------------------

def _find_gzip_cpio(data: bytes) -> tuple[int, int]:
    """Find the gzip-compressed CPIO initramfs inside a kernel blob.

    Returns (start_offset, end_offset) of the gzip stream.
    The ChromeOS kernel blob contains a bzImage followed by an initramfs
    as a gzip-compressed CPIO archive. We search for gzip magic bytes and
    verify the decompressed content starts with the CPIO magic.
    """
    GZIP_MAGIC = b'\x1f\x8b'
    CPIO_MAGIC = b'070701'

    pos = 0
    while True:
        offset = data.find(GZIP_MAGIC, pos)
        if offset == -1:
            raise BuildError(
                "No gzip-compressed CPIO initramfs found in KERN-A.\n"
                "The shim may use LZ4 compression (ARM boards) which is not yet supported."
            )
        try:
            decompressed = gzip.decompress(data[offset:])
            if decompressed[:6] == CPIO_MAGIC:
                # Find the end of this gzip stream
                buf = __import__('io').BytesIO(data[offset:])
                with gzip.GzipFile(fileobj=buf) as gz:
                    gz.read()
                end = offset + buf.tell()
                return offset, end
        except Exception:
            pass
        pos = offset + 1


def _repack_cpio(cpio_data: bytes, init_script: Path) -> bytes:
    """Prepend shimpy's /init to an existing CPIO archive and repack."""
    with tempfile.TemporaryDirectory(prefix="shimpy-cpio-") as work_str:
        work = Path(work_str)

        # Extract existing CPIO
        subprocess.run(
            ["cpio", "-id", "--quiet"],
            input=cpio_data,
            cwd=work,
            check=False,  # may have non-fatal warnings
            capture_output=True,
        )

        # Overwrite /init with shimpy's chainloader
        init_dst = work / "init"
        shutil.copy2(init_script, init_dst)
        init_dst.chmod(0o755)

        # Repack — sort for determinism, newc format
        result = subprocess.run(
            ["sh", "-c", "find . -mindepth 1 | sort | cpio -o -H newc --quiet"],
            capture_output=True,
            cwd=work,
            check=True,
        )
        return result.stdout


def _inject_into_kern_a(blob_path: Path, verbose: bool) -> None:
    """Replace /init in the kernel blob's initramfs with shimpy's chainloader."""
    data = blob_path.read_bytes()

    gz_start, gz_end = _find_gzip_cpio(data)
    original_gz = data[gz_start:gz_end]
    info(f"found initramfs at offset {gz_start} ({len(original_gz)} bytes compressed)")

    # Decompress, patch, recompress
    cpio_data = gzip.decompress(original_gz)
    new_cpio = _repack_cpio(cpio_data, INIT_SCRIPT)
    new_gz = gzip.compress(new_cpio, compresslevel=9)

    size_diff = len(new_gz) - len(original_gz)
    if size_diff != 0:
        info(f"initramfs size change: {size_diff:+d} bytes")

    blob_path.write_bytes(data[:gz_start] + new_gz + data[gz_end:])
    info("shimpy-init.sh injected as /init in KERN-A initramfs")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def patch_shim(image: Path, verbose: bool = False) -> None:
    step("Parsing shim partition table")
    parts = parse_partition_table(image)
    info(f"found {len(parts)} partitions: {list(parts.keys())}")

    kern_a = find_partition(parts, "KERN-A")
    info(f"KERN-A: offset={kern_a['start']} size={kern_a['size']} bytes")

    step("Extracting KERN-A")
    with tempfile.NamedTemporaryFile(suffix=".blob", delete=False, prefix="shimpy-kerna-") as f:
        kern_path = Path(f.name)

    try:
        extract_partition(image, kern_a["start"], kern_a["size"], kern_path)

        step("Injecting shimpy chainloader into KERN-A initramfs")
        _inject_into_kern_a(kern_path, verbose)

        # KERN-A partition must stay the same size — pad with zeros if needed
        original_size = kern_a["size"]
        current_size = kern_path.stat().st_size
        if current_size > original_size:
            raise BuildError(
                f"Patched KERN-A ({current_size} B) exceeds partition size "
                f"({original_size} B). Cannot write back."
            )
        if current_size < original_size:
            with open(kern_path, "ab") as f:
                f.write(b"\x00" * (original_size - current_size))

        step("Writing patched KERN-A back to shim image")
        write_partition(image, kern_a["start"], kern_path)
        info("KERN-A patched successfully")

    finally:
        kern_path.unlink(missing_ok=True)
