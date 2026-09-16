"""Closed candidate-bound prediction production for ranked-v2 evaluation."""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
import math
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .graph.links import extract_markdown_link_edges, write_edges_csv

CONFIG_SCHEMA = "retrieval_arena.candidate_retrieval_config.v1"
PREDICTION_SCHEMA = "retrieval_arena.ranked_prediction.v2"
PROVIDER_SCHEMA = "retrieval_arena.lexical_provider_output.v1"
PROVIDER_NAME = "rag_lexical_topk"
ANSWER_POLICIES = ("head_of_document_v1", "best_passage_v1", "agent_answer_v1")
DEFAULT_ANSWER_POLICY = "head_of_document_v1"
AGENT_ANSWER_POLICY = "agent_answer_v1"
PROVIDER_ROW_FIELDS = {"question_id", "question", "generated_answer", "retrieved_context"}
# S026: the navigator's provider row carries its answer policy and the cited pages.
NAVIGATOR_ROW_FIELDS = PROVIDER_ROW_FIELDS | {"answer_policy", "cited_pages"}
NAVIGATOR_OUTPUT_FILES = {"metadata.json", "predictions.jsonl", "navigation.json"}
TRACES_DIR = "traces"
TRACE_SCHEMA = "e007b.action_trace.v1"
TRACE_POLICY = "bounded_provider_action_controller_v1"
# S046: the repo-search agent's trace (a superset of e007b: search lines,
# opens attributed to a route, no start page -- the reader lands by a search).
SEARCH_TRACE_SCHEMA = "e007c.action_trace.v1"
SEARCH_TRACE_POLICY = "bounded_repo_search_controller_v1"
SEARCH_ROUTES = ("landing", "search", "link")
SEARCH_PROVIDER_FIELDS = frozenset({"model_policy", "search_budget", "search_hit_files", "search_hit_lines_per_file",
                                    "page_visit_budget", "model_call_budget", "page_char_limit", "follow_links"})
TRACE_TERMINALS = ("answer", "budget_exhausted", "invalid_action", "provider_error")
NETWORK_NONE = "none"
NETWORK_HOST_MODEL_ALIAS = "host_model_alias"
MODEL_ALIAS_ENDPOINT = "http://host.docker.internal:11434"
MODEL_POLICY_FIELDS = {"backend", "model", "digest", "temperature", "top_p", "seed", "context_window_tokens",
                       "max_output_tokens", "timeout_seconds", "max_attempts", "endpoint", "thinking"}
PROVIDER_ITEM_FIELDS = {"doc_id", "text", "score", "source", "rank"}
PROVENANCE_FIELDS = {"doc_id", "section_heading", "passage_index", "char_start", "char_end"}
BASE_PROVIDER_FIELDS = frozenset({"name", "image", "platform", "network", "top_k", "max_context_chars_per_doc"})
GRAPH_EDGES_FILE = "graph_edges.csv"
# S044: the corpus import policy is declared per request; the lowercase rule
# is the one the support ids were produced under.
IMPORT_POLICY_AS_WRITTEN = "cikm_flat_path_sha1_v1"
IMPORT_POLICY_LOWER = "cikm_flat_path_sha1_lower_v1"
IMPORT_POLICIES = (IMPORT_POLICY_AS_WRITTEN, IMPORT_POLICY_LOWER)
# S047: a request may declare a corpus exclusion policy; a page the policy
# names is neither imported nor a graph node, and the receipt records the
# policy id and the excluded ids. ``cikm_faq_pattern_v1`` is the CIKM
# exporter's FAQ rule (Capstone ``configs/pinned_cikm_exporter.yaml``,
# ``retrieval/pinned_cikm.py::discover_pages``): the lowercased
# corpus-relative path matches ``*faq*`` or carries ``faq`` in any path part.
# Every request whose experiment id names a direct-lane campaign
# (``E-EXP…``) must declare it; the rule reads paths, never content.
EXCLUDE_POLICY_FAQ = "cikm_faq_pattern_v1"
EXCLUDE_POLICIES = (EXCLUDE_POLICY_FAQ,)
EXCLUDE_REQUIRED_PREFIX = "E-EXP"
GRAPH_SELECTION_ROLES = ("seed", "ranked_neighbor")


@dataclass(frozen=True)
class ProviderProfile:
    """One config-declared provider the endpoint admits (S025).

    ``config_fields`` are the provider-specific knobs the candidate-retrieval
    config must carry (exactly, beyond the base fields and ``answer_policy``);
    ``item_fields`` the context-item keys beyond the common row shape;
    ``requires_graph`` whether prepare extracts the candidate's link graph.
    """

    name: str
    image_repository: str
    item_source: str
    requires_graph: bool
    config_fields: frozenset[str]
    item_fields: frozenset[str]
    # S026: the provider's admissible answer policies, its container network
    # (``none`` for every retriever; ``host_model_alias`` only for the
    # navigator, which reaches the host model alias and nothing else), and
    # whether it publishes per-query action traces beside the predictions.
    answer_policies: tuple[str, ...] = ("head_of_document_v1", "best_passage_v1")
    network: str = NETWORK_NONE
    publishes_traces: bool = False
    # S046: the trace schema and controller policy a trace-publishing provider
    # writes, and, for the repo-search agent, the variant its name fixes.
    trace_schema: str = TRACE_SCHEMA
    trace_policy: str = TRACE_POLICY
    follow_links: bool | None = None

    @property
    def navigator(self) -> bool:
        return self.publishes_traces

    @property
    def repo_search(self) -> bool:
        return self.trace_schema == SEARCH_TRACE_SCHEMA

    @property
    def image_pattern(self) -> str:
        return rf"{re.escape(self.image_repository)}@sha256:[0-9a-f]{{64}}"


