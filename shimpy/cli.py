import logging
import shutil
import sys
import tempfile
from pathlib import Path

import click

from . import __version__
from .boards import all_boards, get_board, resolve_shim_path
from .image import (
    add_gpt_partition,
    copy_shim,
    extend_image,
    format_and_populate,
    format_and_populate_squashfs,
    verify_image,
    write_checksum,
)
from .initramfs import patch_shim, parse_partition_table, find_partition, extract_partition
from .rootfs import (
    bootstrap_rootfs,
    bootstrap_cache_key,
    configure_rootfs,
    default_release,
    build_rootfs,
)
from .util import BuildError, check_root, check_tools, loop_device, run, step, info, warn

BUILD_TMP = Path("shimpy-build-tmp")


@click.group()
@click.version_option(__version__)
def cli() -> None:
    """shimpy — build a Linux-booting ChromeOS recovery image for dedede Chromebooks."""


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

PRESETS: dict[str, dict] = {
    "minimal":  {"distro": "debian",  "packages": "",                       "rootfs_size": 4096},
    "xubuntu":  {"distro": "ubuntu",  "packages": "xubuntu-core",           "rootfs_size": 6144},
    "gnome":    {"distro": "ubuntu",  "packages": "ubuntu-desktop-minimal",  "rootfs_size": 8192},
    "kde":      {"distro": "ubuntu",  "packages": "kubuntu-desktop",         "rootfs_size": 10240},
    "kali":     {"distro": "kali",    "packages": "kali-desktop-xfce",       "rootfs_size": 8192},
    "alpine":   {"distro": "alpine",  "packages": "",                        "rootfs_size": 2048},
    "arch":     {"distro": "arch",    "packages": "",                        "rootfs_size": 8192},
}


@cli.command()
@click.option("--board", required=True, help="Dedede sub-board name (e.g. drawcia, lantis)")
@click.option("--shim", "shim_path", default=None, type=click.Path(exists=True, path_type=Path),
              help="Path to local RMA shim .bin. Required unless the board has a shim_url.")
@click.option("--preset", default=None, type=click.Choice(list(PRESETS)),
              help="Desktop preset: minimal, xubuntu, gnome, kde, kali, alpine, arch.")
@click.option("--distro", default="debian", show_default=True,
              type=click.Choice(["debian", "ubuntu", "kali", "alpine", "arch"]),
              help="Base Linux distro (overridden by --preset)")
@click.option("--release", default=None,
              help="Distro release codename (e.g. bookworm, noble). Defaults per distro.")
@click.option("--packages", default="", help="Comma-separated extra packages to install")
@click.option("--rootfs-size", default=None, type=int,
              help="Linux rootfs partition size in MiB (default: 4096, or preset value)")
@click.option("--output", "output_path", default=None, type=click.Path(path_type=Path),
              help="Output image path (default: shimpy-<board>[-<preset>].bin)")
@click.option("--recovery", "recovery_path", default=None, type=click.Path(exists=True, path_type=Path),
              help="ChromeOS recovery image for additional firmware (improves WiFi/audio support)")
@click.option("--username", default="shimpy", show_default=True,
              help="Username for the default user account created in the rootfs")
@click.option("--password", default="shimpy", show_default=True,
              help="Password for the default user account (change after first boot)")
@click.option("--arch", default="amd64", show_default=True,
              type=click.Choice(["amd64", "arm64"]), help="Target CPU architecture")
@click.option("--no-cache", is_flag=True, default=False,
              help="Ignore cached rootfs and run a fresh bootstrap (slower).")
@click.option("--squashfs", is_flag=True, default=False,
              help="Compress rootfs as squashfs (smaller image) with a separate writable overlay partition.")
@click.option("--write-size", default=2048, show_default=True,
              help="Writable overlay partition size in MiB (squashfs mode only)")
@click.option("-v", "--verbose", is_flag=True, help="Show build step output")
@click.option("--dry-run", is_flag=True, help="Print steps without executing")
def build(
    board: str,
    shim_path: Path | None,
    preset: str | None,
    distro: str,
    release: str | None,
    packages: str,
    rootfs_size: int | None,
    output_path: Path | None,
    recovery_path: Path | None,
    username: str,
    password: str,
    arch: str,
    no_cache: bool,
    squashfs: bool,
    write_size: int,
    verbose: bool,
    dry_run: bool,
) -> None:
    """Build a flashable shimpy image for BOARD.

    Quick start with a preset:

      sudo python3 build.py build --board dedede --shim shim.bin --preset xubuntu

    Available presets: minimal, xubuntu, gnome, kde.
    """
    if preset:
        p = PRESETS[preset]
        distro = p["distro"]
        if not packages:
            packages = p["packages"]
        if rootfs_size is None:
            rootfs_size = p["rootfs_size"]
        if output_path is None:
            output_path = Path(f"shimpy-{board}-{preset}.bin")
    if rootfs_size is None:
        rootfs_size = 4096
    try:
        _build(board, distro, release, shim_path, output_path,
               rootfs_size, packages, arch, recovery_path, username, password,
               no_cache, squashfs, write_size, verbose, dry_run)  # type: ignore[arg-type]
    except BuildError as e:
        click.echo(f"\nError: {e}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\nInterrupted.", err=True)
        sys.exit(130)


