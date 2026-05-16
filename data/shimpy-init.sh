#!/bin/sh
# shimpy chainloader
# Replaces ROOT-A init to pivot into the Linux rootfs on SHIMPY-ROOT partition.

mount -t devtmpfs dev /dev 2>/dev/null || true
mount -t proc proc /proc 2>/dev/null || true
mount -t sysfs sys /sys 2>/dev/null || true

echo "[shimpy] searching for SHIMPY-ROOT..."

ROOT_DEV=""
for i in $(seq 1 20); do
    ROOT_DEV=$(blkid -L SHIMPY-ROOT 2>/dev/null)
    [ -n "$ROOT_DEV" ] && break
    sleep 1
done

if [ -z "$ROOT_DEV" ]; then
    echo "[shimpy] ERROR: SHIMPY-ROOT partition not found after 20s"
    echo "[shimpy] block devices present:"
    ls /dev/sd* /dev/mmcblk* /dev/nvme* 2>/dev/null || echo "  (none visible)"
    echo "[shimpy] dropping to emergency shell"
    exec /bin/sh
fi

echo "[shimpy] found $ROOT_DEV"
mkdir -p /newroot

if ! mount -t ext4 -o ro "$ROOT_DEV" /newroot 2>/dev/null; then
    if ! mount -o ro "$ROOT_DEV" /newroot; then
        echo "[shimpy] ERROR: could not mount $ROOT_DEV"
        exec /bin/sh
    fi
fi

# Remount rw for first-boot setup
mount -o remount,rw /newroot

INIT=/newroot/sbin/init
[ ! -x "$INIT" ] && INIT=/newroot/lib/systemd/systemd
[ ! -x "$INIT" ] && INIT=/newroot/bin/init

if [ ! -x "$INIT" ]; then
    echo "[shimpy] ERROR: no executable init found in rootfs"
    echo "[shimpy] tried: /sbin/init, /lib/systemd/systemd, /bin/init"
    exec /bin/sh
fi

echo "[shimpy] switching root -> $INIT"
exec switch_root /newroot "$INIT"
