#!/bin/sh
set -eu

if [ "$#" -lt 1 ]; then
  echo 'usage: run-verification.sh <focused|all> [receipt] | <evaluate-profile|review-packet> <input-root> <output-root> [receipt]' >&2
  exit 2
fi
command_name=$1
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$command_name" = evaluate-profile ] || [ "$command_name" = review-packet ]; then
  [ "$#" -ge 3 ] && [ "$#" -le 4 ] || { echo verification-operator-input-output-required >&2; exit 2; }
  input_root=$2; output_root=$3; receipt=${4:-$repo/.tmp/verification-image.json}
else
  [ "$#" -le 2 ] || { echo verification-command-invalid >&2; exit 2; }
  case "$command_name" in focused|all) ;; *) echo verification-command-invalid >&2; exit 2 ;; esac
  receipt=${2:-$repo/.tmp/verification-image.json}
fi
case "$receipt" in /*) ;; *) receipt=$repo/$receipt ;; esac

image=$(sed -n 's/.*"reference"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$receipt")
platform=$(sed -n 's/.*"platform"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$receipt")
schema=$(sed -n 's/.*"schema_version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$receipt")
[ "$schema" = retrieval_arena.verification_image_receipt.v2 ] || { echo verification-receipt-schema-invalid >&2; exit 2; }
[ "$platform" = linux/amd64 ] || { echo verification-platform-unregistered >&2; exit 2; }
printf '%s\n' "$image" | grep -Eq '^(sha256:[0-9a-f]{64}|[^[:space:]@]+@sha256:[0-9a-f]{64})$' || { echo verification-image-reference-mutable >&2; exit 2; }
receipt_hash=$(sed -n 's/.*"receipt_sha256"[[:space:]]*:[[:space:]]*"\([0-9a-f]*\)".*/\1/p' "$receipt")
zero=$(printf '%064d' 0)
zero_receipt=$(sed "s/\"receipt_sha256\"[[:space:]]*:[[:space:]]*\"[0-9a-f]\{64\}\"/\"receipt_sha256\":\"$zero\"/" "$receipt")
if command -v sha256sum >/dev/null 2>&1; then actual_receipt_hash=$(printf '%s\n' "$zero_receipt" | sha256sum | sed 's/ .*//'); else actual_receipt_hash=$(printf '%s\n' "$zero_receipt" | shasum -a 256 | sed 's/ .*//'); fi
[ "$receipt_hash" = "$actual_receipt_hash" ] || { echo verification-receipt-self-hash-invalid >&2; exit 2; }
if command -v sha256sum >/dev/null 2>&1; then lock_hash=$(sha256sum "$repo/containers/verification/requirements.lock" | sed 's/ .*//'); else lock_hash=$(shasum -a 256 "$repo/containers/verification/requirements.lock" | sed 's/ .*//'); fi
commit=$(sed -n 's/.*"source_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$receipt")
tree=$(sed -n 's/.*"source_tree"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$receipt")
receipt_lock=$(sed -n 's/.*"dependency_lock_sha256"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$receipt")
[ "$receipt_lock" = "$lock_hash" ] && printf '%s' "$commit" | grep -Eq '^[0-9a-f]{40}$' && printf '%s' "$tree" | grep -Eq '^[0-9a-f]{40}$' || { echo verification-receipt-identity-invalid >&2; exit 2; }
test -z "$(git -C "$repo" status --porcelain)" || { echo verification-source-not-clean >&2; exit 2; }
git -C "$repo" diff --quiet || { echo verification-source-worktree-dirty >&2; exit 2; }
git -C "$repo" diff --cached --quiet || { echo verification-source-index-dirty >&2; exit 2; }
[ "$(git -C "$repo" rev-parse HEAD)" = "$commit" ] && [ "$(git -C "$repo" write-tree)" = "$tree" ] || { echo verification-mounted-source-identity-mismatch >&2; exit 2; }
label_commit=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")
label_tree=$(docker image inspect --format '{{ index .Config.Labels "io.retrieval-arena.source.tree" }}' "$image")
label_lock=$(docker image inspect --format '{{ index .Config.Labels "io.retrieval-arena.verification.dependency-lock-sha256" }}' "$image")
label_platform=$(docker image inspect --format '{{ index .Config.Labels "io.retrieval-arena.verification.platform" }}' "$image")
[ "$label_commit" = "$commit" ] && [ "$label_tree" = "$tree" ] && [ "$label_lock" = "$receipt_lock" ] && [ "$label_platform" = "$platform" ] || { echo verification-image-label-mismatch >&2; exit 2; }

if [ "$command_name" = evaluate-profile ] || [ "$command_name" = review-packet ]; then
  input_root=$(CDPATH= cd -- "$input_root" && pwd)
  output_root=$(CDPATH= cd -- "$output_root" && pwd)
  [ "$input_root" != "$output_root" ] && [ "$input_root" != "$repo" ] && [ "$output_root" != "$repo" ] && [ -f "$input_root/operator-request.json" ] || { echo verification-operator-root-admission-invalid >&2; exit 2; }
  case "$input_root/" in "$repo/"*|"$output_root/"*) echo verification-operator-root-admission-invalid >&2; exit 2 ;; esac
  case "$output_root/" in "$repo/"*|"$input_root/"*) echo verification-operator-root-admission-invalid >&2; exit 2 ;; esac
  case "$repo/" in "$input_root/"*|"$output_root/"*) echo verification-operator-root-admission-invalid >&2; exit 2 ;; esac
  [ "$(find "$output_root" -mindepth 1 -maxdepth 1 -exec basename {} \; | sort)" = .retrieval-arena-output-owner ] || { echo verification-operator-output-not-fresh >&2; exit 2; }
  exec docker run --rm --platform "$platform" --network none --read-only --security-opt no-new-privileges --cap-drop ALL --pids-limit 512 --memory 2g --cpus 2 \
    --mount "type=bind,src=$repo,dst=/workspace/source,readonly" --mount "type=bind,src=$input_root,dst=/workspace/input,readonly" --mount "type=bind,src=$output_root,dst=/workspace/output" \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m,uid=65532,gid=65532 \
    --env "RA_SOURCE_COMMIT=$commit" --env "RA_SOURCE_TREE=$tree" --env "RA_DEPENDENCY_LOCK_SHA256=$receipt_lock" --env "RA_IMAGE_REFERENCE=$image" --env "RA_PLATFORM=$platform" \
    --env HOME=/tmp --env TMPDIR=/tmp --env PYTHONHASHSEED=0 --env PYTHONIOENCODING=utf-8 --env TZ=UTC "$image" "$command_name"
