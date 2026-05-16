#!/bin/bash
# Build a shimpy image using Docker.
# Works on Linux, macOS, and Windows (via Docker Desktop).
#
# Usage:
#   bash docker-build.sh --board dedede --shim /path/to/shim.bin [options]
#
# All shimpy build options are passed through directly.

set -e

IMAGE_NAME="shimpy-builder"
OUTPUT_DIR="$(pwd)"

# Find the --shim argument so we can volume-mount it
SHIM_PATH=""
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --shim)
            SHIM_PATH="$(realpath "$2")"
            ARGS+=(--shim "/shim/$(basename "$SHIM_PATH")")
            shift 2
            ;;
        --output)
            ARGS+=(--output "/output/$(basename "$2")")
            shift 2
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ -z "$SHIM_PATH" ]]; then
    echo "Error: --shim is required."
    echo "Usage: bash docker-build.sh --board <board> --shim /path/to/shim.bin [options]"
    exit 1
fi

# Build the Docker image if not already built
if ! docker image inspect "$IMAGE_NAME" &>/dev/null; then
    echo "==> Building shimpy Docker image..."
    docker build -t "$IMAGE_NAME" "$(dirname "$0")"
fi

echo "==> Running shimpy build in Docker..."
docker run --rm \
    --privileged \
    -v "$(dirname "$SHIM_PATH"):/shim:ro" \
    -v "$OUTPUT_DIR:/output" \
    "$IMAGE_NAME" \
    build "${ARGS[@]}" --output "/output/shimpy-output.bin"

echo ""
echo "Done. Output: $OUTPUT_DIR/shimpy-output.bin"
