"""S055: the repository-grounded identifier lexicon (``repo_lexicon_v1``).

A lexicon is the set of technical identifiers a repository's own source declares or
names -- Go type and function names, string-literal keys such as annotation names
and flags, YAML and JSON keys -- harvested once per snapshot from the mounted
candidate outside the documentation corpus. ``measure-docstyle`` builds it when a
config carries a ``lexicon`` block and writes it beside the metrics; a later run
consumes it through ``lexicon_path``. The harvest is rule-based and deterministic:
no model, no network, every rule named in ``rule_counts``.

Why: generic NER (``en_core_web_sm``) sees none of the identifiers a maintainer's
fix names (E-EXP6, 2026-09-22); a lexicon answers "is this token a thing the
software actually has" without a model.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .candidate_retrieval import canonical, sha256_bytes
from .errors import ValidationError

LEXICON_SCHEMA = "retrieval_arena.repo_lexicon.v1"
LEXICON_POLICY = "repo_lexicon_v1"
LEXICON_FILE = "repo_lexicon.v1.json"
CATALOG_DIR = Path(__file__).resolve().parent / "docstyle_catalogs"
STOP_CATALOGS = {"english": "docstyle_english_stop_v1.json", "schema": "docstyle_schema_stop_v1.json"}
HARVEST_EXTENSIONS = (".go", ".py", ".ts", ".tsx", ".js", ".yaml", ".yml", ".json", ".proto", ".sh", ".toml",
                      ".ini", ".cfg")
HARVEST_NAME_SUFFIXES = (".env.example",)
EXCLUDED_SEGMENTS = ("vendor", "node_modules", "dist", ".git", "hack/testdata")
EXCLUDED_NAME_PATTERNS = ("*.lock", "*.min.*", "*.pb.go", "*.gen.go", "*_generated*", "*_test.go", "*.test.ts")
MAX_FILE_BYTES = 2 * 1024 * 1024
MIN_ENTRY_LENGTH = 3

# Counter 1 classes (b)-(h) as fullmatch patterns, in precedence order; shared with the identifier view.
IDENTIFIER_CLASSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("b_flag", re.compile(r"--[a-z0-9][a-z0-9-]*(=\S+)?")),
    ("c_dotted", re.compile(r"[A-Za-z_][\w-]*(\.[A-Za-z_][\w-]*)+")),
    ("d_path", re.compile(r"[\w.-]+(/[\w.-]+)+")),
    ("e_snake", re.compile(r"[A-Za-z0-9]+(_[A-Za-z0-9]+)+")),
    ("f_camel", re.compile(r"[A-Za-z]*[a-z][A-Z][A-Za-z0-9]*")),
    ("g_version", re.compile(r"v?\d+(\.\d+){1,3}")),
    ("h_caps", re.compile(r"[A-Z0-9_-]{3,}")),
)
_LETTERS_PER_SEGMENT = re.compile(r"[A-Za-z]")
_TOKEN_SPLIT = re.compile(r"\s+")
_LITERAL = re.compile(r'"([^"\\\n]{3,})"|\'([^\'\\\n]{3,})\'|`([^`\n]{3,})`')
_LITERAL_TOKEN = re.compile(r"[A-Za-z0-9_./-]+")
_GO_DECL = re.compile(r"^\s*(?:type|func)\s+(?:\(\w+\s+\*?\w+\)\s+)?([A-Z]\w+)")  # exported names only
_GO_SINGLE = re.compile(r"^(?:const|var)\s+([A-Z]\w+)")
_GO_BLOCK_OPEN = re.compile(r"^(?:const|var) \($")
_GO_BLOCK_CLOSE = re.compile(r"^\)$")
_GO_BLOCK_ASSIGN = re.compile(r"^\s*([A-Z]\w+)\s*=")
_YAML_KEY = re.compile(r"^\s*-?\s*([A-Za-z_][\w.-]{2,})\s*:")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def classify_identifier(token: str) -> str | None:
    """The Counter 1 class of a bare token outside code spans, or None. Precedence (b)..(h), fullmatch."""
    if not token:
        return None
    for name, pattern in IDENTIFIER_CLASSES:
        if not pattern.fullmatch(token):
            continue
        if name == "c_dotted" and any(len(_LETTERS_PER_SEGMENT.findall(segment)) < 2 for segment in token.split(".")):
            continue
        if name == "d_path" and (token.startswith("http") or not any(mark in token for mark in "._-")):
            continue
        if name == "g_version" and not (token.startswith("v") or token.count(".") >= 2):
            continue
        if name == "h_caps" and not (re.search(r"[A-Z]", token) and ("_" in token or re.search(r"\d", token))):
            continue
        return name
    return None


def load_stop_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict) or set(payload) != {"catalog_name", "catalog_version", "entries", "metadata"}:
        raise ValidationError("lexicon-stop-catalog-shape")
    entries = frozenset(item.casefold() for item in payload["entries"])
    if not entries:
        raise ValidationError("lexicon-stop-catalog-entries")
    from .candidate_retrieval import sha256_file
    return {"name": payload["catalog_name"], "version": payload["catalog_version"], "entries": entries,
            "sha256": sha256_file(path), "entry_count": len(entries)}


def load_stop_catalogs() -> dict[str, dict[str, Any]]:
    return {key: load_stop_catalog(CATALOG_DIR / name) for key, name in STOP_CATALOGS.items()}


def stopped(token: str, stops: dict[str, dict[str, Any]]) -> bool:
    folded = token.casefold()
    return any(folded in catalog["entries"] for catalog in stops.values())


def _harvestable(path: Path, source_root: Path, exclude_root: Path | None) -> bool:
    relative = path.relative_to(source_root).as_posix()
    if exclude_root is not None:
        try:
            path.relative_to(exclude_root)
            return False
        except ValueError:
            pass
    directories = "/" + "/".join(relative.split("/")[:-1]) + "/"
    if any(("/" + segment + "/") in directories for segment in EXCLUDED_SEGMENTS):
        return False
    if any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDED_NAME_PATTERNS):
        return False
    return path.suffix in HARVEST_EXTENSIONS or any(path.name.endswith(suffix) for suffix in HARVEST_NAME_SUFFIXES)


def _admit_entry(token: str, stops: dict[str, dict[str, Any]]) -> bool:
    return len(token) >= MIN_ENTRY_LENGTH and not token.isdigit() and not stopped(token, stops)


def harvest_text(text: str, suffix: str, stops: dict[str, dict[str, Any]]) -> tuple[Counter, Counter]:
    """Entries found in one file's text with their occurrence counts, and the per-rule counts."""
    found: Counter = Counter()
    rules: Counter = Counter()
    # r1: Counter 1 classes (b)-(h) anywhere in the text
    for raw in _TOKEN_SPLIT.split(text):
        token = raw.strip(".,;:!?()[]{}\"'*~<>|`")
        if classify_identifier(token) is not None and _admit_entry(token, stops):
            found[token] += 1
            rules["r1_identifier_class"] += 1
    # r2: Go declarations
    if suffix == ".go":
        in_block = False
        for line in text.splitlines():
            if in_block:
                if _GO_BLOCK_CLOSE.match(line):
                    in_block = False
                    continue
                match = _GO_BLOCK_ASSIGN.match(line)
                if match and _admit_entry(match.group(1), stops):
                    found[match.group(1)] += 1
                    rules["r2_go_declaration"] += 1
                continue
            if _GO_BLOCK_OPEN.match(line):
                in_block = True
                continue
            match = _GO_DECL.match(line) or _GO_SINGLE.match(line)
            if match and _admit_entry(match.group(1), stops):
                found[match.group(1)] += 1
                rules["r2_go_declaration"] += 1
    # r3: single-token string literals with a qualifying character
    for match in _LITERAL.finditer(text):
        literal = next(group for group in match.groups() if group is not None)
        if (_LITERAL_TOKEN.fullmatch(literal) and len(literal) >= MIN_ENTRY_LENGTH
                and (any(mark in literal for mark in "._-") or re.search(r"\d", literal)
                     or IDENTIFIER_CLASSES[4][1].fullmatch(literal))
                and _admit_entry(literal, stops)):
            found[literal] += 1
            rules["r3_string_literal"] += 1
    # r4: YAML mapping keys outside fences; JSON object keys
    if suffix in (".yaml", ".yml"):
        in_fence = False
        for line in text.splitlines():
            if _FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = _YAML_KEY.match(line)
            if match and _admit_entry(match.group(1), stops):
                found[match.group(1)] += 1
                rules["r4_mapping_key"] += 1
    return found, rules


