#!/bin/bash
# shimpy interactive setup

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

if [[ $EUID -ne 0 ]]; then
    die "This script must be run as root.\n  Run: sudo bash setup.sh"
fi

# --- Board ---
ask "Step 1: Select your board"
echo ""
python3 "$SCRIPT_DIR/build.py" list-boards
echo ""
while true; do
    read -rp "Enter board name: " BOARD
    [[ -n "$BOARD" ]] && break
    warn "Board name cannot be empty."
done

# --- Shim ---
echo ""
ask "Step 2: Shim image"
echo ""
echo "Download your board's RMA shim from: https://cros.downloads"
echo "Search for your board name (e.g. $BOARD) and download the .bin file."
echo ""
while true; do
    read -rp "Path to shim .bin file: " SHIM_PATH
    SHIM_PATH="${SHIM_PATH/#\~/$HOME}"
    [[ -z "$SHIM_PATH" ]] && { warn "Path cannot be empty."; continue; }
    [[ ! -f "$SHIM_PATH" ]] && { warn "File not found: $SHIM_PATH"; continue; }
    break
done

# --- Recovery image (optional but strongly recommended) ---
echo ""
ask "Step 3: Recovery image (optional — strongly recommended for WiFi/audio)"
echo ""
echo "A ChromeOS recovery image has extra firmware (WiFi, audio, touchpad)"
echo "not always present in the shim. Download from: https://cros.downloads"
echo "Search '${BOARD} recovery' and download the recovery .bin."
echo ""
read -rp "Path to recovery .bin (leave blank to skip): " RECOVERY_PATH
RECOVERY_PATH="${RECOVERY_PATH/#\~/$HOME}"
if [[ -n "$RECOVERY_PATH" ]] && [[ ! -f "$RECOVERY_PATH" ]]; then
    warn "Recovery file not found: $RECOVERY_PATH — skipping"
    RECOVERY_PATH=""
fi

# --- Preset ---
echo ""
ask "Step 4: Choose a preset"
echo ""
echo "  1) Xubuntu  — Ubuntu + XFCE desktop, lightweight   (~6 GB)  [recommended]"
echo "  2) GNOME    — Ubuntu Desktop, full                  (~8 GB)"
echo "  3) KDE      — Kubuntu Plasma                        (~10 GB)"
echo "  4) Kali     — Kali Linux + XFCE                    (~8 GB)"
echo "  5) Arch     — Arch Linux, base only                 (~8 GB)"
echo "  6) Alpine   — Alpine Linux, minimal CLI             (~2 GB)"
echo "  7) Minimal  — Debian, CLI only                      (~4 GB)"
echo "  8) Custom   — choose distro and packages manually"
echo ""
read -rp "Choice [1-8, default 1]: " PRESET_CHOICE

case "$PRESET_CHOICE" in
    2) PRESET="gnome"   ;;
    3) PRESET="kde"     ;;
    4) PRESET="kali"    ;;
    5) PRESET="arch"    ;;
    6) PRESET="alpine"  ;;
    7) PRESET="minimal" ;;
    8) PRESET=""        ;;
    *) PRESET="xubuntu" ;;
esac

EXTRA_PKGS=""
if [[ -z "$PRESET" ]]; then
    read -rp "Packages to install (comma-separated): " EXTRA_PKGS
fi

# --- User account ---
echo ""
ask "Step 5: User account"
echo ""
read -rp "Username [shimpy]: " USERNAME
USERNAME="${USERNAME:-shimpy}"
read -rsp "Password [shimpy]: " PASSWORD
echo ""
PASSWORD="${PASSWORD:-shimpy}"

# --- Output path ---
echo ""
ask "Step 6: Output image path"
echo ""
LABEL="${PRESET:-custom}"
DEFAULT_OUTPUT="shimpy-${BOARD}-${LABEL}.bin"
read -rp "Output path [${DEFAULT_OUTPUT}]: " OUTPUT_PATH
OUTPUT_PATH="${OUTPUT_PATH:-$DEFAULT_OUTPUT}"

# --- Summary ---
echo ""
echo "------------------------------------"
echo -e "${BOLD}Build summary${NC}"
echo "------------------------------------"
echo "  Board:    $BOARD"
echo "  Shim:     $SHIM_PATH"
[[ -n "$RECOVERY_PATH" ]] && echo "  Recovery: $RECOVERY_PATH"
[[ -n "$PRESET"        ]] && echo "  Preset:   $PRESET"
[[ -n "$EXTRA_PKGS"    ]] && echo "  Packages: $EXTRA_PKGS"
echo "  Username: $USERNAME"
echo "  Output:   $OUTPUT_PATH"
echo "------------------------------------"
echo ""
read -rp "Start build? [Y/n]: " CONFIRM
case "$CONFIRM" in
    [nN]*) echo "Aborted."; exit 0 ;;
esac

# --- Build ---
echo ""
CMD=(python3 "$SCRIPT_DIR/build.py" build
    --board    "$BOARD"
    --shim     "$SHIM_PATH"
    --username "$USERNAME"
    --password "$PASSWORD"
    --output   "$OUTPUT_PATH"
    -v
)
[[ -n "$PRESET"        ]] && CMD+=(--preset   "$PRESET")
[[ -n "$EXTRA_PKGS"    ]] && CMD+=(--packages "$EXTRA_PKGS")
[[ -n "$RECOVERY_PATH" ]] && CMD+=(--recovery "$RECOVERY_PATH")

info "Running: ${CMD[*]}"
echo ""
"${CMD[@]}"

echo ""
info "Done! Flash with:"
echo ""
echo "  sudo dd if=${OUTPUT_PATH} of=/dev/sdX bs=4M status=progress"
echo ""
echo "Replace /dev/sdX with your USB drive or SD card."
echo "Login: username=${USERNAME}"
[[ "$PASSWORD" == "shimpy" ]] && warn "Default password used — change it after first boot: passwd"
