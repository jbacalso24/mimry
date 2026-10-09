#!/usr/bin/env bash
# Render the AUR package for a version already published to PyPI: PKGBUILD
# with the version and checksums, the hash-pinned runtime requirements from
# uv.lock, and .SRCINFO.
#
# Usage: packaging/aur/update.sh <version> <out-dir> [pkgrel]
#
# Run it from a checkout of that version's tag, so uv.lock matches, on Arch as
# a normal user with uv and pacman-contrib installed.
set -euo pipefail

version="$1"
out="$(mkdir -p "$2" && cd "$2" && pwd)"
pkgrel="${3:-1}"
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../.."

if ! tr -d '\r' < pyproject.toml | grep -qx "version = \"$version\""; then
  echo "pyproject.toml is not at version $version; check out the v$version tag first." >&2
  exit 1
fi

uv export --frozen --no-dev --no-emit-project --no-header \
  --format requirements-txt > "$out/requirements-$version.txt"
sed -e "s/^pkgver=.*/pkgver=$version/" -e "s/^pkgrel=.*/pkgrel=$pkgrel/" \
  packaging/aur/PKGBUILD > "$out/PKGBUILD"

cd "$out"
updpkgsums
makepkg --printsrcinfo > .SRCINFO
