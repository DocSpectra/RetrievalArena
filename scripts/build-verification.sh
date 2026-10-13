#!/bin/sh
set -eu

tag=${1:-retrieval-arena/verification:local}
receipt=${2:-.tmp/verification-image.json}
platform=${3:-linux/amd64}
provisioned_image=${4:-}
[ "$platform" = linux/amd64 ] || { echo verification-platform-unregistered >&2; exit 2; }
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test -z "$(git -C "$repo" status --porcelain)" || { echo verification-source-not-clean >&2; exit 2; }
git -C "$repo" diff --quiet || { echo verification-source-worktree-dirty >&2; exit 2; }
git -C "$repo" diff --cached --quiet || { echo verification-source-index-dirty >&2; exit 2; }
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

set --
if [ -n "$provisioned_image" ]; then
  printf '%s\n' "$provisioned_image" | grep -Eq '^sha256:[0-9a-f]{64}$' || { echo verification-provisioned-image-mutable >&2; exit 2; }
  actual=$(docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}} {{index .Config.Labels "io.retrieval-arena.verification.dependency-lock-sha256"}}' "$provisioned_image")
  [ "$actual" = "$provisioned_image linux/amd64 $lock_hash" ] || { echo verification-provisioned-image-mismatch >&2; exit 2; }
  local_tag="retrieval-arena/provisioned:${provisioned_image#sha256:}"
  docker tag "$provisioned_image" "$local_tag"
  set -- --build-arg "BASE_IMAGE=$local_tag" --build-arg PROVISIONED=true
fi
docker build --network=none --platform "$platform" --pull=false "$@" \
  --file "$repo/containers/verification/Dockerfile" --tag "$tag" \
  --build-arg "SOURCE_COMMIT=$commit" --build-arg "SOURCE_TREE=$tree" \
  --build-arg "DEPENDENCY_LOCK_SHA256=$lock_hash" --build-arg "TARGET_OCI_PLATFORM=$platform" "$repo"
image_id=$(docker image inspect --format '{{.Id}}' "$tag")
printf '%s\n' "$image_id" | grep -Eq '^sha256:[0-9a-f]{64}$' || { echo verification-image-id-invalid >&2; exit 2; }

case "$receipt" in /*) receipt_path=$receipt ;; *) receipt_path=$repo/$receipt ;; esac
mkdir -p "$(dirname -- "$receipt_path")"
zero=$(printf '%064d' 0)
text=$(printf '{"dependency_lock_sha256":"%s","image":{"reference":"%s","tag":"%s"},"platform":"%s","receipt_sha256":"%s","schema_version":"retrieval_arena.verification_image_receipt.v2","source_commit":"%s","source_tree":"%s"}' \
  "$lock_hash" "$image_id" "$tag" "$platform" "$zero" "$commit" "$tree")
printf '%s\n' "$text" > "$receipt_path"
if command -v sha256sum >/dev/null 2>&1; then receipt_hash=$(sha256sum "$receipt_path" | sed 's/ .*//'); else receipt_hash=$(shasum -a 256 "$receipt_path" | sed 's/ .*//'); fi
printf '%s\n' "${text%$zero*}$receipt_hash${text#*$zero}" > "$receipt_path"
printf '%s\n' "$receipt_path"
