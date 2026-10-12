"""Thin host shim for the pooled-support operator (containers/support/support_operator.py).

Argument passing only: it writes the request with the input inventory, mounts the RA
source read-only, picks the network, and passes a credential by name. All admission
and logic run in the container. Works with docker or podman on Linux, macOS and Windows.

  python scripts/run_support.py <pool|verify|review-packet|import-review> --image <ref>
      --input-root <dir> --output-root <new dir> --args <json file>
      [--model-dir <dir>] [--engine docker|podman] [--allow-dirty]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

RA = Path(__file__).resolve().parents[1]
NETWORK = {"pool": "none", "verify": "external_api", "review-packet": "none", "import-review": "none"}
CREDENTIAL = {"verify": "ANTHROPIC_API_KEY"}


def git(*argv):
    return subprocess.run(["git", "-C", str(RA), *argv], capture_output=True, text=True, check=True).stdout.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=sorted(NETWORK))
    ap.add_argument("--image", required=True)
    ap.add_argument("--input-root", required=True, type=Path)
    ap.add_argument("--output-root", required=True, type=Path)
    ap.add_argument("--args", required=True, type=Path, help="JSON object of operator args (paths relative to the input root)")
    ap.add_argument("--model-dir", type=Path)
    ap.add_argument("--engine", default="docker", choices=["docker", "podman"])
    ap.add_argument("--allow-dirty", action="store_true", help="development only; the receipt records it")
    a = ap.parse_args(argv)
    dirty = git("status", "--porcelain", "--", "retrieval_arena", "containers/support") != ""
    if dirty and not a.allow_dirty:
        sys.exit("support-source-dirty: commit RA first (or --allow-dirty for development)")
    src_in, out = a.input_root.resolve(), a.output_root.resolve()
    if out.exists() and any(out.iterdir()):
        sys.exit("output-not-fresh")
    out.mkdir(parents=True, exist_ok=True)
    args = json.loads(a.args.read_text(encoding="utf-8"))
    files = {}
    for path in sorted(src_in.rglob("*")):
        if path.is_file() and path.name != "operator-request.json":
            files[path.relative_to(src_in).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    request = {"schema_version": "retrieval_arena.pooled_support_operator_request.v1", "command": a.command,
               "args": args, "input_inventory": files}
    (src_in / "operator-request.json").write_text(json.dumps(request, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    network = NETWORK[a.command]
    cmd = [a.engine, "run", "--rm", "--network", "bridge" if network == "external_api" else "none",
           "--entrypoint", "python",
           "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HF_HUB_OFFLINE=1", "-e", "TRANSFORMERS_OFFLINE=1",
           "-e", f"RA_SUPPORT_NETWORK={network}", "-e", f"RA_IMAGE_REFERENCE={a.image}",
           "-e", f"RA_SOURCE_COMMIT={git('rev-parse', 'HEAD')}", "-e", f"RA_SOURCE_TREE={git('rev-parse', 'HEAD^{tree}')}",
           "-e", f"RA_SOURCE_DIRTY={str(dirty).lower()}",
           "--mount", f"type=bind,src={RA},dst=/opt/ra-src,readonly",
           "--mount", f"type=bind,src={src_in},dst=/input,readonly",
           "--mount", f"type=bind,src={out},dst=/output"]
    if a.command in CREDENTIAL:
        cmd += ["-e", CREDENTIAL[a.command]]  # by name: the value comes from this process's environment
    if a.model_dir:
        cmd += ["--mount", f"type=bind,src={a.model_dir.resolve()},dst=/models/embedding,readonly"]
    cmd += [a.image, "-B", "/opt/ra-src/containers/support/support_operator.py", a.command]
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