def _build(
    board: str,
    distro: str,
    release: str | None,
    shim_path: Path | None,
    output_path: Path | None,
    rootfs_size: int,
    packages: str,
    arch: str,
    recovery_path: Path | None,
    username: str,
    password: str,
    no_cache: bool,
    squashfs: bool,
    write_size: int,
    verbose: bool,
    dry_run: bool,
) -> None:
    # --- Step 1: Validate inputs ---
    step("Validating inputs")
    board_info = get_board(board)
    info(f"board: {board} ({board_info['full_name']})")
    info(f"arch:  {board_info['arch']}")

    if arch != board_info["arch"]:
        warn(f"--arch={arch} overrides board default ({board_info['arch']})")

    extra_packages = [p.strip() for p in packages.split(",") if p.strip()]

    if output_path is None:
        output_path = Path(f"shimpy-{board}.bin")

    if dry_run:
        resolved_shim = shim_path or resolve_shim_path(board_info)
        click.echo("[dry-run] would build:")
        click.echo(f"  board:        {board}")
        click.echo(f"  shim:         {resolved_shim or '(none — pass --shim)'}")
        click.echo(f"  recovery:     {recovery_path or '(none — pass --recovery for better WiFi/audio firmware)'}")
        click.echo(f"  distro:       {distro} {release or '(default)'}")
        click.echo(f"  arch:         {arch}")
        click.echo(f"  rootfs_size:  {rootfs_size} MiB")
        click.echo(f"  squashfs:     {squashfs}" + (f" (write overlay: {write_size} MiB)" if squashfs else ""))
        click.echo(f"  output:       {output_path}")
        click.echo(f"  username:     {username}")
        if extra_packages:
            click.echo(f"  packages:     {', '.join(extra_packages)}")
        return

    check_root()
    check_tools()

    # --- Step 2: Acquire shim ---
    step("Acquiring shim")
    shim_path = _resolve_shim(board_info, shim_path)
    info(f"shim: {shim_path}")

    BUILD_TMP.mkdir(parents=True, exist_ok=True)

    # --- Step 3: Copy shim to working output ---
    copy_shim(shim_path, output_path)

    # --- Step 4 & 5: Patch ROOT-A with shimpy chainloader ---
    patch_shim(output_path, verbose=verbose)

    # --- Step 6: Build Linux rootfs (bootstrap from cache if available) ---
    import hashlib as _hashlib
    resolved_release = release or default_release(distro, arch)
    cache_key = bootstrap_cache_key(distro, resolved_release, arch, extra_packages)
    cache_dir  = BUILD_TMP / f"cache-{cache_key}"
    cache_mark = cache_dir / ".shimpy-bootstrap-complete"
    rootfs_dir = BUILD_TMP / f"rootfs-{board}"

    if not no_cache and cache_dir.exists() and cache_mark.exists():
        step(f"Restoring rootfs from cache [{cache_key}]")
        info(f"cache: {cache_dir}  (use --no-cache to rebuild from scratch)")
        rootfs_dir.mkdir(parents=True, exist_ok=True)
        run(["rsync", "-a", "--delete",
             str(cache_dir) + "/", str(rootfs_dir) + "/"],
            verbose=verbose)
    else:
        if no_cache and cache_dir.exists():
            info("--no-cache: ignoring existing cached bootstrap")
        rootfs_dir.mkdir(parents=True, exist_ok=True)
        bootstrap_rootfs(
            target=rootfs_dir,
            distro=distro,
            release=resolved_release,
            arch=arch,
            extra_packages=extra_packages,
            verbose=verbose,
        )
        if not no_cache:
            step(f"Saving bootstrap to cache [{cache_key}]")
            run(["rsync", "-a", str(rootfs_dir) + "/", str(cache_dir) + "/"],
                verbose=verbose)
            cache_mark.touch()
            info(f"cached: {cache_dir}")

    configure_rootfs(
        target=rootfs_dir,
        hostname=f"shimpy-{board}",
        username=username,
        password=password,
        distro=distro,
        squashfs=squashfs,
        verbose=verbose,
    )

    # --- Step 6b: Copy kernel modules and firmware from shim (and recovery) into rootfs ---
    _copy_shim_modules(output_path, rootfs_dir, recovery_path=recovery_path, verbose=verbose)

    # --- Step 7: Assemble image ---
    step("Assembling final image")
    if squashfs:
        format_and_populate_squashfs(output_path, rootfs_dir, write_size, verbose=verbose)
    else:
        start = extend_image(output_path, rootfs_size)
        add_gpt_partition(output_path, start, "SHIMPY-ROOT")
        format_and_populate(output_path, rootfs_dir, verbose=verbose)

    # --- Step 8: Verify ---
    ok = verify_image(output_path, verbose=verbose)

    checksum = write_checksum(output_path)

    click.echo(f"\nDone.")
    click.echo(f"  image:    {output_path}")
    click.echo(f"  checksum: {checksum}")
    click.echo(f"\nFlash with:")
    click.echo(f"  sudo dd if={output_path} of=/dev/sdX bs=4M status=progress")

    if not ok:
        click.echo("\nWARNING: verification found issues — see above.", err=True)
        sys.exit(1)


