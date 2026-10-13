from __future__ import annotations

import csv
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlparse

from ..hashing import sha256_file, sha256_json
from ..snapshots import corpus_doc_id


GRAPH_EXTRACTION_VERSION = "markdown-links-v1"
INLINE_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
REFERENCE_DEF_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
HTML_EXTENSIONS = {".html", ".htm"}


def write_markdown_link_graph(dataset_path: Path) -> dict[str, Any]:
    corpus_dir = dataset_path / "corpus"
    path_to_doc = _path_to_doc_id(corpus_dir)
    edges = sorted(_extract_edges(corpus_dir, path_to_doc))
    edge_path = dataset_path / "graph_edges.csv"
    write_edges_csv(edges, edge_path)
    metrics = graph_metrics(edges, sorted(set(path_to_doc.values())))
    metrics_path = dataset_path / "graph_metrics.json"
    metrics_path.write_text(_json(metrics), encoding="utf-8")
    return {
        "edge_file": "graph_edges.csv",
        "edge_file_hash": sha256_file(edge_path),
        "graph_metrics_file": "graph_metrics.json",
        "graph_metrics_file_hash": sha256_file(metrics_path),
        "graph_hash": sha256_json({"edges": [{"source": source, "target": target} for source, target in edges]}),
        "node_count": metrics["node_count"],
        "edge_count": metrics["edge_count"],
        "graph_metrics": metrics,
    }


def extract_markdown_link_edges(corpus_dir: Path, *, doc_id_for: Callable[[str], str] = corpus_doc_id,
                                extensions: Iterable[str] | None = None,
                                include: Callable[[str], bool] | None = None) -> list[tuple[str, str]]:
    """Return the sorted directed Markdown link edges of a corpus tree.

    The resolver is the one ``write_markdown_link_graph`` uses; ``doc_id_for``
    maps a corpus-relative POSIX path to the node id (S025: the candidate
    corpus is imported under ``cikm_flat_path_sha1_v1`` ids), and
    ``extensions`` restricts the node set (``[".md"]`` keeps every node a
    corpus document, so links to images or data files resolve to nothing).
    ``include`` (S047) is a predicate over the corpus-relative path: a path it
    rejects is not a node, so it neither sources an edge nor receives one.
    """
    path_to_doc = _path_to_doc_id(corpus_dir, doc_id_for=doc_id_for, extensions=extensions, include=include)
    return sorted(_extract_edges(corpus_dir, path_to_doc))


MARKDOWN_PROFILE = "markdown_relative_v1"
LINK_PROFILES = (MARKDOWN_PROFILE, "hugo_site_url_v1")


def extract_profile_link_edges(corpus_dir: Path, *, profiles: Iterable[str], params: dict[str, Any] | None = None,
                               doc_id_for: Callable[[str], str] = corpus_doc_id,
                               extensions: Iterable[str] | None = None,
                               include: Callable[[str], bool] | None = None,
                               ) -> tuple[list[tuple[str, str]], dict[str, dict[str, dict[str, int]]]]:
    """S057: the sorted union of the declared link profiles' edges, and per-profile, per-kind counts.

    ``markdown_relative_v1`` is ``extract_markdown_link_edges`` unchanged, so a request declaring only it
    (or nothing) gets today's graph byte for byte; ``hugo_site_url_v1`` is ``graph/hugo.py``. The node set
    (``extensions``, ``include``) is the same for every profile.
    """
    from .hugo import PROFILE as HUGO_PROFILE, hugo_edges

    path_to_doc = _path_to_doc_id(corpus_dir, doc_id_for=doc_id_for, extensions=extensions, include=include)
    edges: set[tuple[str, str]] = set()
    stats: dict[str, dict[str, dict[str, int]]] = {}
    for profile in profiles:
        if profile == MARKDOWN_PROFILE:
            edges |= _extract_edges(corpus_dir, path_to_doc)
            stats[profile] = {"markdown": _markdown_counts(corpus_dir, path_to_doc)}
        elif profile == HUGO_PROFILE:
            found, counts = hugo_edges(corpus_dir, path_to_doc, (params or {})[HUGO_PROFILE],
                                       lambda text: [*_inline_hrefs(text), *_reference_hrefs(text)])
            edges |= found
            stats[profile] = counts
        else:
            raise ValueError(f"unknown link profile: {profile}")
    return sorted(edges), stats


def _markdown_counts(corpus_dir: Path, path_to_doc: dict[str, str]) -> dict[str, int]:
    counts = {"found": 0, "resolved": 0, "external": 0, "unresolved": 0}
    for path_key in sorted(path_to_doc):
        text = (corpus_dir / path_key).read_text(encoding="utf-8", errors="replace")
        for href in [*_inline_hrefs(text), *_reference_hrefs(text)]:
            parsed = urlparse(href)
            if href.startswith("#"):
                continue
            counts["found"] += 1
            if parsed.scheme or parsed.netloc or href.startswith(("mailto:", "tel:")):
                counts["external"] += 1
            elif _resolve_link(href, Path(path_key).parent, path_to_doc):
                counts["resolved"] += 1
            else:
                counts["unresolved"] += 1
    return counts


def write_edges_csv(edges: Iterable[tuple[str, str]], edge_path: Path) -> None:
    with edge_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "target"])
        writer.writeheader()
        for source, target in edges:
            writer.writerow({"source": source, "target": target})


