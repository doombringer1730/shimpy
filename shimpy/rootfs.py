"""
Linux rootfs bootstrap.

Supports:
  - Debian via debootstrap
  - Ubuntu via ubuntu-base tarball
"""

import hashlib
import shutil
import tempfile
import textwrap
import urllib.request
from pathlib import Path

from .util import BuildError, run, step, info, warn

DEBIAN_MIRROR = "http://deb.debian.org/debian"
UBUNTU_BASE_INDEX = "https://cdimage.ubuntu.com/ubuntu-base/releases/{release}/release/"
KALI_MIRROR = "https://http.kali.org/kali"
ALPINE_MIRROR = "https://dl-cdn.alpinelinux.org/alpine"
ARCH_MIRROR = "https://geo.mirror.pkgbuild.com"

DEBIAN_DEFAULTS = {"amd64": "bookworm", "arm64": "bookworm"}
UBUNTU_DEFAULTS = {"amd64": "noble",    "arm64": "noble"}
KALI_DEFAULTS   = {"amd64": "kali-rolling", "arm64": "kali-rolling"}
ALPINE_DEFAULTS = {"amd64": "v3.21",    "arm64": "v3.21"}
ARCH_DEFAULTS   = {"amd64": "latest"}

OPENRC_DISTROS = frozenset({"alpine"})

BASE_PACKAGES = [
    "systemd",
    "systemd-sysv",
    "udev",
    "kmod",
    "util-linux",
    "e2fsprogs",
    "parted",
    "sudo",
    "openssh-server",
    "iproute2",
    "iputils-ping",
    "network-manager",
    "curl",
    "less",
    "vim-tiny",
    "zram-tools",
]

ALPINE_BASE_PACKAGES = [
    "alpine-base",
    "openrc",
    "e2fsprogs",
    "parted",
    "sudo",
    "openssh",
    "iproute2",
    "networkmanager",
    "curl",
    "less",
    "vim",
    "util-linux",
    "blkid",
]

ARCH_BASE_PACKAGES = [
    "base",
    "systemd",
    "kmod",
    "e2fsprogs",
    "parted",
    "sudo",
    "openssh",
    "iproute2",
    "iputils",
    "networkmanager",
    "curl",
    "less",
    "vim",
    "zram-generator",
]


_DISTRO_DEFAULTS = {
    "debian": DEBIAN_DEFAULTS,
    "ubuntu": UBUNTU_DEFAULTS,
    "kali":   KALI_DEFAULTS,
    "alpine": ALPINE_DEFAULTS,
    "arch":   ARCH_DEFAULTS,
}


def default_release(distro: str, arch: str) -> str:
    table = _DISTRO_DEFAULTS.get(distro)
    if table is None:
        raise BuildError(f"Unknown distro: {distro}")
    return table.get(arch, next(iter(table.values())))


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


def _bootstrap_kali(
    target: Path,
    release: str,
    arch: str,
    packages: list[str],
    verbose: bool,
) -> None:
    tool = _debootstrap_tool()
    all_packages = BASE_PACKAGES + ["kali-archive-keyring"] + packages
    include = ",".join(dict.fromkeys(all_packages))
    cmd = [
        tool,
        f"--arch={arch}",
        f"--include={include}",
        "--no-check-gpg",
        release,
        str(target),
        KALI_MIRROR,
    ]
    info(f"running {tool} {release} {arch} -> {target}")
    run(cmd, verbose=verbose)


def _alpine_minirootfs_url(release: str, arch: str) -> str:
    import re as _re
    index_url = f"{ALPINE_MIRROR}/{release}/releases/{arch}/"
    try:
        with urllib.request.urlopen(index_url) as resp:
            html = resp.read().decode()
    except Exception as e:
        raise BuildError(
            f"Could not fetch Alpine releases index: {e}\n"
            f"URL: {index_url}"
        )
    pattern = rf'alpine-minirootfs-[\d.]+-{_re.escape(arch)}\.tar\.gz'
    matches = _re.findall(pattern, html)
    if not matches:
        raise BuildError(
            f"No Alpine minirootfs tarball found for arch={arch} at {index_url}\n"
            "Check that the release and arch are correct."
        )
    return index_url + sorted(matches)[-1]