def _copy_shim_modules(
    image: Path,
    rootfs: Path,
    *,
    recovery_path: Path | None = None,
    verbose: bool = False,
) -> None:
    """Copy kernel modules and firmware from the shim's ROOT-A into the rootfs.

    Without this, WiFi, touchpad, audio and other hardware won't work because
    the ChromeOS kernel modules won't be available to the Linux userspace.
    """
    import tempfile

    step("Copying kernel modules and firmware from shim")

    parts = parse_partition_table(image)
    root_a = find_partition(parts, "ROOT-A")

    with tempfile.NamedTemporaryFile(suffix=".img", delete=False, prefix="shimpy-roota-mod-") as f:
        roota_path = Path(f.name)

    try:
        extract_partition(image, root_a["start"], root_a["size"], roota_path)

        with loop_device(roota_path, read_only=True) as loop:
            with tempfile.TemporaryDirectory(prefix="shimpy-roota-mnt-") as mnt_str:
                mnt = Path(mnt_str)
                try:
                    run(["mount", "-o", "ro", loop, str(mnt)])
                except Exception:
                    # ChromeOS ext4 features may block mounting — try with noload
                    run(["mount", "-t", "ext4", "-o", "ro,noload", loop, str(mnt)])

                try:
                    copied = []
                    for src_dir in ["lib/modules", "lib/firmware"]:
                        src = mnt / src_dir
                        dst = rootfs / src_dir
                        if src.exists():
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            run(["rsync", "-a", "--ignore-existing",
                                 str(src) + "/", str(dst) + "/"],
                                verbose=verbose)
                            copied.append(src_dir)
                    if copied:
                        info(f"copied from shim: {', '.join(copied)}")
                    else:
                        warn("no modules or firmware found in shim ROOT-A")
                finally:
                    run(["umount", "-l", str(mnt)], check=False)

        # Run depmod for each kernel version found
        modules_dir = rootfs / "lib" / "modules"
        if modules_dir.exists():
            for kver in sorted(modules_dir.iterdir()):
                if kver.is_dir():
                    run(["depmod", "-a", "-b", str(rootfs), kver.name],
                        verbose=verbose, check=False)
                    info(f"ran depmod for {kver.name}")

    finally:
        roota_path.unlink(missing_ok=True)

    if recovery_path is not None:
        _copy_recovery_firmware(recovery_path, rootfs, verbose=verbose)


