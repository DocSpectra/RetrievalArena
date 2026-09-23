"""S057: the ``hugo_site_url_v1`` link profile -- Hugo site URLs resolved to corpus files.

Hugo sites (grafana's documentation) link by site URL, not by file path: ``ref:<name>`` aliases
resolved through the page's front-matter ``refs``, absolute site URLs under a product prefix, and
directory-style relative URLs under Hugo's page-URL semantics. This module maps each of those to a
corpus file, and never guesses: fragments are dropped; an external URL, ``mailto:``, a site URL
outside the declared prefixes (another product or version) is ``external``; a site path that maps
to no file and no alias is ``unresolved``.

Page URLs (content-relative, leading and trailing slash): ``a/b.md`` -> ``/a/b/``; ``a/_index.md``
and ``a/index.md`` -> ``/a/``. A resolved site path ``p`` maps to ``p.md``, ``p/_index.md``,
``p/index.md`` (first existing wins), else to the page whose front-matter ``aliases`` name it
(absolute aliases under a site prefix; relative aliases against the page's parent section URL).
``ref:`` takes the first ``refs[name]`` entry whose ``pattern`` is a prefix of the product URL prefix.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import yaml

PROFILE = "hugo_site_url_v1"
KINDS = ("ref", "site_absolute", "site_relative")
FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


def front_matter(text: str) -> dict[str, Any]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}
    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return value if isinstance(value, dict) else {}


def page_url(content_relative: str) -> str:
    """The Hugo page URL of a content-relative Markdown path (leading and trailing slash)."""
    path = content_relative[:-3] if content_relative.endswith(".md") else content_relative
    parent, _, name = path.rpartition("/")
    if name in ("_index", "index"):
        return "/" + (parent + "/" if parent else "")
    return "/" + path + "/"


def parent_section_url(content_relative: str) -> str:
    url = page_url(content_relative).rstrip("/")
    parent = url.rpartition("/")[0]
    return parent + "/" if parent else "/"


def _site_path(url_path: str, site_prefixes: list[str]) -> str | None:
    """The content-relative site path of an absolute site URL, or None when no declared prefix matches."""
    for prefix in site_prefixes:
        if url_path.startswith(prefix):
            return "/" + url_path[len(prefix):]
        if url_path + "/" == prefix:
            return "/"
    return None


def _normalize(url_path: str) -> str:
    joined = posixpath.normpath(url_path) if url_path not in ("", "/") else "/"
    if not joined.startswith("/"):
        joined = "/" + joined
    return joined.rstrip("/") + "/" if joined != "/" else "/"


class HugoResolver:
    def __init__(self, content_files: dict[str, str], params: dict[str, Any]) -> None:
        """``content_files``: content-relative path -> page text, for every admitted page under the content root."""
        self.product = params["product_url_prefix"]
        self.prefixes = list(params["site_prefixes"])
        self.files = content_files
        self.aliases: dict[str, str] = {}
        self.front: dict[str, dict[str, Any]] = {}
        for relative, text in sorted(content_files.items()):
            meta = front_matter(text)
            self.front[relative] = meta
            for alias in meta.get("aliases") or []:
                if not isinstance(alias, str) or not alias.strip():
                    continue
                alias = urlparse(alias.strip()).path
                if alias.startswith("/"):
                    site = _site_path(alias if alias.endswith("/") else alias + "/", self.prefixes)
                    if site is None:
                        continue
                else:
                    site = posixpath.join(parent_section_url(relative), alias)
                self.aliases.setdefault(_normalize(site), relative)

    def _file_for(self, site: str) -> str | None:
        site = _normalize(site)
        base = site.strip("/")
        options = [f"{base}.md", f"{base}/_index.md", f"{base}/index.md"] if base else ["_index.md", "index.md"]
        for option in options:
            if option in self.files:
                return option
        return self.aliases.get(site)

    def resolve(self, href: str, source_relative: str) -> tuple[str, str, str | None]:
        """(kind, status, content-relative target or None); status is resolved / external / unresolved / skip."""
        href = href.strip("<>")
        if href.startswith("ref:"):
            name = href[4:].split("#", 1)[0]
            entries = (self.front.get(source_relative, {}).get("refs") or {}).get(name)
            if not isinstance(entries, list):
                return "ref", "unresolved", None
            entry = next((e for e in entries if isinstance(e, dict) and isinstance(e.get("pattern"), str)
                          and self.product.startswith(e["pattern"])), None)
            if entry is None or not isinstance(entry.get("destination"), str):
                return "ref", "unresolved", None
            destination = urlparse(entry["destination"]).path
            site = _site_path(destination, self.prefixes)
            if site is None:
                return "ref", "external", None
            target = self._file_for(site)
            return "ref", ("resolved" if target else "unresolved"), target
        parsed = urlparse(href)
        if parsed.scheme or parsed.netloc or href.startswith(("mailto:", "tel:")):
            return "site_absolute", "external", None
        path = unquote(parsed.path)
        if not path:
            return "site_relative", "skip", None   # a fragment on the same page
        if path.lower().endswith((".md", ".png", ".jpg", ".jpeg", ".gif", ".svg")):
            return "site_relative", "skip", None   # a file path: the markdown profile's (or not a page)
        if path.startswith("/"):
            site = _site_path(path if path.endswith("/") else path + "/", self.prefixes)
            if site is None:
                return "site_absolute", "external", None
            target = self._file_for(site)
            return "site_absolute", ("resolved" if target else "unresolved"), target
        target = self._file_for(posixpath.join(page_url(source_relative), path))
        return "site_relative", ("resolved" if target else "unresolved"), target


def hugo_edges(corpus_dir: Path, path_to_doc: dict[str, str], params: dict[str, Any],
               hrefs_of: Callable[[str], list[str]]) -> tuple[set[tuple[str, str]], dict[str, dict[str, int]]]:
    """Edges and per-kind counts of the Hugo profile over the admitted pages under the content root."""
    content_root = params["content_root"].strip("/")
    prefix = content_root + "/" if content_root else ""
    content = {key[len(prefix):]: (corpus_dir / key).read_text(encoding="utf-8", errors="replace")
               for key in sorted(path_to_doc) if key.startswith(prefix) and key.endswith(".md")}
    resolver = HugoResolver(content, params)
    counts = {kind: {"found": 0, "resolved": 0, "external": 0, "unresolved": 0} for kind in KINDS}
    edges: set[tuple[str, str]] = set()
    for relative, text in content.items():
        source = path_to_doc[prefix + relative]
        for href in hrefs_of(text):
            kind, status, target = resolver.resolve(href, relative)
            if status == "skip":
                continue
            counts[kind]["found"] += 1
            counts[kind][status] += 1
            if target is not None:
                target_id = path_to_doc.get(prefix + target)
                if target_id is not None and target_id != source:
                    edges.add((source, target_id))
    return edges, counts