def _bootstrap_alpine(
    target: Path,
    release: str,
    arch: str,
    packages: list[str],
    verbose: bool,
) -> None:
    alpine_arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(arch, arch)
    url = _alpine_minirootfs_url(release, alpine_arch)
    tarball = Path(tempfile.mktemp(suffix=".tar.gz", prefix="shimpy-alpine-"))

    step(f"Downloading Alpine {release} {alpine_arch}")
    info(f"URL: {url}")
    try:
        urllib.request.urlretrieve(url, tarball)
    except Exception as e:
        raise BuildError(f"Failed to download Alpine minirootfs: {e}")

    target.mkdir(parents=True, exist_ok=True)
    run(["tar", "-xzf", str(tarball), "-C", str(target)], verbose=verbose)
    tarball.unlink(missing_ok=True)

    (target / "etc/apk/repositories").write_text(
        f"{ALPINE_MIRROR}/{release}/main\n{ALPINE_MIRROR}/{release}/community\n"
    )
    shutil.copy("/etc/resolv.conf", str(target / "etc/resolv.conf"))

    all_packages = list(dict.fromkeys(ALPINE_BASE_PACKAGES + packages))
    run(["chroot", str(target), "apk", "update"], verbose=verbose)
    run(["chroot", str(target), "apk", "add", "--no-cache"] + all_packages, verbose=verbose)


def _bootstrap_arch(
    target: Path,
    release: str,
    arch: str,
    packages: list[str],
    verbose: bool,
) -> None:
    if arch != "amd64":
        raise BuildError("Arch Linux bootstrap is currently only supported for amd64.")
    if not shutil.which("zstd"):
        raise BuildError(
            "zstd is required to extract the Arch Linux bootstrap tarball.\n"
            "Install with: sudo apt-get install zstd"
        )

    bootstrap_url = f"{ARCH_MIRROR}/iso/latest/archlinux-bootstrap-x86_64.tar.zst"
    bootstrap_tar = Path(tempfile.mktemp(suffix=".tar.zst", prefix="shimpy-arch-"))

    step("Downloading Arch Linux bootstrap tarball")
    info(f"URL: {bootstrap_url}")
    try:
        urllib.request.urlretrieve(bootstrap_url, bootstrap_tar)
    except Exception as e:
        raise BuildError(f"Failed to download Arch bootstrap: {e}")

    with tempfile.TemporaryDirectory(prefix="shimpy-arch-bs-") as bs_dir:
        bs_path = Path(bs_dir)
        step("Extracting Arch bootstrap")
        run(["tar", "--use-compress-program=zstd", "-xf", str(bootstrap_tar),
             "-C", str(bs_path)], verbose=verbose)
        bootstrap_tar.unlink(missing_ok=True)

        arch_root = bs_path / "root.x86_64"
        shutil.copy("/etc/resolv.conf", str(arch_root / "etc/resolv.conf"))
        (arch_root / "etc/pacman.d/mirrorlist").write_text(
            f"Server = {ARCH_MIRROR}/$repo/os/$arch\n"
        )

        target.mkdir(parents=True, exist_ok=True)
        mount_point = arch_root / "mnt"
        mount_point.mkdir(exist_ok=True)
        run(["mount", "--bind", str(target), str(mount_point)])

        bind_mounts = [
            (Path("/proc"), arch_root / "proc"),
            (Path("/sys"),  arch_root / "sys"),
            (Path("/dev"),  arch_root / "dev"),
        ]
        for src, dst in bind_mounts:
            dst.mkdir(exist_ok=True)
            run(["mount", "--bind", str(src), str(dst)])

        try:
            step("Initialising Arch pacman keyring (takes a moment)")
            run(["chroot", str(arch_root), "pacman-key", "--init"], verbose=verbose)
            run(["chroot", str(arch_root), "pacman-key", "--populate", "archlinux"],
                verbose=verbose)

            all_packages = list(dict.fromkeys(ARCH_BASE_PACKAGES + packages))
            step("Installing Arch base system")
            run(["chroot", str(arch_root), "pacman",
                 "--root", "/mnt",
                 "-Sy", "--needed", "--noconfirm"] + all_packages,
                verbose=verbose)
        finally:
            for _, dst in reversed(bind_mounts):
                run(["umount", "-l", str(dst)], check=False)
            run(["umount", "-l", str(mount_point)], check=False)


