# shimpy

Boot Linux on your dedede-family Chromebook using the ChromeOS recovery shim.

shimpy builds a flashable `.bin` image that chainloads a full Linux distribution
via the recovery shim — no permanent ChromeOS removal, no developer mode required.

---

## Supported boards

| Board | Device | CPU |
|---|---|---|
| `dedede` | Acer Chromebook Spin 512 (reference board) | Intel Celeron N4500 |
| `drawcia` | Acer Chromebook Spin 311 (R722T) | Intel Celeron N4500 |
| `drawlat` | Acer Chromebook Spin 512 (R853TA) | Intel Celeron N4500 |
| `galtic` | Acer Chromebook Spin 311 (CP311-3H) | Intel Celeron N4120 |
| `lantis` | Acer Chromebook 314 (CB314-2H) | Intel Celeron N4500 |
| `metaknight` | Acer Chromebook 315 (CB315-4H) | Intel Celeron N4500 |
| `blipper` | HP Chromebook 14a | Intel Celeron N4120 |
| `jelboz` | HP Chromebook 11 G9 EE | Intel Celeron N4500 |
| `boten` | Lenovo IdeaPad 3 Chromebook 14 | Intel Celeron N4020 |
| `pirika` | Lenovo 100e Chromebook Gen 3 | Intel Celeron N4500 |

Don't see your board? If it's in the dedede family, open an issue or add it to
`data/boards.json` — no code changes needed.

---

## Requirements

**Host machine:** Linux x86-64

**System tools:**
```sh
sudo apt-get install parted cgpt e2fsprogs util-linux debootstrap cpio gpgv
```

| Tool | Purpose |
|---|---|
| `parted` | Partition creation |
| `cgpt` | Fast GPT header read/repair (ChromeOS tool) |
| `debugfs` | Inject chainloader into ChromeOS ext4 (included in `e2fsprogs`) |
| `losetup`, `mkfs.ext4` | Loop device and filesystem setup (included in `util-linux`, `e2fsprogs`) |
| `debootstrap` | Debian rootfs bootstrap |
| `gpgv` | APT signature verification inside chroot |
| `cpio`, `dd`, `truncate` | Image assembly |

**Python:** 3.10+

```sh
pip install click
```

**Root access** is required for loop device setup, mounting, and debootstrap.

---

## Getting the shim

