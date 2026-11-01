#!/bin/sh
set -eu

usage() {
  echo 'usage: run-verification.sh <focused|all> [receipt] | <evaluate-profile|review-packet|score-bertscore|measure-graph-spectra|measure-magnetic-attribution|audit-trace-provenance|compare-answer-elements|measure-phase-navigation|score-phase-navigation|compare-support-access|compare-support-access-corpora|export-page-representations|generate-complementary-context|evaluate-complementary-context|page-roles|audit-embedded-links|qualify-local-objective|compare-block-phase|demonstrate-embedded-v2|refine-embedded-v2> <input-root> <output-root> [receipt] | <candidate-retrieve|measure-docstyle> <input-root> <output-root> <candidate-root> [receipt]' >&2
  echo '       named form: run-verification.sh <command> --input-root=<p> --output-root=<p> [--candidate-root=<p>] [--receipt=<p>]' >&2
  exit 2
}
[ "$#" -ge 1 ] || usage
command_name=$1; shift
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
named_input_root=; named_output_root=; named_candidate_root=; named_receipt=; named_seen=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --input-root=*) named_input_root=${1#--input-root=}; named_seen=1; shift ;;
    --output-root=*) named_output_root=${1#--output-root=}; named_seen=1; shift ;;
    --candidate-root=*) named_candidate_root=${1#--candidate-root=}; named_seen=1; shift ;;
    --receipt=*) named_receipt=${1#--receipt=}; named_seen=1; shift ;;
    --*) echo verification-option-invalid >&2; exit 2 ;;
    *) break ;;
  esac
done
if [ "$named_seen" -eq 1 ]; then
  [ "$#" -eq 0 ] || usage
  input_root=$named_input_root; output_root=$named_output_root; candidate_root=$named_candidate_root
  receipt=${named_receipt:-$repo/.tmp/verification-image.json}
  case "$command_name" in
    candidate-retrieve|measure-docstyle) [ -n "$input_root" ] && [ -n "$output_root" ] || { echo verification-operator-input-output-required >&2; exit 2; }
      [ -n "$candidate_root" ] || { echo verification-candidate-root-required >&2; exit 2; } ;;
    evaluate-profile|review-packet|score-bertscore|measure-graph-spectra|measure-magnetic-attribution|audit-trace-provenance|compare-answer-elements|measure-phase-navigation|score-phase-navigation|compare-support-access|compare-support-access-corpora|export-page-representations|generate-complementary-context|evaluate-complementary-context|page-roles|audit-embedded-links|qualify-local-objective|compare-block-phase|demonstrate-embedded-v2|refine-embedded-v2) [ -n "$input_root" ] && [ -n "$output_root" ] || { echo verification-operator-input-output-required >&2; exit 2; }
      [ -z "$candidate_root" ] || { echo verification-command-invalid >&2; exit 2; } ;;
    focused|all) [ -z "$input_root" ] && [ -z "$output_root" ] && [ -z "$candidate_root" ] || { echo verification-command-invalid >&2; exit 2; } ;;
    *) echo verification-command-invalid >&2; exit 2 ;;
  esac
elif [ "$command_name" = candidate-retrieve ] || [ "$command_name" = measure-docstyle ]; then
  [ "$#" -ge 3 ] && [ "$#" -le 4 ] || { echo verification-candidate-root-required >&2; exit 2; }
  input_root=$1; output_root=$2; candidate_root=$3; receipt=${4:-$repo/.tmp/verification-image.json}
elif [ "$command_name" = evaluate-profile ] || [ "$command_name" = review-packet ] || [ "$command_name" = score-bertscore ] || [ "$command_name" = measure-graph-spectra ] || [ "$command_name" = measure-magnetic-attribution ] || [ "$command_name" = audit-trace-provenance ] || [ "$command_name" = compare-answer-elements ] || [ "$command_name" = measure-phase-navigation ] || [ "$command_name" = score-phase-navigation ] || [ "$command_name" = compare-support-access ] || [ "$command_name" = compare-support-access-corpora ] || [ "$command_name" = export-page-representations ] || [ "$command_name" = generate-complementary-context ] || [ "$command_name" = evaluate-complementary-context ] || [ "$command_name" = page-roles ] || [ "$command_name" = audit-embedded-links ] || [ "$command_name" = qualify-local-objective ] || [ "$command_name" = compare-block-phase ] || [ "$command_name" = demonstrate-embedded-v2 ] || [ "$command_name" = refine-embedded-v2 ]; then
  [ "$#" -ge 2 ] && [ "$#" -le 3 ] || { echo verification-operator-input-output-required >&2; exit 2; }
  input_root=$1; output_root=$2; receipt=${3:-$repo/.tmp/verification-image.json}