PROVIDERS: dict[str, ProviderProfile] = {
    "rag_lexical_topk": ProviderProfile(
        "rag_lexical_topk", "retrieval-arena/rag-lexical-topk", "lexical_topk", False,
        frozenset(), frozenset()),
    # BM25 seeds plus a ranked one-hop neighbour budget; the context budget
    # ``top_k`` is split as ``seed_top_k + neighbor_budget``.
    "rag_graph_ranked_expand": ProviderProfile(
        "rag_graph_ranked_expand", "retrieval-arena/rag-graph-ranked-expand", "graph_ranked_expand", True,
        frozenset({"graph_expansion_hops", "seed_top_k", "neighbor_budget"}), frozenset({"selection_role"})),
    # S026: the bounded link-following navigator (real local model through
    # the host model alias); cited pages are its context items, its answer
    # text is the agent's, and one action trace per query is published.
    "rag_llm_guided_search": ProviderProfile(
        "rag_llm_guided_search", "retrieval-arena/rag-llm-guided-search", "llm_guided_navigation", True,
        frozenset({"model_policy", "page_visit_budget", "model_call_budget", "page_char_limit"}), frozenset(),
        answer_policies=(AGENT_ANSWER_POLICY,), network=NETWORK_HOST_MODEL_ALIAS, publishes_traces=True),
    # S046: the repo-search agent reader in its two variants (grep + open +
    # follow links; grep + open only), one image, the variant fixed by the
    # provider name and its ``follow_links`` value; same reach and isolation
    # as the navigator, one e007c trace per query.
    "rag_repo_search_agent": ProviderProfile(
        "rag_repo_search_agent", "retrieval-arena/rag-repo-search-agent", "repo_search_navigation", True,
        SEARCH_PROVIDER_FIELDS, frozenset(),
        answer_policies=(AGENT_ANSWER_POLICY,), network=NETWORK_HOST_MODEL_ALIAS, publishes_traces=True,
        trace_schema=SEARCH_TRACE_SCHEMA, trace_policy=SEARCH_TRACE_POLICY, follow_links=True),
    "rag_repo_search_agent_nolinks": ProviderProfile(
        "rag_repo_search_agent_nolinks", "retrieval-arena/rag-repo-search-agent", "repo_search_navigation", True,
        SEARCH_PROVIDER_FIELDS, frozenset(),
        answer_policies=(AGENT_ANSWER_POLICY,), network=NETWORK_HOST_MODEL_ALIAS, publishes_traces=True,
        trace_schema=SEARCH_TRACE_SCHEMA, trace_policy=SEARCH_TRACE_POLICY, follow_links=False),
}
PROVIDER_IMAGE_PATTERN = re.compile(r"retrieval-arena/[a-z0-9-]+@sha256:[0-9a-f]{64}")


def provider_profile(name: Any) -> ProviderProfile:
    if not isinstance(name, str) or name not in PROVIDERS:
        raise ValidationError("candidate-retrieval-provider-undeclared")
    return PROVIDERS[name]