shimpy requires a ChromeOS RMA recovery shim for your board. Shims can be
downloaded from [cros.downloads](https://cros.downloads).

Search for your board name (e.g. `dedede`) and download the `.bin` file.

> Shims are board-specific. A shim for `drawcia` will not work on `lantis`.

---

## Usage

### Build an image

```sh
sudo python3 build.py build --board <board> --shim /path/to/shim.bin
```

**With Ubuntu (Xubuntu minimal):**
```sh
sudo python3 build.py build \
  --board dedede \
  --shim ~/Downloads/dedede.bin \
  --distro ubuntu \
  --packages xubuntu-core \
  --rootfs-size 6144
```

**With Debian (minimal, no desktop):**
```sh
sudo python3 build.py build \
  --board dedede \
  --shim ~/Downloads/dedede.bin \
  --distro debian
```

### All options

```
Options:
  --board TEXT         Board name (e.g. dedede, drawcia)        [required]
  --shim PATH          Path to RMA shim .bin                    [required]
  --distro TEXT        Base distro: debian, ubuntu              [default: debian]
  --release TEXT       Release codename (e.g. bookworm, noble)
  --output PATH        Output image path                        [default: shimpy-<board>.bin]
  --rootfs-size INT    Linux rootfs size in MiB                 [default: 4096]
  --packages TEXT      Extra packages (comma-separated)
  --arch TEXT          CPU arch: amd64, arm64                   [default: amd64]
  -v, --verbose        Show build step output
  --dry-run            Print steps without executing
```

### Other commands

```sh
# List supported boards
python3 build.py list-boards

# Verify a built image
sudo python3 build.py verify shimpy-dedede.bin

# Clean up build cache
python3 build.py clean
```

---

## Flashing

Write the image to a USB drive or SD card (replace `/dev/sdX` with your device):

```sh
sudo dd if=shimpy-dedede.bin of=/dev/sdX bs=4M status=progress
```

Then boot your Chromebook into recovery mode and insert the drive.

---

## How it works

1. The ChromeOS recovery shim is copied as the base image.
2. shimpy's chainloader script is injected into the shim's `ROOT-A` partition
   as `/sbin/init`.
3. A Linux rootfs (Debian or Ubuntu) is bootstrapped and written to a new
   `SHIMPY-ROOT` partition appended to the image.
4. On boot, depthcharge loads the shim, the shim hands off to shimpy's init,
   which mounts `SHIMPY-ROOT` and pivots into Linux.

ChromeOS on the main drive is untouched.

---

## What's included

### Distros available in `setup.sh`

| Choice | Distro | Desktop | Rootfs |
|---|---|---|---|
| CLI only | Debian bookworm | None | 4 GB |
| Xubuntu | Ubuntu Noble 24.04 | XFCE (minimal) | 6 GB |
| Ubuntu Desktop | Ubuntu Noble 24.04 | GNOME (minimal) | 8 GB |
| Kubuntu | Ubuntu Noble 24.04 | KDE Plasma | 10 GB |
| Custom | Debian or Ubuntu | Your choice | 6 GB |

You can also pass `--distro` and `--packages` directly to `build.py` if you
want something not listed above.

### What works

- **WiFi** — depends on your board. Most dedede devices use Realtek or Intel
  chipsets that are supported in the mainline kernel. If WiFi doesn't show up,
  install firmware packages: `sudo apt-get install firmware-realtek firmware-iwlwifi`
- **Bluetooth** — generally works on supported hardware
- **Audio** — may require manual ALSA/PulseAudio setup
- **USB** — works
- **Touchpad** — works via libinput
- **apt packages** — works normally
- **Flatpak** — works, recommended for GUI apps

### What doesn't work

| Feature | Why |
|---|---|
| **Snap** | Requires a squashfs loop mount inside the rootfs, which conflicts with how the shim mounts the partition. Avoid snap entirely — use apt or Flatpak instead. |
| **Hibernate / suspend-to-disk** | No swap partition in the default layout |
| **Accelerated graphics** | ChromeOS firmware doesn't expose GPU acceleration to the shim — display works but runs on llvmpipe (software rendering) |
| **Camera** | ChromeOS camera firmware is not available |
| **Steam / games** | See below |
| **Verified boot** | Not applicable in recovery mode |

### Steam

Steam can be installed but performance will be poor. The rootfs runs on
software rendering (llvmpipe) with no GPU acceleration, so most games will
either fail to launch or run at unplayable framerates.

**Installing Steam anyway:**
```sh
sudo dpkg --add-architecture i386
sudo apt-get update
sudo apt-get install steam-installer
```

**Common failures:**

- `OpenGL 3.3 or higher is required` — llvmpipe only supports up to OpenGL 3.3,
  so some games will refuse to launch entirely
- `GLSL 3.30 is not supported` — same root cause, no fix
- Steam itself launches slowly due to software rendering — this is expected
- Proton / Windows games — unlikely to run usably without GPU acceleration

If gaming is your goal, shimpy's rootfs is not the right environment for it.
A native dual-boot setup with full GPU support would be needed.

---

## Troubleshooting

### Partition parsing is very slow

`parted` on large shim images (~4GB+) can take 10+ minutes on an HDD because it
seeks to the end of the file to validate the backup GPT header. shimpy uses
`cgpt` for reads (which only reads the GPT headers and is near-instant), but
`parted` is still used for partition creation. If you're on an HDD, the copy
step will also be slow — there's no workaround for that.

### `couldn't mount RDWR because of unsupported optional features`

ChromeOS ext4 partitions use vendor-specific feature flags (`0xff000000`) that
the standard Linux kernel and tools refuse to mount read-write. shimpy works
around this by temporarily clearing those bits from the superblock before
writing with `debugfs`, then restoring them. No action needed — this is handled
automatically.

### `cannot create /dev/null: Permission denied` inside chroot

The ubuntu-base tarball ships without `/dev` populated. shimpy bind-mounts
`/dev`, `/proc`, and `/sys` from the host into the chroot before running apt.
If you see this error it means the chroot setup failed — check that you're
running with `sudo`.

### `gpgv, gpgv2 or gpgv1 required for verification`

ubuntu-base doesn't include `gpgv`, so apt can't verify package signatures on
first run. shimpy installs `gpgv` as the first step inside the chroot. If this
fails, ensure your host has network access and DNS is resolving correctly.

### `Failed to resolve 'archive.ubuntu.com'`

DNS isn't working inside the chroot. shimpy bind-mounts `/etc/resolv.conf` from
the host. If your host uses a local resolver (e.g. `127.0.0.53`), it may not be
reachable from the chroot network namespace. Try setting a public DNS server on
your host temporarily:

```sh
echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf
```

---

## License

GPL-3.0 — see [LICENSE](LICENSE).
