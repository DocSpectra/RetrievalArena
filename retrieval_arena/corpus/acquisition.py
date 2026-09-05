"""Explicit host acquisition; no evaluator imports this operation implicitly.

Command-local core.longpaths mechanics adapted from DocSpectra SDM importer,
commit 00f0148d32ddaa498d744e249ff741837d8dca45.
Copyright (c) 2026 Rocker D'Antonio. MIT; see docs/remote_acquisition.md.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import uuid
import configparser
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit, unquote

from ..errors import ValidationError
from ..hashing import canonical_json_bytes, sha256_file, sha256_json
from ..manifests import finalize_manifest
from ..snapshots import corpus_doc_id, utc_now_iso
from .sources import CorpusSourceDescriptor

SCHEMA = "retrieval_arena.remote_git_acquisition.v1"
POLICY = "exact-git-anonymous-v1"
IMPORT_SCHEMA = "retrieval_arena.corpus_import_manifest.v2"
MARKER = ".retrieval-arena-acquisition-owner"
RECEIPT = "acquisition_receipt.json"
MANIFEST = "corpus_import_manifest.json"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
STRATEGY = "ls-remote-capture-fetch-exact-full-detached-v1"
REUSE = "verify-exact-never-refresh-v1"


def fail(code: str) -> None:
    # Never interpolate a Git diagnostic, URL, ref, or arbitrary exception.
    raise ValidationError("acquisition-" + code) from None


def canonical_url(value: str | None) -> str:
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        fail("url-invalid")
    try:
        url = urlsplit(value)
        if url.username is not None or url.password is not None or url.query or url.fragment:
            fail("url-credentials-or-suffix-rejected")
        if url.scheme == "file":
            if url.netloc or not url.path.startswith("/") or "%" in value:
                fail("url-invalid")
            path = Path(unquote(url.path))
            if os.name == "nt" and re.match(r"/[A-Za-z]:/", url.path):
                path = Path(url.path[1:])
            return path.resolve(strict=True).as_uri()
        if url.scheme != "https" or not url.hostname or url.port not in (None, 443):
            fail("url-invalid")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", url.hostname) or not re.fullmatch(r"/[A-Za-z0-9_./~-]+", url.path):
            fail("url-invalid")
        if any(p in {".", ".."} for p in url.path.split("/")):
            fail("url-invalid")
        return urlunsplit(("https", url.hostname.lower(), url.path.rstrip("/"), "", ""))
    except (OSError, ValueError):
        fail("url-invalid")


def ref_kind(ref: str | None) -> str:
    if not isinstance(ref, str) or not ref:
        fail("ref-invalid")
    if HEX40.fullmatch(ref):
        return "commit"
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", ref):
        fail("ref-invalid")
    if ref == "HEAD" or ".." in ref or "//" in ref or any(
        p.startswith(".") or p.endswith((".", ".lock")) or not p for p in ref.split("/")
    ):
        fail("ref-invalid")
    if ref.startswith("refs/heads/"):
        return "branch"
    if ref.startswith("refs/tags/"):
        return "tag"
    if ref.startswith("refs/") or re.fullmatch(r"[0-9a-fA-F]{4,40}", ref):
        fail("ref-invalid")
    return "short"


def descriptor_payload(d: CorpusSourceDescriptor) -> dict[str, Any]:
    if d.source_type != "git" or d.source_path is not None:
        fail("remote-descriptor-required")
    for item in (d.corpus_id, d.snapshot_id):
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", item):
            fail("descriptor-id-invalid")
    ref_kind(d.requested_ref)
    docs = d.docs_root
    if docs is not None and (not isinstance(docs, str) or not docs or
        PurePosixPath(docs).is_absolute() or "\\" in docs or
        any(p in {"", ".", "..", ".git"} for p in docs.split("/")) or
        any(ord(c) < 32 for c in docs)):
        fail("docs-root-invalid")
    for patterns in (d.include, d.exclude):
        if not isinstance(patterns, tuple) or any(not isinstance(p, str) or not p or
            "\\" in p or p.startswith("/") or ".." in p.split("/") or
            any(ord(c) < 32 for c in p) for p in patterns):
            fail("selection-invalid")
    return {"corpus_id": d.corpus_id, "snapshot_id": d.snapshot_id,
            "source_type": "git", "source_url": canonical_url(d.source_url),
            "requested_ref": d.requested_ref, "docs_root": docs,
            "include": list(d.include), "exclude": list(d.exclude)}


@dataclass(frozen=True)
class GitRuntime:
    executable: str
    network: bool = False

    def run(self, root: Path, *args: str, optional: bool = False) -> bytes:
        env = {k: v for k, v in os.environ.items() if k in
               {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"}}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0", GIT_ATTR_NOSYSTEM="1", GIT_NO_REPLACE_OBJECTS="1",
                   GIT_ALLOW_PROTOCOL="https" if self.network else "file")
        # SDM's long-path setting is per invocation, never global.
        argv = [self.executable, "-c", "core.longpaths=true", "-c", "core.autocrlf=false",
                "-c", "core.hooksPath=" + os.devnull, "-c", "credential.helper=",
                "-c", "http.followRedirects=false", "-c", "core.attributesFile=" + os.devnull,
                "-C", str(root), *args]
        try:
            result = subprocess.run(argv, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError):
            fail("git-execution-failed")
        if result.returncode and not optional:
            fail("git-command-failed")
        return result.stdout if not result.returncode else b""

    def text(self, root: Path, *args: str, optional: bool = False) -> str:
        try:
            return self.run(root, *args, optional=optional).decode("utf-8").strip()
        except UnicodeError:
            fail("git-response-invalid")


def _advertisement(git: GitRuntime, root: Path, url: str, requested: str) -> tuple[str, str]:
    rows: dict[str, str] = {}
    for line in git.text(root, "ls-remote", "--", url).splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not HEX40.fullmatch(parts[0]) or parts[1] in rows:
            fail("remote-response-invalid")
        rows[parts[1]] = parts[0]
    kind = ref_kind(requested)
    names = [requested] if kind != "short" else ["refs/heads/" + requested, "refs/tags/" + requested]
    matches = [name for name in names if name in rows]
    if len(matches) != 1:
        fail("ref-missing-or-ambiguous")
    name = matches[0]
    return name, rows.get(name + "^{}", rows[name])


def _safe_path(path: Path) -> Path:
    absolute = path.absolute()
    for parent in (absolute, *absolute.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            fail("root-link-rejected")
    if absolute != absolute.resolve():
        fail("root-noncanonical")
    return absolute


def _inventory(git: GitRuntime, repo: Path, commit: str, d: dict[str, Any]) -> list[dict[str, Any]]:
    records = git.run(repo, "ls-tree", "-rz", "--full-tree", commit)
    inventory = []
    all_paths = []
    for record in records.split(b"\0"):
        if not record:
            continue
        try:
            header, name = record.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split()
            relative = name.decode("utf-8")
        except (ValueError, UnicodeError):
            fail("tree-invalid")
        if mode not in {"100644", "100755"} or kind != "blob" or not HEX40.fullmatch(oid):
            fail("tree-entry-unsupported")
        if (PurePosixPath(relative).is_absolute() or "\\" in relative or
            any(p in {"", ".", ".."} or p.casefold() == ".git" for p in relative.split("/"))):
            fail("tree-path-invalid")
        file = repo / relative
        _safe_path(file)
        content = git.run(repo, "cat-file", "blob", oid)
        if not file.is_file() or file.read_bytes() != content:
            fail("materialized-file-mismatch")
        all_paths.append(relative)
        prefix = d["docs_root"] + "/" if d["docs_root"] else ""
        if not relative.startswith(prefix):
            continue
        selected = relative[len(prefix):]
        if not any(PurePosixPath(selected).match(p) for p in d["include"]):
            continue
        if any(PurePosixPath(selected).match(p) for p in d["exclude"]):
            continue
        inventory.append({"path": selected, "doc_id": corpus_doc_id(selected),
                          "size_bytes": len(content), "sha256": sha256_file(file)})
    if len({p.casefold() for p in all_paths}) != len(all_paths):
        fail("tree-case-collision")
    if not inventory:
        fail("selection-empty")
    return sorted(inventory, key=lambda row: row["path"])


def _observe(git: GitRuntime, repo: Path, commit: str, d: dict[str, Any]) -> dict[str, Any]:
    _safe_path(repo)
    if not (repo / ".git").is_dir() or (repo / ".git").is_symlink():
        fail("repository-not-independent")
    for path in repo.rglob("*"):
        _safe_path(path)
    config = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        config.read_string((repo / ".git/config").read_text())
    except (configparser.Error, UnicodeError):
        fail("git-config-invalid")
    if set(config.sections()) != {"core", 'remote "origin"'} or config.defaults():
        fail("git-config-invalid")
    allowed = {"repositoryformatversion": {"0"}, "filemode": {"true", "false"}, "bare": {"false"},
               "logallrefupdates": {"true"}, "ignorecase": {"true", "false"}, "symlinks": {"false", "true"},
               "longpaths": {"true"}, "autocrlf": {"false"}}
    if any(key not in allowed or value not in allowed[key] for key, value in config["core"].items()):
        fail("git-config-invalid")
    if dict(config['remote "origin"']) != {"url": d["source_url"]}:
        fail("origin-mismatch")
    for name in ("objects/info/alternates", "shallow", "info/grafts", "info/attributes"):
        if (repo / ".git" / name).exists():
            fail("repository-extension-rejected")
    if git.text(repo, "symbolic-ref", "-q", "HEAD", optional=True):
        fail("checkout-not-detached")
    if git.text(repo, "rev-parse", "HEAD") != commit:
        fail("commit-mismatch")
    if git.text(repo, "config", "--get", "remote.origin.url") != d["source_url"]:
        fail("origin-mismatch")
    if git.run(repo, "status", "--porcelain=v1", "--ignored", "--untracked-files=all"):
        fail("checkout-dirty")
    git.run(repo, "fsck", "--full", "--no-reflogs")
    tree = git.text(repo, "rev-parse", "--verify", commit + "^{tree}")
    docs_tree = git.text(repo, "rev-parse", "--verify", commit + ":" + d["docs_root"]) if d["docs_root"] else None
    if not HEX40.fullmatch(tree) or (docs_tree and
        (not HEX40.fullmatch(docs_tree) or git.text(repo, "cat-file", "-t", docs_tree) != "tree")):
        fail("tree-invalid")
    return {"resolved_commit": commit, "full_tree": tree, "docs_tree": docs_tree,
            "clean": True, "detached": True, "independent_objects": True,
            "inventory": _inventory(git, repo, commit, d)}


def _write(path: Path, value: dict[str, Any] | list[Any]) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _read(path: Path) -> Any:
    try:
        value = json.loads(path.read_bytes())
        if path.read_bytes() != canonical_json_bytes(value) + b"\n":
            fail("artifact-noncanonical")
        return value
    except (OSError, ValueError):
        fail("artifact-invalid")


def _manifest(receipt: dict[str, Any], final: Path) -> dict[str, Any]:
    identity = receipt["identity"]
    d = identity["descriptor"]
    return finalize_manifest({"schema_version": IMPORT_SCHEMA, "manifest_type": "corpus_import",
        "created_at": receipt["created_at"], **d,
        "source_descriptor": d, "source_descriptor_hash": identity["descriptor_hash"],
        "source_provenance": {k: identity[k] for k in ("resolved_commit", "full_tree", "docs_tree")},
        "snapshot_identity_hash": receipt["identity_hash"], "content_hash": identity["content_hash"],
        "file_inventory": identity["inventory"], "selected_files": identity["inventory"],
        "selected_file_count": len(identity["inventory"]),
        "documents_path": str(final / "documents"), "materialized_source_path": str(final / "source"),
        "snapshot_workspace": str(final), "acquisition_receipt": str(final / RECEIPT),
        "acquisition_receipt_hash": receipt["manifest_hash"], "stage_status": "ok"})


@dataclass(frozen=True)
class AcquisitionResult:
    root: Path
    receipt: dict[str, Any]
    reused: bool

    def import_result(self) -> dict[str, Any]:
        return {"manifest": _read(self.root / MANIFEST), "manifest_path": self.root / MANIFEST,
                "documents_path": self.root / "documents", "reused": self.reused,
                "receipt_path": self.root / RECEIPT, "receipt": self.receipt}


def verify_acquisition(root: Path, descriptor: CorpusSourceDescriptor, *, expected_commit: str,
                       expected_receipt_hash: str | None = None) -> dict[str, Any]:
    """Offline validation against caller-bound descriptor, commit and optional receipt."""
    try:
        return _verify(root, descriptor_payload(descriptor), expected_commit, expected_receipt_hash)
    except (OSError, ValueError, KeyError, TypeError):
        fail("artifact-invalid")


def _verify(root: Path, d: dict[str, Any], commit: str, expected_hash: str | None,
            binding_root: Path | None = None) -> dict[str, Any]:
    _safe_path(root)
    expected_files = {MARKER, RECEIPT, MANIFEST, "descriptor.json", "inventory.json", "source", "documents"}
    if {p.name for p in root.iterdir()} != expected_files:
        fail("publication-incomplete")
    for file in root.rglob("*"):
        _safe_path(file)
    receipt = _read(root / RECEIPT)
    if set(receipt) != {"schema_version", "policy_version", "created_at", "identity", "identity_hash",
                        "operational", "phase", "status", "failure_class", "manifest_hash"}:
        fail("receipt-fields-invalid")
    if receipt != finalize_manifest(receipt) or (expected_hash and receipt["manifest_hash"] != expected_hash):
        fail("receipt-hash-mismatch")
    if (receipt["schema_version"], receipt["policy_version"], receipt["phase"], receipt["status"], receipt["failure_class"]) != (
        SCHEMA, POLICY, "complete", "success", None):
        fail("receipt-terminal-invalid")
    identity = receipt["identity"]
    if set(identity) != {"descriptor", "descriptor_hash", "ref_interpretation", "resolved_commit", "full_tree",
        "docs_tree", "clean", "detached", "independent_objects", "inventory", "content_hash", "strategy",
        "reuse_disposition", "implementation", "artifacts"}:
        fail("receipt-identity-fields-invalid")
    if any(type(identity[key]) is not bool or identity[key] is not True for key in ("clean", "detached", "independent_objects")):
        fail("receipt-observation-invalid")
    if identity["descriptor"] != d or identity["descriptor_hash"] != sha256_json(d):
        fail("descriptor-mismatch")
    kind = ref_kind(d["requested_ref"])
    interpretation = identity["ref_interpretation"]
    refs = [d["requested_ref"]] if kind != "short" else ["refs/heads/" + d["requested_ref"], "refs/tags/" + d["requested_ref"]]
    if interpretation not in [{"kind": kind, "full_ref": ref} for ref in refs]:
        fail("ref-interpretation-mismatch")
    if identity["strategy"] != STRATEGY or identity["reuse_disposition"] != REUSE:
        fail("policy-mismatch")
    if identity["artifacts"] != {"source": "source", "documents": "documents", "descriptor": "descriptor.json",
                                "inventory": "inventory.json", "import_manifest": MANIFEST}:
        fail("artifact-reference-mismatch")
    impl = identity["implementation"]
    if set(impl) != {"commit", "tree"} or any(not isinstance(v, str) or not HEX40.fullmatch(v) for v in impl.values()):
        fail("implementation-identity-invalid")
    operational = receipt["operational"]
    if (set(operational) != {"root", "git_executable", "git_version"} or
        operational["root"] != str(binding_root or root) or
        not isinstance(operational["git_executable"], str) or
        not re.fullmatch(r"git version [0-9][A-Za-z0-9. ()-]*", operational["git_version"]) or
        not isinstance(receipt["created_at"], str)):
        fail("operational-fields-invalid")
    if receipt["identity_hash"] != sha256_json(identity):
        fail("identity-hash-mismatch")
    if _read(root / MARKER) != {"schema": SCHEMA, "descriptor_hash": sha256_json(d), "commit": commit}:
        fail("owner-mismatch")
    if _read(root / "descriptor.json") != d or _read(root / "inventory.json") != identity["inventory"]:
        fail("artifact-binding-mismatch")
    git = GitRuntime(_git_executable())
    observed = _observe(git, root / "source", commit, d)
    if any(identity[key] != val for key, val in observed.items()):
        fail("materialization-mismatch")
    if identity["content_hash"] != sha256_json({"files": observed["inventory"]}):
        fail("inventory-mismatch")
    docs = root / "documents"
    files = sorted(p.relative_to(docs).as_posix() for p in docs.rglob("*") if p.is_file())
    if files != [row["path"] for row in identity["inventory"]]:
        fail("documents-inventory-mismatch")
    for row in identity["inventory"]:
        if sha256_file(docs / row["path"]) != row["sha256"]:
            fail("documents-content-mismatch")
    if _read(root / MANIFEST) != _manifest(receipt, binding_root or root):
        fail("manifest-binding-mismatch")
    return receipt


def _git_executable() -> str:
    executable = shutil.which("git")
    if not executable:
        fail("git-unavailable")
    return str(Path(executable).resolve())


def _implementation(git: GitRuntime) -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    commit, tree = os.environ.get("RA_SOURCE_COMMIT"), os.environ.get("RA_SOURCE_TREE")
    if not commit or not tree:
        commit = git.text(root, "rev-parse", "HEAD")
        tree = git.text(root, "rev-parse", "HEAD^{tree}")
        if git.run(root, "status", "--porcelain", "--untracked-files=all"):
            fail("implementation-dirty")
    if not HEX40.fullmatch(commit) or not HEX40.fullmatch(tree):
        fail("implementation-identity-invalid")
    return {"commit": commit, "tree": tree}


def _cleanup(root: Path, parent: Path, marker: dict[str, Any]) -> None:
    _safe_path(root)
    if root.parent != parent or not root.name.startswith(".acquiring-") or _read(root / MARKER) != marker:
        fail("cleanup-owner-mismatch")
    def readonly(function: Any, path: str, _error: Any) -> None:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    shutil.rmtree(root, onerror=readonly)


def acquire_remote(descriptor: CorpusSourceDescriptor, *, capability: str = "disabled",
                   created_at: str | None = None) -> AcquisitionResult:
    """Only explicit host capability may transport. local-only admits file URLs."""
    if capability not in {"local-only", "host-network"}:
        fail("capability-disabled")
    if capability == "host-network" and os.environ.get("RA_NETWORK_DISABLED") == "1":
        fail("network-disabled")
    d = descriptor_payload(descriptor)
    network = d["source_url"].startswith("https:")
    if network and capability != "host-network":
        fail("network-disabled")
    git = GitRuntime(_git_executable(), network)
    implementation = _implementation(git)
    parent = _safe_path(descriptor.destination_workspace) / d["corpus_id"] / d["snapshot_id"]
    _safe_path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    stage = parent / (".acquiring-" + uuid.uuid4().hex)
    stage.mkdir()
    marker = {"schema": SCHEMA, "descriptor_hash": sha256_json(d), "commit": None}
    _write(stage / MARKER, marker)
    try:
        requested = d["requested_ref"]
        interpretation = ref_kind(requested)
        ref, commit = (requested, requested) if interpretation == "commit" else _advertisement(git, stage, d["source_url"], requested)
        marker["commit"] = commit
        _write(stage / MARKER, marker)
        final = parent / sha256_json({"descriptor": d, "commit": commit})
        if final.exists() or final.is_symlink():
            receipt = verify_acquisition(final, descriptor, expected_commit=commit)
            if interpretation != "commit" and _advertisement(git, stage, d["source_url"], requested) != (ref, commit):
                fail("remote-changed")
            return AcquisitionResult(final, receipt, True)
        repo = stage / "source"
        repo.mkdir()
        git.run(repo, "init", "--template=", "-b", "acquisition-unborn")
        git.run(repo, "config", "core.longpaths", "true")
        git.run(repo, "config", "core.autocrlf", "false")
        git.run(repo, "config", "remote.origin.url", d["source_url"])
        git.run(repo, "fetch", "--no-tags", "--no-recurse-submodules", "--", d["source_url"], commit)
        git.run(repo, "checkout", "--detach", commit, "--")
        observed = _observe(git, repo, commit, d)
        documents = stage / "documents"
        documents.mkdir()
        for row in observed["inventory"]:
            dest = documents / row["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / (d["docs_root"] or "") / row["path"], dest)
        _write(stage / "descriptor.json", d)
        _write(stage / "inventory.json", observed["inventory"])
        identity = {"descriptor": d, "descriptor_hash": sha256_json(d),
            "ref_interpretation": {"kind": interpretation, "full_ref": ref}, **observed,
            "content_hash": sha256_json({"files": observed["inventory"]}), "strategy": STRATEGY,
            "reuse_disposition": REUSE, "implementation": implementation,
            "artifacts": {"source": "source", "documents": "documents", "descriptor": "descriptor.json",
                          "inventory": "inventory.json", "import_manifest": MANIFEST}}
        version = git.text(stage, "--version")
        if not re.fullmatch(r"git version [0-9][A-Za-z0-9. ()-]*", version):
            fail("git-version-invalid")
        receipt = finalize_manifest({"schema_version": SCHEMA, "policy_version": POLICY,
            "created_at": created_at or utc_now_iso(), "identity": identity,
            "identity_hash": sha256_json(identity), "operational": {"root": str(final),
                "git_executable": git.executable, "git_version": version},
            "phase": "complete", "status": "success", "failure_class": None})
        _write(stage / RECEIPT, receipt)
        _write(stage / MANIFEST, _manifest(receipt, final))
        # Validate with final path bindings before the atomic directory rename.
        if _observe(git, repo, commit, d) != observed:
            fail("source-changed")
        if interpretation != "commit" and _advertisement(git, stage, d["source_url"], requested) != (ref, commit):
            fail("remote-changed")
        _verify(stage, d, commit, receipt["manifest_hash"], binding_root=final)
        _safe_path(parent)
        stage.rename(final)
        return AcquisitionResult(final, receipt, False)
    except (OSError, ValueError, subprocess.SubprocessError):
        fail("operation-failed")
    finally:
        if stage.exists():
            _cleanup(stage, parent, marker)