def _copy_recovery_firmware(recovery_path: Path, rootfs: Path, *, verbose: bool = False) -> None:
    """Copy additional firmware from a ChromeOS recovery image into the rootfs.

    Recovery images carry firmware blobs (WiFi, audio, touchpad) that are often
    absent from the smaller RMA shim. Copying them on top of the shim firmware
    significantly improves hardware support on first boot.
    """
    import tempfile

    step("Copying firmware from recovery image")

    try:
        parts = parse_partition_table(recovery_path)
        root_a = find_partition(parts, "ROOT-A")
    except Exception as e:
        warn(f"Could not parse recovery image: {e} — skipping recovery firmware")
        return

    with tempfile.NamedTemporaryFile(suffix=".img", delete=False, prefix="shimpy-reco-") as f:
        reco_img = Path(f.name)

    try:
        extract_partition(recovery_path, root_a["start"], root_a["size"], reco_img)

        with loop_device(reco_img, read_only=True) as loop:
            with tempfile.TemporaryDirectory(prefix="shimpy-reco-mnt-") as mnt_str:
                mnt = Path(mnt_str)
                try:
                    run(["mount", "-o", "ro", loop, str(mnt)])
                except Exception:
                    run(["mount", "-t", "ext4", "-o", "ro,noload", loop, str(mnt)])

                try:
                    copied = []
                    for src_dir in ["lib/firmware", "lib/modprobe.d"]:
                        src = mnt / src_dir
                        dst = rootfs / src_dir
                        if src.exists():
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            run(["rsync", "-a", "--ignore-existing",
                                 str(src) + "/", str(dst) + "/"],
                                verbose=verbose)
                            copied.append(src_dir)
                    if copied:
                        info(f"copied from recovery: {', '.join(copied)}")
                    else:
                        warn("no firmware found in recovery ROOT-A")
                finally:
                    run(["umount", "-l", str(mnt)], check=False)
    except Exception as e:
        warn(f"Recovery firmware copy failed: {e}")
    finally:
        reco_img.unlink(missing_ok=True)


def _resolve_shim(board_info: dict, shim_path: Path | None) -> Path:
    if shim_path is not None:
        return shim_path

    configured = resolve_shim_path(board_info)
    if configured is not None:
        if not configured.exists():
            raise BuildError(
                f"boards.json shim_path for '{board_info['name']}' not found: {configured}\n"
                "Update shim_path in data/boards.json or pass --shim."
            )
        info(f"using configured shim: {configured}")
        return configured

    shim_url = board_info.get("shim_url")
    if shim_url:
        return _download_shim(shim_url, board_info["name"])

    note = board_info.get("shim_note", "")
    raise BuildError(
        f"No shim provided for board '{board_info['name']}' and no shim_url configured.\n"
        f"{note}\n\n"
        "Pass the shim with: --shim /path/to/shim.bin"
    )


def _download_shim(url: str, board: str) -> Path:
    import urllib.request
    dest = BUILD_TMP / f"shim-{board}.bin"
    if dest.exists():
        info(f"using cached shim: {dest}")
        return dest

    info(f"downloading shim from {url}")
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as e:
        raise BuildError(f"Failed to download shim: {e}\nUse --shim to provide it manually.")
    return dest


# ---------------------------------------------------------------------------
# list-boards
# ---------------------------------------------------------------------------

@cli.command("list-boards")
def list_boards() -> None:
    """List supported dedede boards."""
    boards = all_boards()
    col_w = max(len(n) for n in boards) + 2
    click.echo(f"{'Board':<{col_w}}  {'Model':<45}  Arch")
    click.echo("-" * (col_w + 52))
    for name, info_dict in sorted(boards.items()):
        click.echo(
            f"{name:<{col_w}}  {info_dict['full_name']:<45}  {info_dict['arch']}"
        )


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("image", type=click.Path(exists=True, path_type=Path))
@click.option("-v", "--verbose", is_flag=True)
def verify(image: Path, verbose: bool) -> None:
    """Verify a built shimpy image."""
    try:
        check_root()
        ok = verify_image(image, verbose=verbose)
        sys.exit(0 if ok else 1)
    except BuildError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
@click.option("--cache-only", is_flag=True, help="Only remove cached rootfs snapshots, keep built rootfs dirs")
def clean(yes: bool, cache_only: bool) -> None:
    """Remove the build cache directory (shimpy-build-tmp/).

    Use --cache-only to only purge rootfs bootstrap caches (cache-* dirs)
    without removing in-progress build state.
    """
    if not BUILD_TMP.exists():
        click.echo("Nothing to clean.")
        return

    if cache_only:
        caches = list(BUILD_TMP.glob("cache-*"))
        if not caches:
            click.echo("No cached rootfs snapshots found.")
            return
        total = sum(sum(f.stat().st_size for f in c.rglob("*") if f.is_file())
                    for c in caches) // (1024 * 1024)
        if not yes:
            click.confirm(f"Remove {len(caches)} cached bootstrap(s) (~{total} MiB)?", abort=True)
        for c in caches:
            shutil.rmtree(c)
        click.echo(f"Removed {len(caches)} cached bootstrap(s).")
    else:
        if not yes:
            click.confirm(f"Remove {BUILD_TMP}?", abort=True)
        shutil.rmtree(BUILD_TMP)
        click.echo(f"Removed {BUILD_TMP}")
