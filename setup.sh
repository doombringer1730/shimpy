#!/bin/bash
# shimpy interactive setup
# Asks questions and runs build.py with the right flags.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}==>${NC} ${BOLD}$*${NC}"; }
warn()  { echo -e "${YELLOW}WARN:${NC} $*"; }
die()   { echo -e "${RED}Error:${NC} $*" >&2; exit 1; }
ask()   { echo -e "${BOLD}$*${NC}"; }

echo ""
echo -e "${BOLD}shimpy — Chromebook Linux Boot Tool${NC}"
echo "------------------------------------"
echo ""

# --- Root check ---
if [[ $EUID -ne 0 ]]; then
    die "This script must be run as root.\n  Run: sudo bash setup.sh"
fi

# --- Board ---
ask "Step 1: Select your board"
echo ""
echo "Supported boards:"
echo ""
python3 "$SCRIPT_DIR/build.py" list-boards
echo ""
while true; do
    read -rp "Enter board name: " BOARD
    if [[ -z "$BOARD" ]]; then
        warn "Board name cannot be empty."
    else
        break
    fi
done

# --- Shim ---
echo ""
ask "Step 2: Shim image"
echo ""
echo "You need a ChromeOS RMA shim for your board."
echo "Download one from: https://cros.downloads"
echo "Search for your board name (e.g. $BOARD) and download the .bin file."
echo ""
while true; do
    read -rp "Path to shim .bin file: " SHIM_PATH
    SHIM_PATH="${SHIM_PATH/#\~/$HOME}"
    if [[ -z "$SHIM_PATH" ]]; then
        warn "Shim path cannot be empty."
    elif [[ ! -f "$SHIM_PATH" ]]; then
        warn "File not found: $SHIM_PATH"
    else
        break
    fi
done

# --- Distro ---
echo ""
ask "Step 3: Choose a Linux distribution"
echo ""
echo "  1) Debian (stable, minimal)             [default]"
echo "  2) Ubuntu (Noble 24.04)"
echo ""
read -rp "Choice [1-2]: " DISTRO_CHOICE
case "$DISTRO_CHOICE" in
    2) DISTRO="ubuntu" ;;
    *) DISTRO="debian" ;;
esac
info "Distro: $DISTRO"

# --- Desktop ---
echo ""
ask "Step 4: Choose a desktop environment"
echo ""
echo "  1) None — minimal CLI only              [default]"
echo "  2) Xubuntu (XFCE, lightweight)"
echo "  3) Ubuntu Desktop (GNOME, full)"
echo "  4) Kubuntu (KDE Plasma)"
echo "  5) Custom packages (enter manually)"
echo ""
read -rp "Choice [1-5]: " DESKTOP_CHOICE

PACKAGES=""
ROOTFS_SIZE=4096

case "$DESKTOP_CHOICE" in
    2)
        PACKAGES="xubuntu-core"
        ROOTFS_SIZE=6144
        info "Desktop: Xubuntu (XFCE minimal)"
        ;;
    3)
        PACKAGES="ubuntu-desktop-minimal"
        ROOTFS_SIZE=8192
        info "Desktop: Ubuntu (GNOME)"
        ;;
    4)
        PACKAGES="kubuntu-desktop"
        ROOTFS_SIZE=10240
        info "Desktop: Kubuntu (KDE Plasma)"
        ;;
    5)
        read -rp "Enter packages (comma-separated): " PACKAGES
        ROOTFS_SIZE=6144
        ;;
    *)
        info "Desktop: none (CLI only)"
        ;;
esac

# --- Extra packages ---
echo ""
ask "Step 5: Extra packages (optional)"
echo ""
read -rp "Additional packages to install (comma-separated, or leave blank): " EXTRA_PKGS

if [[ -n "$EXTRA_PKGS" ]]; then
    if [[ -n "$PACKAGES" ]]; then
        PACKAGES="$PACKAGES,$EXTRA_PKGS"
    else
        PACKAGES="$EXTRA_PKGS"
    fi
fi

# --- Output path ---
echo ""
ask "Step 6: Output image path"
echo ""
DEFAULT_OUTPUT="shimpy-${BOARD}.bin"
read -rp "Output path [${DEFAULT_OUTPUT}]: " OUTPUT_PATH
OUTPUT_PATH="${OUTPUT_PATH:-$DEFAULT_OUTPUT}"

# --- Summary ---
echo ""
echo "------------------------------------"
echo -e "${BOLD}Build summary${NC}"
echo "------------------------------------"
echo "  Board:       $BOARD"
echo "  Shim:        $SHIM_PATH"
echo "  Distro:      $DISTRO"
[[ -n "$PACKAGES" ]] && echo "  Packages:    $PACKAGES"
echo "  Rootfs size: ${ROOTFS_SIZE} MiB (recommended for selection)"
echo "  Output:      $OUTPUT_PATH"
echo "------------------------------------"
echo ""
read -rp "Start build? [Y/n]: " CONFIRM
case "$CONFIRM" in
    [nN]*) echo "Aborted."; exit 0 ;;
esac

# --- Build ---
echo ""
CMD=(python3 "$SCRIPT_DIR/build.py" build
    --board "$BOARD"
    --shim "$SHIM_PATH"
    --distro "$DISTRO"
    --rootfs-size "$ROOTFS_SIZE"
    --output "$OUTPUT_PATH"
    -v
)
[[ -n "$PACKAGES" ]] && CMD+=(--packages "$PACKAGES")

info "Running: ${CMD[*]}"
echo ""
"${CMD[@]}"

echo ""
info "Done! Flash your image:"
echo ""
echo "  sudo dd if=${OUTPUT_PATH} of=/dev/sdX bs=4M status=progress"
echo ""
echo "Replace /dev/sdX with your USB drive or SD card."