fi

exec docker run --rm --platform "$platform" --network none --read-only \
  --security-opt no-new-privileges --cap-drop ALL --pids-limit 512 \
  --memory 2g --cpus 2 \
  --mount "type=bind,src=$repo,dst=/workspace/source,readonly" \
  --tmpfs /workspace/source/.tmp_unit_tests:rw,nosuid,nodev,size=512m,uid=65532,gid=65532 \
  --tmpfs /workspace/source/unit_tests/_tmp_html_report:rw,nosuid,nodev,size=256m,uid=65532,gid=65532 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m,uid=65532,gid=65532 \
  --env HOME=/tmp --env LANG=C.UTF-8 --env LC_ALL=C.UTF-8 \
  --env PATH=/usr/local/bin:/usr/bin:/bin --env PYTHONDONTWRITEBYTECODE=1 \
  --env PYTHONHASHSEED=0 --env PYTHONIOENCODING=utf-8 \
  --env TMPDIR=/workspace/source/.tmp_unit_tests --env TZ=UTC \
  --env "RA_SOURCE_COMMIT=$commit" --env "RA_SOURCE_TREE=$tree" \
  --env "RA_DEPENDENCY_LOCK_SHA256=$receipt_lock" --env "RA_IMAGE_REFERENCE=$image" \
  "$image" "$command_name"