def _json_keys(value: Any, found: Counter, rules: Counter, stops: dict[str, dict[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and _admit_entry(key, stops):
                found[key] += 1
                rules["r4_mapping_key"] += 1
            _json_keys(item, found, rules, stops)
    elif isinstance(value, list):
        for item in value:
            _json_keys(item, found, rules, stops)


def build_lexicon(source_root: Path, exclude_root: Path | None, candidate: dict[str, str],
                  stops: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The lexicon of one checkout: every admitted file harvested, entries sorted, hash over the entries."""
    stops = stops if stops is not None else load_stop_catalogs()
    if not source_root.is_dir():
        raise ValidationError("lexicon-source-root")
    occurrences: Counter = Counter()
    file_counts: Counter = Counter()
    rules: Counter = Counter()
    file_count = skipped = 0
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.is_symlink() or not _harvestable(path, source_root, exclude_root):
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            skipped += 1
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        found, file_rules = harvest_text(text, path.suffix, stops)
        if path.suffix == ".json":
            try:
                _json_keys(json.loads(text), found, file_rules, stops)
            except ValueError:
                skipped += 1
                rules["r4_invalid_json_skipped"] += 1
        file_count += 1
        rules.update(file_rules)
        for token, count in found.items():
            occurrences[token] += count
            file_counts[token] += 1
    entries = [{"token": token, "file_count": file_counts[token], "occurrences": occurrences[token]}
               for token in sorted(occurrences)]
    return {"schema_version": LEXICON_SCHEMA, "policy": LEXICON_POLICY, "commit": candidate["commit"],
            "tree": candidate["tree"], "file_count": file_count, "skipped_count": skipped,
            "rule_counts": dict(sorted(rules.items())), "entry_count": len(entries),
            "stop_catalogs": {key: {"name": catalog["name"], "version": catalog["version"],
                                    "sha256": catalog["sha256"], "entry_count": catalog["entry_count"]}
                              for key, catalog in stops.items()},
            "entries": entries, "lexicon_sha256": sha256_bytes(canonical(entries))}


def load_lexicon(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValidationError("lexicon-missing")
    payload = json.loads(path.read_bytes())
    required = {"schema_version", "policy", "commit", "tree", "file_count", "skipped_count", "rule_counts",
                "entry_count", "stop_catalogs", "entries", "lexicon_sha256"}
    if (not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != LEXICON_SCHEMA
            or payload["policy"] != LEXICON_POLICY or not isinstance(payload["entries"], list)
            or payload["lexicon_sha256"] != sha256_bytes(canonical(payload["entries"]))):
        raise ValidationError("lexicon-shape")
    return payload


def mention_index(lexicon: dict[str, Any], stops: dict[str, dict[str, Any]]) -> tuple[frozenset[str], dict[str, str]]:
    """Exact entries, and the casefolded index of entries of >= 6 characters that are not stop words."""
    exact = frozenset(row["token"] for row in lexicon["entries"])
    folded = {}
    for token in exact:
        if len(token) >= 6 and not stopped(token, stops):
            folded.setdefault(token.casefold(), token)
    return exact, folded
