"""
Linux rootfs bootstrap.

Supports:
  - Debian via debootstrap
  - Ubuntu via ubuntu-base tarball
"""

import shutil
import tempfile
import urllib.request
from pathlib import Path

from .util import BuildError, run, step, info, warn

DEBIAN_MIRROR = "http://deb.debian.org/debian"
UBUNTU_BASE_INDEX = "https://cdimage.ubuntu.com/ubuntu-base/releases/{release}/release/"

DEBIAN_DEFAULTS = {
    "amd64": "bookworm",
    "arm64": "bookworm",
}

UBUNTU_DEFAULTS = {
    "amd64": "noble",
    "arm64": "noble",
}

BASE_PACKAGES = [
    "systemd",
    "systemd-sysv",
    "udev",
    "kmod",
    "util-linux",
    "e2fsprogs",
    "openssh-server",
    "iproute2",
    "iputils-ping",
    "curl",
    "less",
    "vim-tiny",
]


def _default_release(distro: str, arch: str) -> str:
    if distro == "debian":
        return DEBIAN_DEFAULTS.get(arch, "bookworm")
    if distro == "ubuntu":
        return UBUNTU_DEFAULTS.get(arch, "noble")
    raise BuildError(f"Unknown distro: {distro}")


def _debootstrap_tool() -> str:
    for tool in ("mmdebstrap", "debootstrap"):
        if shutil.which(tool):
            return tool
    raise BuildError(
        "Neither 'debootstrap' nor 'mmdebstrap' found.\n"
        "Install with: sudo apt-get install debootstrap"
    )


def _bootstrap_debian(
    target: Path,
    release: str,
    arch: str,
    packages: list[str],
    verbose: bool,
) -> None:
    tool = _debootstrap_tool()
    all_packages = BASE_PACKAGES + packages
    include = ",".join(dict.fromkeys(all_packages))  # dedup, preserve order

    cmd = [
        tool,
        f"--arch={arch}",
        f"--include={include}",
        release,
        str(target),
        DEBIAN_MIRROR,
    ]
    info(f"running {tool} {release} {arch} -> {target}")
    run(cmd, verbose=verbose)


def _ubuntu_base_url(release: str, arch: str) -> str:
    """Scrape the ubuntu-base release index to find the actual tarball filename."""
    import re as _re
    index_url = UBUNTU_BASE_INDEX.format(release=release)
    try:
        with urllib.request.urlopen(index_url) as resp:
            html = resp.read().decode()
    except Exception as e:
        raise BuildError(
            f"Could not fetch ubuntu-base index: {e}\n"
            f"URL: {index_url}\n"
            "Check your network connection and that the release name is valid."
        )
    pattern = rf'ubuntu-base-[\d.]+-base-{_re.escape(arch)}\.tar\.gz'
    matches = _re.findall(pattern, html)
    if not matches:
        raise BuildError(
            f"No ubuntu-base tarball found for arch={arch} at {index_url}\n"
            "Check that the release and arch are correct."
        )
    # Take the last match (highest version)
    filename = sorted(matches)[-1]
    return index_url + filename


def _bootstrap_ubuntu(
    target: Path,
    release: str,
    arch: str,
    packages: list[str],
    verbose: bool,
) -> None:
    url = _ubuntu_base_url(release, arch)
    tarball = Path(tempfile.mktemp(suffix=".tar.gz", prefix="shimpy-ubuntu-base-"))

    step(f"Downloading ubuntu-base {release} {arch}")
    info(f"URL: {url}")
    try:
        urllib.request.urlretrieve(url, tarball)
    except Exception as e:
        raise BuildError(
            f"Failed to download ubuntu-base: {e}\n"
            f"URL: {url}\n"
            "Check your network connection and that the release name is correct."
        )

    info(f"extracting ubuntu-base to {target}")
    target.mkdir(parents=True, exist_ok=True)
    run(["tar", "-xzf", str(tarball), "-C", str(target)], verbose=verbose)
    tarball.unlink(missing_ok=True)

    if packages or BASE_PACKAGES:
        all_packages = list(dict.fromkeys(BASE_PACKAGES + packages))
        _apt_install(target, all_packages, verbose)


def _apt_install(target: Path, packages: list[str], verbose: bool) -> None:
    info(f"installing packages: {', '.join(packages)}")
    env_prefix = [
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    ]
    chroot = ["chroot", str(target)]

    # ubuntu-base needs /dev, /proc, /sys, and resolv.conf from the host
    mounts = [
        (Path("/dev"),           target / "dev",            "bind"),
        (Path("/proc"),          target / "proc",           "bind"),
        (Path("/sys"),           target / "sys",            "bind"),
        (Path("/etc/resolv.conf"), target / "etc/resolv.conf", "bind"),
    ]
    for src, dst, _ in mounts:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            dst.touch() if src.is_file() else dst.mkdir(parents=True, exist_ok=True)
        run(["mount", "--bind", str(src), str(dst)])

    try:
        # Install gpgv first so apt can verify signatures
        run(env_prefix + chroot + [
            "apt-get", "install", "-y", "--no-install-recommends", "gpgv",
        ], verbose=verbose)
        run(env_prefix + chroot + ["apt-get", "update", "-qq"], verbose=verbose)
        run(
            env_prefix + chroot + [
                "apt-get", "install", "-y", "--no-install-recommends",
            ] + packages,
            verbose=verbose,
        )
    finally:
        for _, dst, _ in reversed(mounts):
            run(["umount", "-l", str(dst)], check=False)


def _configure_rootfs(target: Path, hostname: str, verbose: bool) -> None:
    step("Configuring rootfs")

    (target / "etc" / "hostname").write_text(hostname + "\n")
    info(f"hostname: {hostname}")

    hosts = target / "etc" / "hosts"
    if not hosts.exists() or hostname not in hosts.read_text():
        with open(hosts, "a") as f:
            f.write(f"127.0.1.1\t{hostname}\n")

    # Ensure /sbin/init exists (systemd link)
    sbin_init = target / "sbin" / "init"
    if not sbin_init.exists():
        sbin_init.parent.mkdir(parents=True, exist_ok=True)
        systemd = target / "lib" / "systemd" / "systemd"
        if systemd.exists():
            sbin_init.symlink_to("/lib/systemd/systemd")
            info("created /sbin/init -> /lib/systemd/systemd symlink")
        else:
            warn("/sbin/init not found and systemd not present — rootfs may not boot")

    info("rootfs configuration complete")


def build_rootfs(
    target: Path,
    distro: str,
    release: str | None,
    arch: str,
    extra_packages: list[str],
    hostname: str,
    verbose: bool,
) -> None:
    resolved_release = release or _default_release(distro, arch)
    info(f"distro={distro} release={resolved_release} arch={arch}")

    target.mkdir(parents=True, exist_ok=True)

    if distro == "debian":
        step(f"Bootstrapping Debian {resolved_release} ({arch})")
        _bootstrap_debian(target, resolved_release, arch, extra_packages, verbose)
    elif distro == "ubuntu":
        step(f"Bootstrapping Ubuntu {resolved_release} ({arch})")
        _bootstrap_ubuntu(target, resolved_release, arch, extra_packages, verbose)
    else:
        raise BuildError(f"Unsupported distro: '{distro}'. Supported: debian, ubuntu")

    _configure_rootfs(target, hostname, verbose)