def _apt_install(target: Path, packages: list[str], verbose: bool) -> None:
    info(f"installing packages: {', '.join(packages)}")
    env_prefix = [
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    ]
    chroot = ["chroot", str(target)]

    # ubuntu-base needs /dev, /dev/pts, /proc, /sys, and resolv.conf from the host
    mounts = [
        (Path("/dev"),             target / "dev"),
        (Path("/dev/pts"),         target / "dev/pts"),
        (Path("/proc"),            target / "proc"),
        (Path("/sys"),             target / "sys"),
        (Path("/etc/resolv.conf"), target / "etc/resolv.conf"),
    ]
    for src, dst in mounts:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            dst.touch() if src.is_file() else dst.mkdir(parents=True, exist_ok=True)
        run(["mount", "--bind", str(src), str(dst)])

    try:
        # Remove broken dpkg statoverride entries that reference missing groups
        statoverride = target / "var/lib/dpkg/statoverride"
        if statoverride.exists():
            lines = statoverride.read_text().splitlines()
            cleaned = [l for l in lines if l.strip() and "messagebus" not in l]
            statoverride.write_text("\n".join(cleaned) + "\n" if cleaned else "")

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
        for _, dst in reversed(mounts):
            run(["umount", "-l", str(dst)], check=False)


def _create_user(target: Path, username: str, password: str, distro: str) -> None:
    step(f"Creating user '{username}'")

    if distro == "alpine":
        for g in ["wheel", "plugdev", "netdev"]:
            run(["chroot", str(target), "addgroup", "-S", g], check=False)
        run(["chroot", str(target), "adduser",
             "-D", "-s", "/bin/ash", "-G", "wheel", username], check=False)
        for g in ["audio", "video", "input", "plugdev", "netdev"]:
            run(["chroot", str(target), "addgroup", username, g], check=False)
        sudoers_d = target / "etc/sudoers.d"
        sudoers_d.mkdir(parents=True, exist_ok=True)
        f = sudoers_d / "wheel"
        f.write_text("%wheel ALL=(ALL) ALL\n")
        f.chmod(0o440)

    elif distro == "arch":
        for g in ["plugdev", "netdev"]:
            run(["chroot", str(target), "groupadd", "--system", g], check=False)
        sudoers_d = target / "etc/sudoers.d"
        sudoers_d.mkdir(parents=True, exist_ok=True)
        f = sudoers_d / "wheel"
        f.write_text("%wheel ALL=(ALL) ALL\n")
        f.chmod(0o440)
        run(["chroot", str(target), "useradd",
             "-m", "-s", "/bin/bash",
             "-G", "wheel,audio,video,input,plugdev,netdev",
             username], check=False)

    else:
        for g in ["plugdev", "netdev"]:
            run(["chroot", str(target), "groupadd", "--system", g], check=False)
        run(["chroot", str(target), "useradd",
             "-m", "-s", "/bin/bash",
             "-G", "sudo,audio,video,input,plugdev,netdev",
             username], check=False)

    run(["chroot", str(target), "chpasswd"],
        input=f"{username}:{password}\n".encode())
    run(["chroot", str(target), "chpasswd"],
        input=f"root:{password}\n".encode())
    info(f"user '{username}' created")
    warn(f"default password is '{password}' — change it after first boot with: passwd")