def graph_metrics(edges: list[tuple[str, str]], corpus_nodes: list[str]) -> dict[str, Any]:
    nodes = sorted(set(corpus_nodes) | {node for edge in edges for node in edge})
    weak = _weak_components(nodes, edges)
    strong = _strong_components(nodes, edges)
    out_degree: dict[str, int] = {node: 0 for node in nodes}
    in_degree: dict[str, int] = {node: 0 for node in nodes}
    for source, target in edges:
        out_degree[source] = out_degree.get(source, 0) + 1
        in_degree[target] = in_degree.get(target, 0) + 1
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "weak_components": [{"size": len(component), "nodes": component} for component in weak],
        "strong_components": [{"size": len(component), "nodes": component} for component in strong],
        "weak_component_count": len(weak),
        "strong_component_count": len(strong),
        "largest_component_size": max((len(component) for component in weak), default=0),
        "isolated_node_count": sum(1 for node in nodes if in_degree.get(node, 0) == 0 and out_degree.get(node, 0) == 0),
        "max_in_degree": max(in_degree.values(), default=0),
        "max_out_degree": max(out_degree.values(), default=0),
    }


def _extract_edges(corpus_dir: Path, path_to_doc: dict[str, str]) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for path_key, source_doc_id in sorted(path_to_doc.items()):
        source_path = corpus_dir / path_key
        text = source_path.read_text(encoding="utf-8", errors="replace")
        for href in [*_inline_hrefs(text), *_reference_hrefs(text)]:
            target = _resolve_link(href, Path(path_key).parent, path_to_doc)
            if target and target != source_doc_id:
                edges.add((source_doc_id, target))
    return edges


def _inline_hrefs(text: str) -> list[str]:
    return [match.group(1).strip("<>") for match in INLINE_LINK_RE.finditer(text)]


def _reference_hrefs(text: str) -> list[str]:
    return [match.group(1).strip("<>") for match in REFERENCE_DEF_RE.finditer(text)]


def _resolve_link(href: str, source_parent: Path, path_to_doc: dict[str, str]) -> str | None:
    parsed = urlparse(href)
    if parsed.scheme or parsed.netloc or href.startswith(("mailto:", "tel:", "#")):
        return None
    raw_path = unquote(parsed.path)
    if not raw_path:
        return None
    raw_path = raw_path.replace("{{ page.lang }}", "").replace("{{page.lang}}", "")
    raw_path = raw_path.replace("{{ site.lang }}", "").replace("{{site.lang}}", "")
    raw_path = raw_path.strip("/")
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not href.startswith("/"):
        candidate = source_parent / candidate
    normalized = _normalize_candidate(candidate.as_posix())
    for option in _candidate_paths(normalized):
        if option in path_to_doc:
            return path_to_doc[option]
    return None


def _candidate_paths(path: str) -> list[str]:
    base = path.strip("/")
    suffix = Path(base).suffix.lower()
    candidates = [base]
    if suffix in HTML_EXTENSIONS:
        candidates.append(str(Path(base).with_suffix(".md")).replace("\\", "/"))
    elif suffix == "":
        candidates.extend([f"{base}.md", f"{base}/index.md"])
    return list(dict.fromkeys(_normalize_candidate(candidate) for candidate in candidates if candidate))


def _normalize_candidate(path: str) -> str:
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _path_to_doc_id(corpus_dir: Path, *, doc_id_for: Callable[[str], str] = corpus_doc_id,
                    extensions: Iterable[str] | None = None,
                    include: Callable[[str], bool] | None = None) -> dict[str, str]:
    admitted = None if extensions is None else {extension.lower() for extension in extensions}
    return {
        path.relative_to(corpus_dir).as_posix(): doc_id_for(path.relative_to(corpus_dir).as_posix())
        for path in sorted(corpus_dir.rglob("*"))
        if path.is_file() and (admitted is None or path.suffix.lower() in admitted)
        and (include is None or include(path.relative_to(corpus_dir).as_posix()))
    }


def _weak_components(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    for source, target in edges:
        neighbors[source].add(target)
        neighbors[target].add(source)
    seen: set[str] = set()
    components: list[list[str]] = []
    for node in nodes:
        if node in seen:
            continue
        queue: deque[str] = deque([node])
        seen.add(node)
        component: list[str] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in sorted(neighbors[current]):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    return sorted(components, key=lambda component: (-len(component), component))


def _strong_components(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    graph: dict[str, list[str]] = {node: [] for node in nodes}
    reverse: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        graph.setdefault(source, []).append(target)
        reverse.setdefault(target, []).append(source)
    visited: set[str] = set()
    order: list[str] = []

    def visit(node: str) -> None:
        visited.add(node)
        for neighbor in sorted(graph.get(node, [])):
            if neighbor not in visited:
                visit(neighbor)
        order.append(node)

    for node in nodes:
        if node not in visited:
            visit(node)
    components: list[list[str]] = []
    visited.clear()

    def assign(node: str, component: list[str]) -> None:
        visited.add(node)
        component.append(node)
        for neighbor in sorted(reverse.get(node, [])):
            if neighbor not in visited:
                assign(neighbor, component)

    for node in reversed(order):
        if node in visited:
            continue
        component: list[str] = []
        assign(node, component)
        components.append(sorted(component))
    return sorted(components, key=lambda component: (-len(component), component))


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, indent=2, sort_keys=True) + "\n"
