#!/bin/sh
# Install the pinned age release (age and age-keygen) into $1 (default /usr/local/bin), verifying
# the tarball's SHA-256. Used by the Dockerfile and CI so both run exactly the same binaries.
# Needs curl and tar. The pinned hashes are the digests GitHub publishes for the release assets
# (https://github.com/FiloSottile/age/releases/tag/v1.3.2); update them together with VERSION.
set -eu

VERSION=v1.3.2
DEST="${1:-/usr/local/bin}"

case "$(uname -m)" in
    x86_64 | amd64)
        ARCH=amd64
        SHA256=cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10
        ;;
    aarch64 | arm64)
        ARCH=arm64
        SHA256=6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4
        ;;
    *)
        echo "Unsupported architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

NAME="age-${VERSION}-linux-${ARCH}.tar.gz"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

curl -fsSL --retry 3 -o "$WORK/${NAME}" "https://github.com/FiloSottile/age/releases/download/${VERSION}/${NAME}"
(cd "$WORK" && echo "${SHA256}  ${NAME}" | sha256sum -c -)

tar -xzf "$WORK/${NAME}" -C "$WORK"
mkdir -p "$DEST"
install -m 0755 "$WORK/age/age" "$DEST/age"
install -m 0755 "$WORK/age/age-keygen" "$DEST/age-keygen"
"$DEST/age" --version