def _expand_script(label: str) -> str:
    return textwrap.dedent(f"""\
        #!/bin/sh
        set -e
        TARGET_DEV=$(blkid -L {label} 2>/dev/null)
        [ -z "$TARGET_DEV" ] && {{ echo "[shimpy-expand] {label} not found"; exit 0; }}
        DISK=$(lsblk -ndo pkname "$TARGET_DEV" 2>/dev/null)
        PART_NUM=$(lsblk -ndo PARTN "$TARGET_DEV" 2>/dev/null)
        if [ -n "$DISK" ] && [ -n "$PART_NUM" ]; then
            echo "[shimpy-expand] Growing /dev/$DISK partition $PART_NUM to fill disk"
            parted --script "/dev/$DISK" resizepart "$PART_NUM" 100% 2>/dev/null || true
            partprobe "/dev/$DISK" 2>/dev/null || true
            echo "[shimpy-expand] Resizing filesystem on $TARGET_DEV"
            resize2fs "$TARGET_DEV" 2>/dev/null || true
            echo "[shimpy-expand] Done"
        fi
        rm -f /var/lib/shimpy-expand-needed
    """)


def _install_expand_service(target: Path, distro: str, *, squashfs: bool = False) -> None:
    # In squashfs mode expand the writable overlay; in ext4 mode expand the rootfs
    label = "SHIMPY-WRITE" if squashfs else "SHIMPY-ROOT"
    script_path = target / "usr/local/sbin/shimpy-expand"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_expand_script(label))
    script_path.chmod(0o755)

    flag = target / "var/lib/shimpy-expand-needed"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    if distro in OPENRC_DISTROS:
        openrc_unit = textwrap.dedent("""\
            #!/sbin/openrc-run
            description="Expand shimpy rootfs to fill disk on first boot"
            depend() { need localmount; before default; }
            start() {
                [ -f /var/lib/shimpy-expand-needed ] || return 0
                /usr/local/sbin/shimpy-expand
            }
        """)
        init_path = target / "etc/init.d/shimpy-expand"
        init_path.write_text(openrc_unit)
        init_path.chmod(0o755)
        run(["chroot", str(target), "rc-update", "add", "shimpy-expand", "boot"], check=False)
    else:
        unit = textwrap.dedent("""\
            [Unit]
            Description=Expand shimpy rootfs to fill disk on first boot
            DefaultDependencies=no
            After=local-fs.target
            Before=basic.target
            ConditionPathExists=/var/lib/shimpy-expand-needed

            [Service]
            Type=oneshot
            ExecStart=/usr/local/sbin/shimpy-expand
            RemainAfterExit=yes

            [Install]
            WantedBy=basic.target
        """)
        unit_path = target / "etc/systemd/system/shimpy-expand.service"
        unit_path.write_text(unit)

        wants = target / "etc/systemd/system/basic.target.wants"
        wants.mkdir(parents=True, exist_ok=True)
        link = wants / "shimpy-expand.service"
        if not link.exists():
            link.symlink_to("/etc/systemd/system/shimpy-expand.service")

    info("installed shimpy-expand first-boot service")


