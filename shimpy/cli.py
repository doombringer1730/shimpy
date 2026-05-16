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
    verify_image,
    write_checksum,
)
from .initramfs import patch_shim
from .rootfs import build_rootfs
from .util import BuildError, check_root, check_tools, step, info, warn

BUILD_TMP = Path("shimpy-build-tmp")


@click.group()
@click.version_option(__version__)
def cli() -> None:
    """shimpy — build a Linux-booting ChromeOS recovery image for dedede Chromebooks."""


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--board", required=True, help="Dedede sub-board name (e.g. drawcia, lantis)")
@click.option("--distro", default="debian", show_default=True,
              type=click.Choice(["debian", "ubuntu"]), help="Base Linux distro")
@click.option("--release", default=None,
              help="Distro release codename (e.g. bookworm, noble). Defaults per distro.")
@click.option("--shim", "shim_path", default=None, type=click.Path(exists=True, path_type=Path),
              help="Path to local RMA shim .bin. Required unless the board has a shim_url.")
@click.option("--output", "output_path", default=None, type=click.Path(path_type=Path),
              help="Output image path (default: shimpy-<board>.bin)")
@click.option("--rootfs-size", default=4096, show_default=True,
              help="Linux rootfs partition size in MiB")
@click.option("--packages", default="", help="Comma-separated extra packages to install")
@click.option("--arch", default="amd64", show_default=True,
              type=click.Choice(["amd64", "arm64"]), help="Target CPU architecture")
@click.option("-v", "--verbose", is_flag=True, help="Show build step output")
@click.option("--dry-run", is_flag=True, help="Print steps without executing")
def build(
    board: str,
    distro: str,
    release: str | None,
    shim_path: Path | None,
    output_path: Path | None,
    rootfs_size: int,
    packages: str,
    arch: str,
    verbose: bool,
    dry_run: bool,
) -> None:
    """Build a flashable shimpy image for BOARD."""
    try:
        _build(board, distro, release, shim_path, output_path,
               rootfs_size, packages, arch, verbose, dry_run)
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
        click.echo(f"  distro:       {distro} {release or '(default)'}")
        click.echo(f"  arch:         {arch}")
        click.echo(f"  rootfs_size:  {rootfs_size} MiB")
        click.echo(f"  output:       {output_path}")
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

    # --- Step 6: Build Linux rootfs ---
    rootfs_dir = BUILD_TMP / f"rootfs-{board}"
    build_rootfs(
        target=rootfs_dir,
        distro=distro,
        release=release,
        arch=arch,
        extra_packages=extra_packages,
        hostname=f"shimpy-{board}",
        verbose=verbose,
    )

    # --- Step 7: Assemble image ---
    step("Assembling final image")
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
def clean(yes: bool) -> None:
    """Remove the build cache directory (shimpy-build-tmp/)."""
    if not BUILD_TMP.exists():
        click.echo("Nothing to clean.")
        return
    if not yes:
        click.confirm(f"Remove {BUILD_TMP}?", abort=True)
    shutil.rmtree(BUILD_TMP)
    click.echo(f"Removed {BUILD_TMP}")