def _positive_int(value: Any, *, minimum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= minimum


def imported_document_name(relative_text: str, import_policy: str = IMPORT_POLICY_AS_WRITTEN) -> tuple[str, str]:
    """Return (imported file name, doc id) under one declared import policy.

    ``cikm_flat_path_sha1_v1`` hashes the corpus-relative path as written;
    ``cikm_flat_path_sha1_lower_v1`` (S044) hashes it lowercased, which is the
    rule of the CIKM dataset importer (``stable_doc_id``) that produced the
    support ids every ranked metric joins against. Under the lowercase rule two
    paths differing only by letter case share one id; ``prepare_provider_input``
    refuses such a corpus (``candidate-retrieval-document-id-collision``) rather
    than merging the documents.
    """
    if import_policy not in IMPORT_POLICIES:
        raise ValidationError("candidate-retrieval-import-policy")
    hashed = relative_text.lower() if import_policy == IMPORT_POLICY_LOWER else relative_text
    relative = Path(hashed)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", relative.with_suffix("").as_posix()).strip("_").lower()
    suffix = hashlib.sha1(hashed.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    imported_name = f"{stem}__{suffix}.md"
    return imported_name, Path(imported_name).with_suffix("").as_posix()


def excluded_by_policy(relative_text: str, exclude_policy: str | None) -> bool:
    """Whether one corpus-relative path is excluded under the declared policy (S047)."""
    if exclude_policy is None:
        return False
    if exclude_policy not in EXCLUDE_POLICIES:
        raise ValidationError("candidate-retrieval-exclude-policy")
    lowered = relative_text.lower()
    return fnmatch.fnmatch(lowered, "*faq*") or any("faq" in part for part in lowered.split("/"))


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def regular_inventory(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir() or root.is_symlink():
        raise ValidationError("candidate-retrieval-root-invalid")
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValidationError("candidate-retrieval-symlink-rejected")
        if path.is_dir():
            continue
        if not stat.S_ISREG(mode):
            raise ValidationError("candidate-retrieval-nonregular-rejected")
        rows.append({"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path),
                     "size_bytes": path.stat().st_size})
    return rows


def _exact(value: Any, fields: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError(code)
    return value


def _relative(value: Any, code: str) -> str:
    if (not isinstance(value, str) or not value or Path(value).is_absolute()
            or "\\" in value or any(part in {"", ".", ".."} for part in Path(value).parts)):
        raise ValidationError(code)
    return value


def validate_config(value: Any) -> dict[str, Any]:
    config = _exact(value, {
        "schema_version", "experiment_id", "execution_id", "candidate", "corpus",
        "queries_path", "provider", "ranked", "input_inventory",
        "input_inventory_sha256", "output_identity",
    }, "candidate-retrieval-config-fields")
    if config["schema_version"] != CONFIG_SCHEMA:
        raise ValidationError("candidate-retrieval-config-schema")
    for name in ("experiment_id", "execution_id", "output_identity"):
        if not isinstance(config[name], str) or not config[name]:
            raise ValidationError(f"candidate-retrieval-{name}")
    candidate = _exact(config["candidate"], {"commit", "tree"}, "candidate-retrieval-candidate-fields")
    if any(not isinstance(candidate[name], str) or len(candidate[name]) != 40
           or any(ch not in "0123456789abcdef" for ch in candidate[name])
           for name in ("commit", "tree")):
        raise ValidationError("candidate-retrieval-candidate-identity")
    corpus_fields = {"root", "extensions", "import_policy"}
    if not isinstance(config["corpus"], dict) or set(config["corpus"]) not in (corpus_fields, corpus_fields | {"exclude_policy"}):
        raise ValidationError("candidate-retrieval-corpus-fields")
    corpus = config["corpus"]
    _relative(corpus["root"], "candidate-retrieval-corpus-root")
    if corpus["extensions"] != [".md"] or corpus["import_policy"] not in IMPORT_POLICIES:
        raise ValidationError("candidate-retrieval-import-policy")
    # S047: the exclusion policy is a closed id; a direct-lane request without it is refused.
    if "exclude_policy" in corpus and corpus["exclude_policy"] not in EXCLUDE_POLICIES:
        raise ValidationError("candidate-retrieval-exclude-policy")
    if "exclude_policy" not in corpus and config["experiment_id"].startswith(EXCLUDE_REQUIRED_PREFIX):
        raise ValidationError("candidate-retrieval-exclude-policy-required")
    _relative(config["queries_path"], "candidate-retrieval-queries-path")
    if not isinstance(config["provider"], dict):
        raise ValidationError("candidate-retrieval-provider-fields")
    profile = provider_profile(config["provider"].get("name"))
    provider_fields = BASE_PROVIDER_FIELDS | profile.config_fields
    if set(config["provider"]) not in (provider_fields, provider_fields | {"answer_policy"}):
        raise ValidationError("candidate-retrieval-provider-fields")
    # Legacy requests carry no answer_policy; the effective policy is always recorded.
    provider = {**config["provider"],
                "answer_policy": config["provider"].get("answer_policy", DEFAULT_ANSWER_POLICY)}
    config = {**config, "provider": provider}
    if (provider["platform"] != "linux/amd64" or provider["network"] != profile.network
            or not isinstance(provider["image"], str)
            or re.fullmatch(profile.image_pattern, provider["image"]) is None
            or not _positive_int(provider["top_k"], minimum=1)
            or not _positive_int(provider["max_context_chars_per_doc"], minimum=0)
            or provider["answer_policy"] not in profile.answer_policies):
        raise ValidationError("candidate-retrieval-provider-policy")
    if profile.navigator:
        _validate_navigator_policy(provider)
        if profile.repo_search:
            _validate_repo_search_policy(provider, profile)
    elif profile.requires_graph and (
            provider["graph_expansion_hops"] != 1
            or not _positive_int(provider["seed_top_k"], minimum=1)
            or not _positive_int(provider["neighbor_budget"], minimum=0)
            or provider["seed_top_k"] + provider["neighbor_budget"] != provider["top_k"]):
        raise ValidationError("candidate-retrieval-provider-graph-policy")
    ranked = _exact(config["ranked"], {"schema_version", "context_item_budget_k"},
                    "candidate-retrieval-ranked-fields")
    if (ranked["schema_version"] != PREDICTION_SCHEMA
            or ranked["context_item_budget_k"] != provider["top_k"]):
        raise ValidationError("candidate-retrieval-ranked-policy")
    inventory = config["input_inventory"]
    if (not isinstance(inventory, list) or inventory != sorted(inventory, key=lambda row: row.get("path", ""))
            or any(not isinstance(row, dict) or set(row) != {"path", "sha256", "size_bytes"}
                   for row in inventory)
            or config["input_inventory_sha256"] != sha256_bytes(canonical(inventory))):
        raise ValidationError("candidate-retrieval-inventory")
    return config


def _validate_navigator_policy(provider: dict[str, Any]) -> None:
    """S026: budgets are positive; the model policy is closed and names the host alias."""
    policy = provider["model_policy"]
    if (not _positive_int(provider["page_visit_budget"], minimum=1)
            or not _positive_int(provider["model_call_budget"], minimum=1)
            or not _positive_int(provider["page_char_limit"], minimum=1)
            or not isinstance(policy, dict) or set(policy) != MODEL_POLICY_FIELDS
            or policy["backend"] != "ollama" or not isinstance(policy["model"], str) or not policy["model"]
            or not isinstance(policy["digest"], str) or re.fullmatch(r"sha256:[0-9a-f]{64}", policy["digest"]) is None
            or any(isinstance(policy[name], bool) or not isinstance(policy[name], (int, float))
                   or not math.isfinite(policy[name]) for name in ("temperature", "top_p", "timeout_seconds"))
            or any(not _positive_int(policy[name], minimum=1)
                   for name in ("context_window_tokens", "max_output_tokens", "max_attempts"))
            or isinstance(policy["seed"], bool) or not isinstance(policy["seed"], int)
            or policy["max_attempts"] != 1 or policy["endpoint"] != MODEL_ALIAS_ENDPOINT
            or not isinstance(policy["thinking"], bool)):
        raise ValidationError("candidate-retrieval-provider-model-policy")


def _validate_repo_search_policy(provider: dict[str, Any], profile: ProviderProfile) -> None:
    """S046: the search budgets are positive and the variant matches the provider's name."""
    if (not _positive_int(provider["search_budget"], minimum=1)
            or not _positive_int(provider["search_hit_files"], minimum=1)
            or not _positive_int(provider["search_hit_lines_per_file"], minimum=1)
            or not isinstance(provider["follow_links"], bool)
            or provider["follow_links"] != profile.follow_links):
        raise ValidationError("candidate-retrieval-provider-search-policy")


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(["/usr/bin/git", "-c", f"safe.directory={repo}", "-C", str(repo), *arguments], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                                 "TZ": "UTC", "GIT_CONFIG_NOSYSTEM": "1",
                                 "GIT_CONFIG_GLOBAL": "/dev/null"}, check=False)
    if result.returncode:
        raise ValidationError("candidate-retrieval-git-failure")
    return result.stdout.strip()


def prepare_provider_input(config: dict[str, Any], input_root: Path, candidate_root: Path,
                           provider_input: Path) -> dict[str, Any]:
    config = validate_config(config)
    payload_inventory = [row for row in regular_inventory(input_root)
                         if row["path"] not in {"config.json", "operator-request.json"}]
    if payload_inventory != config["input_inventory"]:
        raise ValidationError("candidate-retrieval-input-inventory-mismatch")
    if (_git(candidate_root, "rev-parse", "HEAD") != config["candidate"]["commit"]
            or _git(candidate_root, "rev-parse", "HEAD^{tree}") != config["candidate"]["tree"]
            or _git(candidate_root, "status", "--porcelain", "--untracked-files=all")):
        raise ValidationError("candidate-retrieval-candidate-admission")
    corpus_root = candidate_root / config["corpus"]["root"]
    import_policy = config["corpus"]["import_policy"]
    exclude_policy = config["corpus"].get("exclude_policy")
    if not corpus_root.is_dir() or corpus_root.is_symlink():
        raise ValidationError("candidate-retrieval-corpus-root")
    queries = input_root / config["queries_path"]
    if not queries.is_file() or queries.is_symlink():
        raise ValidationError("candidate-retrieval-queries-missing")
    if provider_input.exists() or not provider_input.parent.is_dir():
        raise ValidationError("candidate-retrieval-provider-input-root")
    provider_input.mkdir()
    (provider_input / "corpus").mkdir()
    document_rows = []
    excluded_rows = []
    seen_imported: set[str] = set()
    for source in sorted(corpus_root.rglob("*.md")):
        if source.is_symlink() or not source.is_file():
            raise ValidationError("candidate-retrieval-corpus-file")
        relative_text = source.relative_to(corpus_root).as_posix()
        imported_name, doc_id = imported_document_name(relative_text, import_policy)
        if imported_name in seen_imported:
            raise ValidationError("candidate-retrieval-document-id-collision")
        seen_imported.add(imported_name)
        if excluded_by_policy(relative_text, exclude_policy):
            # S047: an excluded page is inventoried (so the receipt can name
            # it) but never copied into the provider input.
            excluded_rows.append({"path": relative_text, "imported_path": imported_name, "doc_id": doc_id,
                                  "sha256": sha256_file(source), "size_bytes": source.stat().st_size,
                                  "excluded": True})
            continue
        destination = provider_input / "corpus" / imported_name
        destination.write_bytes(source.read_bytes())
        text = source.read_text(encoding="utf-8")
        lines = [line.strip("# ").strip() for line in text.splitlines()
                 if line.strip() and not line.startswith("#")]
        snippet = " ".join(lines)
        limit = config["provider"]["max_context_chars_per_doc"]
        if limit > 0 and len(snippet) > limit:
            snippet = snippet[:limit].rsplit(" ", 1)[0].strip()
        document_rows.append({"path": relative_text, "imported_path": imported_name,
                              "doc_id": doc_id,
                              "sha256": sha256_file(source), "size_bytes": source.stat().st_size,
                              "expected_text": snippet})
    if not document_rows:
        raise ValidationError("candidate-retrieval-corpus-empty")
    excluded_ids = sorted(row["doc_id"] for row in excluded_rows)
    (provider_input / "questions.jsonl").write_bytes(queries.read_bytes())
    profile = provider_profile(config["provider"]["name"])
    provider_config = {"top_k": config["provider"]["top_k"],
                       "max_context_chars_per_doc": config["provider"]["max_context_chars_per_doc"],
                       "answer_policy": config["provider"]["answer_policy"],
                       **{name: config["provider"][name] for name in sorted(profile.config_fields)}}
    (provider_input / "config.yaml").write_bytes(canonical(provider_config) + b"\n")
    graph_edges_sha256 = None
    if profile.requires_graph:
        # Per-candidate link graph: the resolver of write_markdown_link_graph
        # over the candidate's own docs tree, nodes = imported .md documents.
        # S047: an excluded page is not a node, so a link to it resolves to nothing.
        edges = extract_markdown_link_edges(
            corpus_root, doc_id_for=lambda relative: imported_document_name(relative, import_policy)[1],
            extensions=config["corpus"]["extensions"],
            include=lambda relative: not excluded_by_policy(relative, exclude_policy))
        write_edges_csv(edges, provider_input / GRAPH_EDGES_FILE)
        graph_edges_sha256 = sha256_file(provider_input / GRAPH_EDGES_FILE)
    inventory_rows = document_rows + excluded_rows
    (provider_input.parent / "documents.json").write_bytes(canonical(inventory_rows) + b"\n")
    return {"schema_version": "retrieval_arena.candidate_retrieval_launch.v1",
            "provider_name": profile.name,
            "provider_image": config["provider"]["image"], "platform": "linux/amd64",
            "network": profile.network, "candidate_commit": config["candidate"]["commit"],
            "candidate_tree": config["candidate"]["tree"],
            "import_policy": import_policy,
            "corpus_exclude_policy": exclude_policy,
            "excluded_document_count": len(excluded_ids),
            "excluded_document_ids": excluded_ids,
            "graph_edges_sha256": graph_edges_sha256,
            "document_inventory_sha256": sha256_bytes(canonical(inventory_rows)),
            "provider_input_inventory": regular_inventory(provider_input),
            "provider_input_inventory_sha256": sha256_bytes(canonical(regular_inventory(provider_input)))}


def expected_provider_metadata(provider: dict[str, Any]) -> dict[str, Any]:
    """The exact ``metadata.json`` a declared provider must publish."""
    profile = provider_profile(provider["name"])
    common = {"name": profile.name, "deterministic": True, "top_k": provider["top_k"],
              "max_context_chars_per_doc": provider["max_context_chars_per_doc"],
              "answer_policy": provider["answer_policy"]}
    if profile.repo_search:
        return {**common, "deterministic": False,
                **{name: provider[name] for name in ("search_budget", "search_hit_files", "search_hit_lines_per_file",
                                                     "page_visit_budget", "model_call_budget", "page_char_limit",
                                                     "follow_links", "model_policy")},
                "trace_schema_version": SEARCH_TRACE_SCHEMA, "provider_policy": SEARCH_TRACE_POLICY,
                "prompt_version": "e007d_repo_search_prompt_v1", "landing": "search_question_text"}
    if profile.navigator:
        return {**common, "deterministic": False,
                "page_visit_budget": provider["page_visit_budget"],
                "model_call_budget": provider["model_call_budget"],
                "page_char_limit": provider["page_char_limit"], "model_policy": provider["model_policy"],
                "trace_schema_version": TRACE_SCHEMA, "provider_policy": TRACE_POLICY,
                "prompt_version": "e007c_navigator_prompt_v1", "start_document": "index.md"}
    if profile.requires_graph:
        return {**common, "seed_ranker": "bm25", "neighbor_ranker": "bm25",
                "graph_expansion_hops": provider["graph_expansion_hops"],
                "seed_top_k": provider["seed_top_k"], "neighbor_budget": provider["neighbor_budget"]}
    return {**common, "ranker": "bm25"}


def _read_graph(path: Path) -> dict[str, set[str]]:
    """Undirected adjacency of the published edge file (the provider expands both ways)."""
    if not path.is_file() or path.is_symlink():
        raise ValidationError("candidate-retrieval-graph-missing")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "source,target":
        raise ValidationError("candidate-retrieval-graph-header")
    adjacency: dict[str, set[str]] = {}
    for line in lines[1:]:
        source, separator, target = line.partition(",")
        if not separator or not source or not target:
            raise ValidationError("candidate-retrieval-graph-row")
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
    return adjacency


def _score_sorted(items: list[dict[str, Any]]) -> bool:
    return items == sorted(items, key=lambda item: (-item["score"], item["support_target_id"]))


def _check_order(profile: ProviderProfile, provider: dict[str, Any], items: list[dict[str, Any]],
                 roles: list[str], graph: dict[str, set[str]] | None) -> None:
    if not profile.requires_graph or profile.navigator:
        # Retrievers rank by score; the navigator's cited pages carry a
        # descending citation-order score, so the same check applies.
        if not _score_sorted(items):
            raise ValidationError("candidate-retrieval-provider-order")
        return
    # Seeds (score order) precede ranked neighbours (score order); counts
    # respect the declared split; each neighbour is adjacent to a seed.
    seed_count = roles.count("seed")
    if (any(role not in GRAPH_SELECTION_ROLES for role in roles)
            or roles != ["seed"] * seed_count + ["ranked_neighbor"] * (len(roles) - seed_count)
            or seed_count > provider["seed_top_k"]
            or len(roles) - seed_count > provider["neighbor_budget"]
            or not _score_sorted(items[:seed_count]) or not _score_sorted(items[seed_count:])):
        raise ValidationError("candidate-retrieval-provider-order")
    if graph is not None:
        seeds = {item["support_target_id"] for item in items[:seed_count]}
        frontier: set[str] = set().union(*(graph.get(seed, set()) for seed in seeds)) - seeds
        if any(item["support_target_id"] not in frontier for item in items[seed_count:]):
            raise ValidationError("candidate-retrieval-provider-neighbor")


def _validate_provenance(value: Any, context_ids: set[str], by_id: dict[Any, dict[str, Any]],
                         provider_input: Path | None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValidationError("candidate-retrieval-provider-provenance")
    for entry in value:
        _exact(entry, PROVENANCE_FIELDS, "candidate-retrieval-provider-provenance")
        heading = entry["section_heading"]
        if (not isinstance(entry["doc_id"], str) or entry["doc_id"] not in context_ids
                or heading is not None and not isinstance(heading, str)
                or any(isinstance(entry[name], bool) or not isinstance(entry[name], int)
                       for name in ("passage_index", "char_start", "char_end"))
                or entry["passage_index"] < 0 or not 0 <= entry["char_start"] < entry["char_end"]):
            raise ValidationError("candidate-retrieval-provider-provenance")
        if provider_input is not None:
            imported = by_id[entry["doc_id"]].get("imported_path")
            document = provider_input / "corpus" / imported if isinstance(imported, str) else None
            if (document is None or not document.is_file() or document.is_symlink()
                    or entry["char_end"] > len(document.read_text(encoding="utf-8"))):
                raise ValidationError("candidate-retrieval-provider-provenance")
    return value


def project_ranked_predictions(config: dict[str, Any], provider_output: Path, input_root: Path,
                               documents_path: Path, provider_input: Path | None = None,
                               ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    config = validate_config(config)
    predictions_path = provider_output / "predictions.jsonl"
    metadata_path = provider_output / "metadata.json"
    provider = config["provider"]
    profile = provider_profile(provider["name"])
    expected_files = NAVIGATOR_OUTPUT_FILES | {TRACES_DIR} if profile.navigator else {"metadata.json", "predictions.jsonl"}
    if (set(path.name for path in provider_output.iterdir()) != expected_files
            or predictions_path.is_symlink() or metadata_path.is_symlink()
            or profile.navigator and (provider_output / TRACES_DIR).is_symlink()):
        raise ValidationError("candidate-retrieval-provider-output-shape")
    expected_metadata = expected_provider_metadata(provider)
    metadata = _exact(json.loads(metadata_path.read_bytes()), set(expected_metadata),
                      "candidate-retrieval-provider-metadata-fields")
    answer_policy = provider["answer_policy"]
    best_passage = answer_policy == "best_passage_v1"
    if metadata != expected_metadata:
        raise ValidationError("candidate-retrieval-provider-metadata")
    graph = (_read_graph(provider_input / GRAPH_EDGES_FILE)
             if profile.requires_graph and provider_input is not None else None)
    rows = [json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    questions = [json.loads(line) for line in (input_root / config["queries_path"]).read_text(
        encoding="utf-8").splitlines() if line.strip()]
    if (not questions or any(not isinstance(row, dict) or set(row) != {"question_id", "question"}
                             or not isinstance(row["question_id"], str) or not row["question_id"]
                             or not isinstance(row["question"], str) or not row["question"] for row in questions)
            or [row["question_id"] for row in questions] != list(dict.fromkeys(row["question_id"] for row in questions))):
        raise ValidationError("candidate-retrieval-questions")
    if (len(rows) != len(questions)
            or [(row.get("question_id"), row.get("question")) for row in rows]
            != [(row["question_id"], row["question"]) for row in questions]):
        raise ValidationError("candidate-retrieval-query-completeness")
    documents = json.loads(documents_path.read_bytes())
    if not isinstance(documents, list):
        raise ValidationError("candidate-retrieval-document-inventory")
    by_id = {row.get("doc_id"): row for row in documents if isinstance(row, dict)}
    if len(by_id) != len(documents) or None in by_id:
        raise ValidationError("candidate-retrieval-document-inventory")
    # S047: an excluded page is inventoried but never admitted as a context
    # item, a cited page, a visited page or a provenance source.
    by_id = {doc_id: row for doc_id, row in by_id.items() if not row.get("excluded")}
    result = []
    answers: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    row_fields = PROVIDER_ROW_FIELDS | {"answer_provenance"} if best_passage else PROVIDER_ROW_FIELDS
    traces: dict[str, dict[str, Any]] = {}
    if profile.navigator:
        row_fields = NAVIGATOR_ROW_FIELDS
        traces = (validate_search_traces if profile.repo_search else validate_traces)(
            provider_output / TRACES_DIR, [row["question_id"] for row in questions], set(by_id), provider)
    for row in rows:
        _exact(row, row_fields, "candidate-retrieval-provider-row-fields")
        query_id = row["question_id"]
        if (not isinstance(query_id, str) or not query_id or query_id in seen_queries
                or not isinstance(row["generated_answer"], str)):
            raise ValidationError("candidate-retrieval-query-identity")
        seen_queries.add(query_id)
        context = row["retrieved_context"]
        if not isinstance(context, list) or len(context) > provider["top_k"]:
            raise ValidationError("candidate-retrieval-provider-context")
        items = []
        seen_items: set[str] = set()
        roles: list[str] = []
        for source_rank, item in enumerate(context, 1):
            _exact(item, PROVIDER_ITEM_FIELDS | profile.item_fields,
                   "candidate-retrieval-provider-item-fields")
            score = item["score"]
            admitted = by_id.get(item.get("doc_id"))
            if (item["source"] != profile.item_source or item["rank"] != source_rank
                    or not isinstance(item["doc_id"], str) or not item["doc_id"]
                    or item["doc_id"] in seen_items or isinstance(score, bool)
                    or not isinstance(score, (int, float)) or not math.isfinite(score)
                    or admitted is None or item["text"] != admitted.get("expected_text")):
                raise ValidationError("candidate-retrieval-provider-item")
            seen_items.add(item["doc_id"])
            roles.append(item.get("selection_role", "seed"))
            items.append({"support_target_id": item["doc_id"], "rank": source_rank,
                          "score": float(score), "source_rank": source_rank,
                          "source_item_id": item["doc_id"],
                          "source_provenance_sha256": sha256_bytes(canonical(item))})
        _check_order(profile, provider, items, roles, graph)
        result.append({"schema_version": PREDICTION_SCHEMA, "query_id": query_id,
                       "context_item_budget_k": provider["top_k"], "max_rank": len(items),
                       "retrieval_exhausted": len(items) < provider["top_k"], "items": items,
                       "duplicate_collapse_count": 0, "duplicate_collapse_sha256": None})
        answer = {"query_id": query_id, "generated_answer": row["generated_answer"]}
        if best_passage:
            answer["answer_policy"] = answer_policy
            answer["answer_provenance"] = _validate_provenance(
                row["answer_provenance"], seen_items, by_id, provider_input)
        if profile.navigator:
            cited = row["cited_pages"]
            trace = traces[query_id]
            if (row["answer_policy"] != AGENT_ANSWER_POLICY or not isinstance(cited, list)
                    or any(not isinstance(c, str) or c not in by_id for c in cited)
                    or len(cited) != len(set(cited))
                    or [item["support_target_id"] for item in items] != cited[:provider["top_k"]]
                    or trace["answer"] != {"cited_pages": cited, "text": row["generated_answer"]}
                    or trace["final_context_doc_ids"] != cited[:provider["top_k"]]):
                raise ValidationError("candidate-retrieval-provider-navigation-row")
            answer["answer_policy"] = AGENT_ANSWER_POLICY
            answer["cited_pages"] = cited
        answers.append(answer)
    return result, answers


def validate_traces(traces_dir: Path, question_ids: list[str], doc_ids: set[str],
                    provider: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """S026: one trace per question, bounded by the declared budgets, over admitted pages.

    A trace file is a header line, one line per action, and a terminal line.
    Returns each trace's terminal record keyed by question id.
    """
    if not traces_dir.is_dir() or traces_dir.is_symlink():
        raise ValidationError("candidate-retrieval-provider-traces")
    names = {path.name for path in traces_dir.iterdir()}
    if names != {f"{query_id}.jsonl" for query_id in question_ids}:
        raise ValidationError("candidate-retrieval-provider-traces")
    terminals: dict[str, dict[str, Any]] = {}
    for query_id in question_ids:
        path = traces_dir / f"{query_id}.jsonl"
        if path.is_symlink() or not path.is_file():
            raise ValidationError("candidate-retrieval-provider-traces")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if (len(rows) < 3 or rows[0].get("record") != "header" or rows[-1].get("record") != "terminal"
                or rows[0].get("question_id") != query_id or rows[0].get("schema_version") != TRACE_SCHEMA
                or rows[0].get("policy") != TRACE_POLICY
                or any(row.get("record") != "action" for row in rows[1:-1])):
            raise ValidationError("candidate-retrieval-provider-trace-shape")
        terminal = rows[-1]
        actions = rows[1:-1]
        opens = [row for row in actions if row.get("action") == "open"]
        visits = terminal.get("visits")
        reason = terminal.get("terminal", {}).get("reason") if isinstance(terminal.get("terminal"), dict) else None
        answered = len(actions) > 1 and actions[-2].get("action") == "answer"
        if (actions[-1].get("action") != "stop"
                or any(row.get("action") not in {"open", "back", "answer", "stop"} for row in actions)
                or not isinstance(visits, list) or not visits or visits[0] != rows[0].get("start_doc_id")
                or any(v not in doc_ids for v in visits)
                or [row["details"]["doc_id"] for row in opens] != visits[1:]
                or terminal.get("opens") != len(opens) or len(opens) > provider["page_visit_budget"]
                or isinstance(terminal.get("model_calls"), bool) or not isinstance(terminal.get("model_calls"), int)
                or terminal["model_calls"] > provider["model_call_budget"]
                or reason not in TRACE_TERMINALS or (reason == "answer") != answered
                or not isinstance(terminal.get("answer"), dict)
                or any(c not in visits for c in terminal["answer"].get("cited_pages", []))):
            raise ValidationError("candidate-retrieval-provider-trace-budget")
        terminals[query_id] = terminal
    return terminals


def validate_search_traces(traces_dir: Path, question_ids: list[str], doc_ids: set[str],
                           provider: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """S046: one e007c trace per question, bounded by the three budgets, over admitted pages.

    The first action is the landing search (``landing: true``, the question as
    query); every search carries its query and the ids it returned; every open
    names its route (``landing``/``search``/``link``, a link only in the
    ``follow_links`` variant) and lands on an admitted page; the visits are
    exactly the opened pages in order; searches, opens and model calls stay
    within the declared budgets; a citation is a visited page.
    """
    if not traces_dir.is_dir() or traces_dir.is_symlink():
        raise ValidationError("candidate-retrieval-provider-traces")
    names = {path.name for path in traces_dir.iterdir()}
    if names != {f"{query_id}.jsonl" for query_id in question_ids}:
        raise ValidationError("candidate-retrieval-provider-traces")
    terminals: dict[str, dict[str, Any]] = {}
    for query_id in question_ids:
        path = traces_dir / f"{query_id}.jsonl"
        if path.is_symlink() or not path.is_file():
            raise ValidationError("candidate-retrieval-provider-traces")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if (len(rows) < 3 or rows[0].get("record") != "header" or rows[-1].get("record") != "terminal"
                or rows[0].get("question_id") != query_id or rows[0].get("schema_version") != SEARCH_TRACE_SCHEMA
                or rows[0].get("policy") != SEARCH_TRACE_POLICY
                or rows[0].get("follow_links") != provider["follow_links"]
                or not isinstance(rows[0].get("landing_query"), str)
                or any(row.get("record") != "action" for row in rows[1:-1])):
            raise ValidationError("candidate-retrieval-provider-trace-shape")
        terminal = rows[-1]
        actions = rows[1:-1]
        searches = [row for row in actions if row.get("action") == "search"]
        opens = [row for row in actions if row.get("action") == "open"]
        visits = terminal.get("visits")
        reason = terminal.get("terminal", {}).get("reason") if isinstance(terminal.get("terminal"), dict) else None
        answered = len(actions) > 1 and actions[-2].get("action") == "answer"
        details = [row.get("details") for row in actions]
        if (actions[-1].get("action") != "stop"
                or any(not isinstance(d, dict) for d in details)
                or any(row.get("action") not in {"search", "open", "back", "answer", "stop"} for row in actions)
                or not searches or actions[0].get("action") != "search" or actions[0]["details"].get("landing") is not True
                or actions[0]["details"].get("query") != rows[0]["landing_query"]
                or any(row["details"].get("landing") is not False for row in searches[1:])
                or any(not isinstance(row["details"].get("query"), str)
                       or not isinstance(row["details"].get("doc_ids"), list)
                       or any(d not in doc_ids for d in row["details"]["doc_ids"]) for row in searches)
                or [row["details"].get("search_index") for row in searches] != list(range(1, len(searches) + 1))
                or any(row["details"].get("route") not in SEARCH_ROUTES
                       or (row["details"]["route"] == "link" and not provider["follow_links"]) for row in opens)
                or not isinstance(visits, list) or any(v not in doc_ids for v in visits)
                or [row["details"].get("doc_id") for row in opens] != visits
                or terminal.get("opens") != len(opens) or len(opens) > provider["page_visit_budget"]
                or terminal.get("searches") != len(searches) or len(searches) > provider["search_budget"]
                or isinstance(terminal.get("model_calls"), bool) or not isinstance(terminal.get("model_calls"), int)
                or terminal["model_calls"] > provider["model_call_budget"]
                or reason not in TRACE_TERMINALS or (reason == "answer") != answered
                or not isinstance(terminal.get("answer"), dict)
                or any(c not in visits for c in terminal["answer"].get("cited_pages", []))):
            raise ValidationError("candidate-retrieval-provider-trace-budget")
        terminals[query_id] = terminal
    return terminals
