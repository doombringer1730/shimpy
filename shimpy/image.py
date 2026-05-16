"""
Final image assembly.

Takes a patched shim image and a built rootfs directory, appends a new
ext4 partition (SHIMPY-ROOT) containing the rootfs, and writes the
output .bin together with a SHA-256 checksum file.
"""

import hashlib
import shutil
import tempfile
from pathlib import Path

from .util import BuildError, loop_device, mounted, run, run_output, step, info, warn

SECTOR = 512
MiB = 1024 * 1024
# Align to 1 MiB boundaries (required by parted and avoids performance issues)
ALIGN = MiB


def _image_size(path: Path) -> int:
    return path.stat().st_size


def _align_up(value: int, align: int) -> int:
    return ((value + align - 1) // align) * align


def copy_shim(src: Path, dst: Path) -> None:
    step("Copying shim image")
    info(f"{src} -> {dst}")
    shutil.copy2(src, dst)


def extend_image(path: Path, rootfs_size_mib: int) -> int:
    """Extend the image file to accommodate the new rootfs partition.
    Returns the byte offset where the new partition starts."""
    current = _image_size(path)
    start = _align_up(current, ALIGN)
    # Pad to start alignment first if needed
    if start > current:
        run(["truncate", "--size", str(start), str(path)])

    new_size = start + rootfs_size_mib * MiB
    run(["truncate", "--size", str(new_size), str(path)])
    info(f"extended image: {current // MiB} MiB -> {new_size // MiB} MiB")

    # After extending, the backup GPT header is at the old end of file.
    # sgdisk -e moves it to the correct location before parted adds a partition.
    run(["sgdisk", "-e", str(path)])
    info("GPT backup header relocated to end of extended image")

    return start


def add_gpt_partition(image: Path, start_bytes: int, label: str) -> None:
    """Add a new GPT partition from start_bytes to end of image."""
    total = _image_size(image)
    start_mib = start_bytes // MiB
    end_mib = total // MiB - 1  # leave 1 MiB for GPT backup header

    run([
        "parted", "--script", str(image),
        "unit", "MiB",
        "mkpart", label, "ext4",
        str(start_mib), str(end_mib),
    ])
    info(f"added partition '{label}' at {start_mib} MiB – {end_mib} MiB")


def _find_partition_num(image: Path, label: str) -> int:
    """Return partition number for a given label using cgpt (fast, header-only read)."""
    import re
    raw = run_output(["cgpt", "show", str(image)])
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[2].isdigit():
            continue
        m = re.search(r'Label:\s+"([^"]+)"', line)
        if m and m.group(1) == label:
            return int(parts[2])
    raise BuildError(f"Partition '{label}' not found after creation.")


def format_and_populate(image: Path, rootfs_dir: Path, verbose: bool) -> None:
    step("Formatting SHIMPY-ROOT partition")

    part_num = _find_partition_num(image, "SHIMPY-ROOT")
    info(f"SHIMPY-ROOT is partition {part_num}")

    with loop_device(image, partscan=True) as loop:
        part_dev = f"{loop}p{part_num}"

        run(["mkfs.ext4", "-L", "SHIMPY-ROOT", "-F", part_dev], verbose=verbose)
        info(f"formatted {part_dev} as ext4 with label SHIMPY-ROOT")

        with tempfile.TemporaryDirectory(prefix="shimpy-mnt-") as mnt_str:
            mnt = Path(mnt_str)
            with mounted(part_dev, mnt):
                step("Copying rootfs into image")
                # rsync preserves permissions, symlinks, devices
                if shutil.which("rsync"):
                    run([
                        "rsync", "-aHAX",
                        "--info=progress2" if verbose else "--quiet",
                        str(rootfs_dir) + "/",
                        str(mnt) + "/",
                    ], verbose=verbose)
                else:
                    run(["cp", "-a", str(rootfs_dir) + "/.", str(mnt) + "/"],
                        verbose=verbose)
                info(f"rootfs copied to partition {part_num}")


def write_checksum(image: Path) -> Path:
    step("Writing checksum")
    h = hashlib.sha256()
    with open(image, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    checksum_path = image.with_suffix(".sha256")
    checksum_path.write_text(f"{digest}  {image.name}\n")
    info(f"SHA-256: {digest}")
    info(f"written to {checksum_path}")
    return checksum_path


def verify_image(image: Path, verbose: bool = False) -> bool:
    """Verify a built shimpy image. Returns True if all checks pass."""
    ok = True
    issues: list[str] = []

    step("Verifying image")

    # 1. Check partition table
    try:
        raw = run_output(["parted", "-m", "--", str(image), "unit", "B", "print"])
    except Exception as e:
        issues.append(f"Cannot read partition table: {e}")
        return False

    labels = set()
    for line in raw.splitlines():
        parts = line.rstrip(";").split(":")
        if len(parts) >= 6 and parts[0].isdigit():
            labels.add(parts[5])

    if "ROOT-A" not in labels:
        issues.append("ROOT-A partition not found")
        ok = False
    else:
        info("ROOT-A: present")

    if "SHIMPY-ROOT" not in labels:
        issues.append("SHIMPY-ROOT partition not found")
        ok = False
    else:
        info("SHIMPY-ROOT: present")

    # 2. Check SHIMPY-ROOT has /sbin/init
    if "SHIMPY-ROOT" in labels:
        try:
            part_num = _find_partition_num(image, "SHIMPY-ROOT")
            with loop_device(image, partscan=True) as loop:
                part_dev = f"{loop}p{part_num}"
                with tempfile.TemporaryDirectory(prefix="shimpy-verify-") as mnt_str:
                    mnt = Path(mnt_str)
                    with mounted(part_dev, mnt, options="ro"):
                        init = mnt / "sbin" / "init"
                        if not init.exists():
                            issues.append("SHIMPY-ROOT missing /sbin/init")
                            ok = False
                        else:
                            info("SHIMPY-ROOT /sbin/init: present")
        except Exception as e:
            issues.append(f"Could not inspect SHIMPY-ROOT: {e}")
            ok = False

    # 3. Check ROOT-A has shimpy-init marker
    if "ROOT-A" in labels:
        try:
            part_num = _find_partition_num(image, "ROOT-A")
            with loop_device(image, partscan=True) as loop:
                part_dev = f"{loop}p{part_num}"
                with tempfile.TemporaryDirectory(prefix="shimpy-verify-roota-") as mnt_str:
                    mnt = Path(mnt_str)
                    with mounted(part_dev, mnt, options="ro"):
                        init = mnt / "sbin" / "init"
                        if not init.exists():
                            issues.append("ROOT-A missing /sbin/init (shimpy chainloader)")
                            ok = False
                        elif b"shimpy" not in init.read_bytes():
                            issues.append("ROOT-A /sbin/init does not look like shimpy chainloader")
                            ok = False
                        else:
                            info("ROOT-A /sbin/init: shimpy chainloader present")
        except Exception as e:
            issues.append(f"Could not inspect ROOT-A: {e}")
            ok = False

    if issues:
        for issue in issues:
            warn(f"FAIL: {issue}")
    else:
        info("all checks passed")

    return ok
