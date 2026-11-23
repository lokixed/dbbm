#!/bin/sh
# Install the pinned rclone release into $1 (default /usr/local/bin), verifying its SHA-256.
# Used by the Dockerfile and CI so both run exactly the same binary. Needs curl and python3.
set -eu

VERSION=v1.76.0
DEST="${1:-/usr/local/bin}"

case "$(uname -m)" in
    x86_64 | amd64)
        ARCH=amd64
        SHA256=b00f00b3266c3764413afe599d3ec316f6f43b3e0ea8a0bc1d186d2133e46df7
        ;;
    aarch64 | arm64)
        ARCH=arm64
        SHA256=ee169016675e23a69d77240565133ff7dbc9b33d16fa8f7053d4af39f1eaeb78
        ;;
    *)
        echo "Unsupported architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

NAME="rclone-${VERSION}-linux-${ARCH}"
BASE="https://github.com/rclone/rclone/releases/download/${VERSION}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

curl -fsSL --retry 3 -o "$WORK/${NAME}.zip" "${BASE}/${NAME}.zip"
curl -fsSL --retry 3 -o "$WORK/SHA256SUMS" "${BASE}/SHA256SUMS"

# The pinned hash is the trust anchor; the published list must agree with it too.
if ! grep -qx "${SHA256}  ${NAME}.zip" "$WORK/SHA256SUMS"; then
    echo "SHA256SUMS does not list the pinned checksum for ${NAME}.zip" >&2
    exit 1
fi
(cd "$WORK" && echo "${SHA256}  ${NAME}.zip" | sha256sum -c -)

python3 - "$WORK/${NAME}.zip" "${NAME}/rclone" "$WORK/rclone" <<'EOF'
import shutil
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive, archive.open(sys.argv[2]) as src, open(sys.argv[3], "wb") as dst:
    shutil.copyfileobj(src, dst)
EOF

mkdir -p "$DEST"
install -m 0755 "$WORK/rclone" "$DEST/rclone"
"$DEST/rclone" version
