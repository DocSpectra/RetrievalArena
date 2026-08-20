#!/bin/sh
set -eu

tag=${1:-retrieval-arena/verification:local}
receipt=${2:-.tmp/verification-image.json}
platform=${3:-linux/amd64}
[ "$platform" = linux/amd64 ] || { echo verification-platform-unregistered >&2; exit 2; }
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
commit=$(git -C "$repo" rev-parse HEAD)
tree=$(git -C "$repo" write-tree)
if command -v sha256sum >/dev/null 2>&1; then
  lock_hash=$(sha256sum "$repo/containers/verification/requirements.lock" | sed 's/ .*//')
elif command -v shasum >/dev/null 2>&1; then
  lock_hash=$(shasum -a 256 "$repo/containers/verification/requirements.lock" | sed 's/ .*//')
else
  echo sha256-tool-unavailable >&2
  exit 2
fi

docker build --platform "$platform" --pull=false \
  --file "$repo/containers/verification/Dockerfile" --tag "$tag" \
  --build-arg "SOURCE_COMMIT=$commit" --build-arg "SOURCE_TREE=$tree" \
  --build-arg "DEPENDENCY_LOCK_SHA256=$lock_hash" --build-arg "TARGET_OCI_PLATFORM=$platform" "$repo"
image_id=$(docker image inspect --format '{{.Id}}' "$tag")
printf '%s\n' "$image_id" | grep -Eq '^sha256:[0-9a-f]{64}$' || { echo verification-image-id-invalid >&2; exit 2; }

case "$receipt" in /*) receipt_path=$receipt ;; *) receipt_path=$repo/$receipt ;; esac
mkdir -p "$(dirname -- "$receipt_path")"
printf '{"dependency_lock_sha256":"%s","image":{"reference":"%s","tag":"%s"},"platform":"%s","schema_version":"retrieval_arena.verification_image_receipt.v1","source_commit":"%s","source_tree":"%s"}\n' \
  "$lock_hash" "$image_id" "$tag" "$platform" "$commit" "$tree" > "$receipt_path"
printf '%s\n' "$receipt_path"