def configure_rootfs(
    target: Path, hostname: str, username: str, password: str,
    distro: str, squashfs: bool = False, verbose: bool = False,
) -> None:
    step("Configuring rootfs")

    (target / "etc/hostname").write_text(hostname + "\n")
    info(f"hostname: {hostname}")

    hosts = target / "etc/hosts"
    if not hosts.exists() or hostname not in hosts.read_text():
        with open(hosts, "a") as f:
            f.write(f"127.0.1.1\t{hostname}\n")

    # lightdm autologin — same config location on all distros
    lightdm_dir = target / "etc/lightdm/lightdm.conf.d"
    lightdm_dir.mkdir(parents=True, exist_ok=True)
    (lightdm_dir / "50-shimpy-autologin.conf").write_text(
        f"[Seat:*]\nautologin-user={username}\nautologin-user-timeout=0\n"
    )
    info(f"configured lightdm autologin for '{username}'")

    if distro in OPENRC_DISTROS:
        run(["chroot", str(target), "rc-update", "add", "networkmanager", "default"], check=False)
        info("enabled NetworkManager (OpenRC)")
    else:
        sbin_init = target / "sbin/init"
        if not sbin_init.exists():
            sbin_init.parent.mkdir(parents=True, exist_ok=True)
            systemd = target / "lib/systemd/systemd"
            if systemd.exists():
                sbin_init.symlink_to("/lib/systemd/systemd")
                info("created /sbin/init -> /lib/systemd/systemd symlink")
            else:
                warn("/sbin/init not found and systemd not present — rootfs may not boot")

        systemd_dir = target / "etc/systemd/system"
        systemd_dir.mkdir(parents=True, exist_ok=True)
        mask_units = [
            "sys-kernel-debug.mount",
            "sys-kernel-tracing.mount",
            "dev-hugepages.mount",
            "dev-mqueue.mount",
            "systemd-journald-audit.socket",
            "auditd.service",
            "ModemManager.service",
        ]
        for unit in mask_units:
            p = systemd_dir / unit
            if not p.exists():
                p.symlink_to("/dev/null")
        info(f"masked {len(mask_units)} systemd units for ChromeOS kernel compatibility")

        nm_dir = target / "etc/NetworkManager/conf.d"
        nm_dir.mkdir(parents=True, exist_ok=True)
        (nm_dir / "10-shimpy.conf").write_text(
            "[main]\nplugins=ifupdown,keyfile\n\n[ifupdown]\nmanaged=true\n"
        )
        run(["systemctl", "--root", str(target), "enable", "NetworkManager"], check=False)
        info("enabled NetworkManager")

        if distro == "arch":
            zram_gen_dir = target / "etc/systemd/zram-generator.conf.d"
            zram_gen_dir.mkdir(parents=True, exist_ok=True)
            (zram_gen_dir / "shimpy.conf").write_text("[zram0]\nzram-size = ram / 2\n")
            info("configured zram swap via zram-generator (50% RAM)")
        else:
            zram_defaults = target / "etc/default/zramswap"
            zram_defaults.parent.mkdir(parents=True, exist_ok=True)
            zram_defaults.write_text("ALGO=lz4\nPERCENT=50\nPRIORITY=100\n")
            run(["systemctl", "--root", str(target), "enable", "zramswap"], check=False)
            info("configured zram swap (lz4, 50% RAM)")

    _create_user(target, username, password, distro)
    _install_expand_service(target, distro, squashfs=squashfs)

    info("rootfs configuration complete")


def bootstrap_cache_key(distro: str, release: str, arch: str, extra_packages: list[str]) -> str:
    """Stable hash identifying a bootstrap configuration for caching."""
    data = f"{distro}:{release}:{arch}:" + ":".join(sorted(extra_packages))
    return hashlib.sha256(data.encode()).hexdigest()[:12]


def bootstrap_rootfs(
    target: Path,
    distro: str,
    release: str,
    arch: str,
    extra_packages: list[str],
    verbose: bool = False,
) -> None:
    """Bootstrap the rootfs (slow, cacheable). Does not configure hostname/user/services."""
    target.mkdir(parents=True, exist_ok=True)

    if distro == "debian":
        step(f"Bootstrapping Debian {release} ({arch})")
        _bootstrap_debian(target, release, arch, extra_packages, verbose)
    elif distro == "ubuntu":
        step(f"Bootstrapping Ubuntu {release} ({arch})")
        _bootstrap_ubuntu(target, release, arch, extra_packages, verbose)
    elif distro == "kali":
        step(f"Bootstrapping Kali {release} ({arch})")
        _bootstrap_kali(target, release, arch, extra_packages, verbose)
    elif distro == "alpine":
        step(f"Bootstrapping Alpine {release} ({arch})")
        _bootstrap_alpine(target, release, arch, extra_packages, verbose)
    elif distro == "arch":
        step(f"Bootstrapping Arch Linux ({arch})")
        _bootstrap_arch(target, release, arch, extra_packages, verbose)
    else:
        raise BuildError(
            f"Unsupported distro: '{distro}'. "
            "Supported: debian, ubuntu, kali, alpine, arch"
        )


def build_rootfs(
    target: Path,
    distro: str,
    release: str | None,
    arch: str,
    extra_packages: list[str],
    hostname: str,
    username: str,
    password: str,
    squashfs: bool = False,
    verbose: bool = False,
) -> None:
    resolved = release or default_release(distro, arch)
    info(f"distro={distro} release={resolved} arch={arch}")
    bootstrap_rootfs(target, distro, resolved, arch, extra_packages, verbose)
    configure_rootfs(target, hostname, username, password, distro, squashfs, verbose)
