#!/bin/sh
# shimpy chainloader

mount -t devtmpfs dev /dev 2>/dev/null || true
mount -t proc proc /proc 2>/dev/null || true
mount -t sysfs sys /sys 2>/dev/null || true

echo "[shimpy] searching for rootfs..."

SQS_DEV=""
ROOT_DEV=""
for i in $(seq 1 20); do
    SQS_DEV=$(blkid -L SHIMPY-SQS 2>/dev/null)
    ROOT_DEV=$(blkid -L SHIMPY-ROOT 2>/dev/null)
    [ -n "$SQS_DEV" ] || [ -n "$ROOT_DEV" ] && break
    sleep 1
done

if [ -z "$SQS_DEV" ] && [ -z "$ROOT_DEV" ]; then
    echo "[shimpy] ERROR: no SHIMPY-SQS or SHIMPY-ROOT partition found after 20s"
    echo "[shimpy] block devices present:"
    ls /dev/sd* /dev/mmcblk* /dev/nvme* 2>/dev/null || echo "  (none visible)"
    exec /bin/sh
fi

mkdir -p /newroot

if [ -n "$SQS_DEV" ]; then
    echo "[shimpy] squashfs mode: $SQS_DEV"
    mkdir -p /shimpy_sqs /shimpy_overlay

    if ! mount -t squashfs -o ro "$SQS_DEV" /shimpy_sqs 2>/dev/null; then
        echo "[shimpy] ERROR: could not mount squashfs on $SQS_DEV"
        exec /bin/sh
    fi

    WRITE_DEV=$(blkid -L SHIMPY-WRITE 2>/dev/null)
    if [ -n "$WRITE_DEV" ]; then
        echo "[shimpy] write overlay: $WRITE_DEV"
        if ! mount -t ext4 "$WRITE_DEV" /shimpy_overlay 2>/dev/null; then
            mount -t tmpfs tmpfs /shimpy_overlay
            echo "[shimpy] WARNING: SHIMPY-WRITE mount failed, using volatile tmpfs"
        fi
    else
        mount -t tmpfs tmpfs /shimpy_overlay
        echo "[shimpy] WARNING: no SHIMPY-WRITE partition — writes will not survive reboot"
    fi

    mkdir -p /shimpy_overlay/upper /shimpy_overlay/work

    if ! mount -t overlay overlay \
        -o "lowerdir=/shimpy_sqs,upperdir=/shimpy_overlay/upper,workdir=/shimpy_overlay/work" \
        /newroot 2>/dev/null; then
        echo "[shimpy] ERROR: overlayfs failed — ChromeOS kernel may not support it"
        exec /bin/sh
    fi
    echo "[shimpy] overlayfs mounted"

else
    echo "[shimpy] ext4 mode: $ROOT_DEV"
    if ! mount -t ext4 -o ro "$ROOT_DEV" /newroot 2>/dev/null; then
        if ! mount -o ro "$ROOT_DEV" /newroot; then
            echo "[shimpy] ERROR: could not mount $ROOT_DEV"
            exec /bin/sh
        fi
    fi
    mount -o remount,rw /newroot
fi

INIT=/newroot/sbin/init
[ ! -x "$INIT" ] && INIT=/newroot/lib/systemd/systemd
[ ! -x "$INIT" ] && INIT=/newroot/bin/init

if [ ! -x "$INIT" ]; then
    echo "[shimpy] ERROR: no executable init found in rootfs"
    exec /bin/sh
fi

echo "[shimpy] switching root -> $INIT"
exec switch_root /newroot "$INIT"
