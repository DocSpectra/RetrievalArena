#!/bin/sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo 'usage: run-verification.sh <focused|all> [receipt]' >&2
  exit 2
fi
case "$1" in focused|all) command_name=$1 ;; *) echo verification-command-invalid >&2; exit 2 ;; esac
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
receipt=${2:-$repo/.tmp/verification-image.json}
case "$receipt" in /*) ;; *) receipt=$repo/$receipt ;; esac

image=$(sed -n 's/.*"reference"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$receipt")
platform=$(sed -n 's/.*"platform"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$receipt")
schema=$(sed -n 's/.*"schema_version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$receipt")
[ "$schema" = retrieval_arena.verification_image_receipt.v1 ] || { echo verification-receipt-schema-invalid >&2; exit 2; }
[ "$platform" = linux/amd64 ] || { echo verification-platform-unregistered >&2; exit 2; }
printf '%s\n' "$image" | grep -Eq '^(sha256:[0-9a-f]{64}|[^[:space:]@]+@sha256:[0-9a-f]{64})$' || { echo verification-image-reference-mutable >&2; exit 2; }

exec docker run --rm --platform "$platform" --network none --read-only \
  --security-opt no-new-privileges --cap-drop ALL --pids-limit 512 \
  --mount "type=bind,src=$repo,dst=/workspace/source,readonly" \
  --tmpfs /workspace/source/.tmp_unit_tests:rw,nosuid,nodev,size=512m,uid=65532,gid=65532 \
  --tmpfs /workspace/source/unit_tests/_tmp_html_report:rw,nosuid,nodev,size=256m,uid=65532,gid=65532 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m,uid=65532,gid=65532 \
  --env HOME=/tmp --env LANG=C.UTF-8 --env LC_ALL=C.UTF-8 \
  --env PATH=/usr/local/bin:/usr/bin:/bin --env PYTHONDONTWRITEBYTECODE=1 \
  --env PYTHONHASHSEED=0 --env PYTHONIOENCODING=utf-8 \
  --env TMPDIR=/workspace/source/.tmp_unit_tests --env TZ=UTC \
  "$image" "$command_name"