else
  [ "$#" -le 1 ] || { echo verification-command-invalid >&2; exit 2; }
  case "$command_name" in focused|all) ;; *) echo verification-command-invalid >&2; exit 2 ;; esac
  receipt=${1:-$repo/.tmp/verification-image.json}
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

if [ "$command_name" = evaluate-profile ] || [ "$command_name" = review-packet ] || [ "$command_name" = score-bertscore ] || [ "$command_name" = candidate-retrieve ] || [ "$command_name" = measure-docstyle ] || [ "$command_name" = measure-graph-spectra ] || [ "$command_name" = measure-magnetic-attribution ] || [ "$command_name" = audit-trace-provenance ] || [ "$command_name" = compare-answer-elements ] || [ "$command_name" = measure-phase-navigation ] || [ "$command_name" = score-phase-navigation ] || [ "$command_name" = compare-support-access ] || [ "$command_name" = compare-support-access-corpora ] || [ "$command_name" = export-page-representations ] || [ "$command_name" = generate-complementary-context ] || [ "$command_name" = evaluate-complementary-context ] || [ "$command_name" = page-roles ] || [ "$command_name" = audit-embedded-links ] || [ "$command_name" = qualify-local-objective ] || [ "$command_name" = compare-block-phase ] || [ "$command_name" = demonstrate-embedded-v2 ] || [ "$command_name" = refine-embedded-v2 ]; then
  input_root=$(CDPATH= cd -- "$input_root" && pwd)
  output_root=$(CDPATH= cd -- "$output_root" && pwd)
  [ "$input_root" != "$output_root" ] && [ "$input_root" != "$repo" ] && [ "$output_root" != "$repo" ] && [ -f "$input_root/operator-request.json" ] || { echo verification-operator-root-admission-invalid >&2; exit 2; }
  case "$input_root/" in "$repo/"*|"$output_root/"*) echo verification-operator-root-admission-invalid >&2; exit 2 ;; esac
  case "$output_root/" in "$repo/"*|"$input_root/"*) echo verification-operator-root-admission-invalid >&2; exit 2 ;; esac
  case "$repo/" in "$input_root/"*|"$output_root/"*) echo verification-operator-root-admission-invalid >&2; exit 2 ;; esac
  [ "$(find "$output_root" -mindepth 1 -maxdepth 1 -exec basename {} \; | sort)" = .retrieval-arena-output-owner ] || { echo verification-operator-output-not-fresh >&2; exit 2; }
  if [ "$command_name" = candidate-retrieve ] || [ "$command_name" = measure-docstyle ]; then
    candidate_root=$(CDPATH= cd -- "$candidate_root" && pwd)
    admit_pair() {
      [ "$1" != "$2" ] || { echo verification-operator-root-admission-invalid >&2; exit 2; }
      case "$1/" in "$2/"*) echo verification-operator-root-admission-invalid >&2; exit 2 ;; esac
      case "$2/" in "$1/"*) echo verification-operator-root-admission-invalid >&2; exit 2 ;; esac
    }
    admit_pair "$repo" "$input_root"; admit_pair "$repo" "$output_root"
    admit_pair "$repo" "$candidate_root"; admit_pair "$input_root" "$output_root"
    admit_pair "$input_root" "$candidate_root"; admit_pair "$output_root" "$candidate_root"
  fi
  # S052: measure-docstyle is a single phase with the candidate mounted read-only.
  if [ "$command_name" = measure-docstyle ]; then
    exec docker run --rm --platform "$platform" --network none --read-only --security-opt no-new-privileges --cap-drop ALL --pids-limit 512 --memory 2g --cpus 2 \
      --mount "type=bind,src=$repo,dst=/workspace/source,readonly" --mount "type=bind,src=$input_root,dst=/workspace/input,readonly" \
      --mount "type=bind,src=$candidate_root,dst=/workspace/candidate,readonly" --mount "type=bind,src=$output_root,dst=/workspace/output" \
      --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m,uid=65532,gid=65532 \
      --env "RA_SOURCE_COMMIT=$commit" --env "RA_SOURCE_TREE=$tree" --env "RA_DEPENDENCY_LOCK_SHA256=$receipt_lock" --env "RA_IMAGE_REFERENCE=$image" --env "RA_PLATFORM=$platform" \
      --env HOME=/tmp --env TMPDIR=/tmp --env PYTHONHASHSEED=0 --env PYTHONIOENCODING=utf-8 --env TZ=UTC "$image" measure-docstyle
  fi
  if [ "$command_name" = candidate-retrieve ]; then
    run_operator() {
      docker run --rm --platform "$platform" --network none --read-only --security-opt no-new-privileges --cap-drop ALL --pids-limit 512 --memory 2g --cpus 2 \
        --mount "type=bind,src=$repo,dst=/workspace/source,readonly" --mount "type=bind,src=$input_root,dst=/workspace/input,readonly" \
        --mount "type=bind,src=$candidate_root,dst=/workspace/candidate,readonly" --mount "type=bind,src=$output_root,dst=/workspace/output" \
        --tmpfs /tmp:rw,nosuid,nodev,size=256m,uid=65532,gid=65532 \
        --env "RA_SOURCE_COMMIT=$commit" --env "RA_SOURCE_TREE=$tree" --env "RA_DEPENDENCY_LOCK_SHA256=$receipt_lock" --env "RA_IMAGE_REFERENCE=$image" --env "RA_PLATFORM=$platform" \
        --env HOME=/tmp --env TMPDIR=/tmp --env PYTHONHASHSEED=0 --env PYTHONIOENCODING=utf-8 --env TZ=UTC "$image" "$1"
    }
    run_operator candidate-retrieve-prepare
    launch=$output_root/.candidate-retrieval-work/launch.json
    provider_image=$(sed -n 's/.*"provider_image"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$launch")
    if ! printf '%s\n' "$provider_image" | grep -Eq '^retrieval-arena/[a-z0-9-]+@sha256:[0-9a-f]{64}$'; then
      printf '{"exit_code":-2}\n' > "$output_root/.candidate-retrieval-work/provider-exit.json"
      run_operator candidate-retrieve-finalize
      exit $?
    fi
    # S026: the navigator provider runs on the default bridge with the host model alias (S046: the repo-search agent's two variants too).
    launch_network=$(sed -n 's/.*"network"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$launch")
    launch_provider=$(sed -n 's/.*"provider_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$launch")
    if [ "$launch_network" = host_model_alias ] && { [ "$launch_provider" = rag_llm_guided_search ] || [ "$launch_provider" = rag_repo_search_agent ] || [ "$launch_provider" = rag_repo_search_agent_nolinks ]; }; then
      set -- --add-host host.docker.internal:host-gateway
    elif [ "$launch_network" = none ]; then
      set -- --network none
    else
      printf '{"exit_code":-2}
' > "$output_root/.candidate-retrieval-work/provider-exit.json"
      run_operator candidate-retrieve-finalize
      exit $?
    fi
    provider_exit=0
    docker run --rm --platform linux/amd64 --user 65532:65532 "$@" --read-only --security-opt no-new-privileges --cap-drop ALL --pids-limit 512 --memory 2g --cpus 2 \
      --mount "type=bind,src=$output_root/.candidate-retrieval-work/provider-input,dst=/input,readonly" \
      --mount "type=bind,src=$output_root/.candidate-retrieval-work/provider-output,dst=/output" \
      --tmpfs /tmp:rw,noexec,nosuid,nodev,size=128m "$provider_image" || provider_exit=$?
    printf '{"exit_code":%s}\n' "$provider_exit" > "$output_root/.candidate-retrieval-work/provider-exit.json"
    run_operator candidate-retrieve-finalize
    exit $?
  fi
  operator_memory=2g
  [ "$command_name" != score-bertscore ] || operator_memory=8g
  [ "$command_name" != measure-graph-spectra ] || operator_memory=4g
  [ "$command_name" != measure-magnetic-attribution ] || operator_memory=4g
  [ "$command_name" != measure-phase-navigation ] || operator_memory=8g
  [ "$command_name" != audit-trace-provenance ] || operator_memory=4g
  [ "$command_name" != compare-answer-elements ] || operator_memory=4g
  [ "$command_name" != compare-support-access ] && [ "$command_name" != compare-support-access-corpora ] || operator_memory=8g
  [ "$command_name" != export-page-representations ] && [ "$command_name" != generate-complementary-context ] && [ "$command_name" != evaluate-complementary-context ] || operator_memory=8g
  [ "$command_name" != page-roles ] || operator_memory=8g
  operator_cpus=2
  [ "$command_name" != measure-phase-navigation ] || operator_cpus=4
  [ "$command_name" != compare-support-access ] && [ "$command_name" != compare-support-access-corpora ] || operator_cpus=4
  [ "$command_name" != export-page-representations ] && [ "$command_name" != generate-complementary-context ] && [ "$command_name" != evaluate-complementary-context ] || operator_cpus=4
  [ "$command_name" != page-roles ] || operator_cpus=4
  exec docker run --rm --platform "$platform" --network none --read-only --security-opt no-new-privileges --cap-drop ALL --pids-limit 512 --memory "$operator_memory" --cpus "$operator_cpus" \
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
