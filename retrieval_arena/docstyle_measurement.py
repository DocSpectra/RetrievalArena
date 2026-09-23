"""S052: the DocEng 2026 per-document metrics (Appendix B) computed inside the evaluator.

The paper ("Operational Runbooks as Living Documents", D'Antonio, Xie, Guo,
DOI 10.1145/3820755.3821485) defines 35 per-document measurements in seven
categories plus four metadata fields. This module computes them as the
paper's text defines them, over the raw Markdown source of every ``.md``
document a corpus request admits (the same inventory loop as
``candidate_retrieval``: ``rglob('*.md')``, the import policy's document id,
the exclusion policy), with spaCy ``en_core_web_sm`` 3.8.0 for the Basic NLP,
NER and POS categories. The paper's own tool (``RunbookTextStats``, a
fine-tuned NER) is not available; every choice its text leaves open is
declared once in ``ALGORITHM_CHOICES`` and written into the manifest, never
tuned toward the paper's exported tables. Column names are the export's
(``github_per_document.csv``).

Reading revision R1 (S052 calibration, 2026-09-21, the one revision the
prompt allows): the text metrics (word count, marker densities, the regex
sentence count and the spaCy input) read the *text view* -- the raw source
with fenced code blocks and inline code spans removed -- and
``paragraph_count`` counts every blank-line-separated block of that view.
The export README's "code blocks ... are parsed structurally, not stripped"
is read as: code is structure (counted by the code metrics), not text. The
file and Markdown-structure metrics still read the raw source, fence-aware.

S055 (2026-09-22): two identifier column sets beside the spaCy ones, because generic
NER sees none of the technical identifiers a maintainer's fix names (E-EXP6). The
*identifier view* keeps inline code (fenced blocks, link targets, autolinks, images
and HTML tags removed); Counter 1 (``identifier_rules_v1``) classifies bare tokens
by eight rules; Counter 2 (``repo_lexicon_v1``, ``repo_lexicon.py``) counts mentions
of the repository's own identifiers, harvested from the mounted checkout when the
config carries a ``lexicon`` block or read from ``lexicon_path``. The pre-existing
columns are unchanged in definition and value; the table schema is ``.v2`` because
rows gained columns (file names unchanged).

No retrieval, no question, no generative model: a measurement.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from .candidate_retrieval import (
    EXCLUDE_POLICIES, EXCLUDE_POLICY_CIKM_V2, IMPORT_POLICIES, canonical, excluded_by_policy,
    imported_document_name, sha256_bytes, sha256_file, validate_faq_patterns,
)
from .errors import ValidationError
from .repo_lexicon import (
    IDENTIFIER_CLASSES, LEXICON_POLICY, classify_identifier, load_lexicon, load_stop_catalogs, mention_index,
)

CONFIG_SCHEMA = "retrieval_arena.docstyle_measurement_config.v1"
REQUEST_SCHEMA = "retrieval_arena.docstyle_measurement_request.v1"
TABLE_SCHEMA = "retrieval_arena.docstyle_metrics.v2"
SUMMARY_SCHEMA = "retrieval_arena.docstyle_summary.v2"
LEXICON_COMMIT_POLICIES = ("same_commit", "frozen_snapshot")
SPACY_MODEL = "en_core_web_sm"
SPACY_MODEL_VERSION = "3.8.0"
CATALOG_DIR = Path(__file__).resolve().parent / "docstyle_catalogs"
CATALOG_FILES = {"vagueness": "docstyle_vagueness_v1.json", "constraint": "docstyle_constraint_v1.json"}
MATTR_WINDOW = 50
TOP_ENTITIES = 10
FOCAL_SIGNALS = ("imperative_verb_ratio", "constraint_density", "vagueness_density", "entity_density")

# The export's 31 metric columns, in its column order, then the four the export omits.
EXPORT_METRIC_COLUMNS = (
    "word_count", "sentence_count", "avg_sentence_len_tokens", "token_count", "bytes", "lines",
    "type_token_ratio", "moving_avg_type_token_ratio", "entity_density", "entity_count",
    "unique_entity_count", "entity_mention_ratio", "imperative_verb_ratio", "pronoun_density",
    "pronoun_count", "vagueness_density", "constraint_density", "code_fence_count", "code_block_lines",
    "list_item_count", "header_count", "header_h1_count", "header_h2_count", "header_h3_count",
    "header_h4_count", "header_h5_count", "header_h6_count", "paragraph_count", "bullet_list_count",
    "numbered_list_count", "checklist_count",
)
EXTRA_COLUMNS = ("chars", "sentence_count_basic", "entity_type_counts", "top_entities", "identifier_class_counts")
# S055: the identifier columns, their own tuple (never part of the export's 31).
IDENTIFIER_COLUMNS = ("identifier_view_word_count", "identifier_count", "unique_identifier_count", "identifier_density",
                      "lexicon_mention_count", "unique_lexicon_mention_count", "lexicon_mention_density",
                      "identifier_resolution_ratio")
NUMERIC_COLUMNS = EXPORT_METRIC_COLUMNS + ("chars", "sentence_count_basic") + IDENTIFIER_COLUMNS
IDENTITY_COLUMNS = ("doc_id", "doc_path", "corpus_id", "commit", "error")
ROW_COLUMNS = IDENTITY_COLUMNS + EXPORT_METRIC_COLUMNS + EXTRA_COLUMNS + IDENTIFIER_COLUMNS

# Every algorithmic choice the paper's text leaves open, stated before any calibration run.
ALGORITHM_CHOICES = {
    "source_text": "the raw Markdown source decoded as UTF-8 for the file and structure metrics; the text metrics (word_count, sentence_count_basic, paragraph_count, marker densities, the spaCy input) read the text view: the source with fenced code blocks (fence lines and their contents, the structure parser's fence state) and inline code spans (`...`, across lines) removed, nothing else stripped (front matter and Liquid tags stay) -- reading revision R1 of the S052 calibration",
    "bytes": "len(source bytes)",
    "chars": "len(decoded text)",
    "lines": "len(text.splitlines())",
    "word_count": "len(text_view.split()) -- whitespace-separated over the text view (R1; before R1: over the raw source)",
    "header": "ATX only: a line outside a fence matching ^ {0,3}(#{1,6})(?:\\s|$); setext underlines are not headers",
    "code_fence": "a line outside a fence whose lstrip starts with ``` or ~~~ opens a block; the next line starting with the same fence character run closes it; an unclosed block runs to the end; code_fence_count counts opened blocks; code_block_lines counts the lines strictly between the fence lines",
    "bullet_list": "a line outside a fence matching ^\\s*[-*+]\\s+\\S (any indent; nested items count)",
    "numbered_list": "a line outside a fence matching ^\\s*\\d+[.)]\\s+\\S",
    "checklist": "a bullet line whose item text starts with [ ] or [x]/[X]; counted inside bullet_list_count as well (the export's rule)",
    "list_item_count": "bullet_list_count + numbered_list_count (the paper's text: total list items; the paper's tool wrote bullets only -- a declared paper-tool discrepancy)",
    "paragraph_count": "every maximal run of non-blank lines of the text view, headers and list items included (R1; before R1: runs outside fences whose first line was neither a header nor a list item)",
    "sentence_count_basic": "len(re.findall(r'[.!?]+(?=\\s|$)', text_view))",
    "marker_matching": "over the text view casefolded, U+2019 -> U+0027, whitespace collapsed; case-insensitive, word-boundary, longest match first, non-overlapping (the S051 rule); density = count / word_count * 1000",
    "spacy_input": "the whole text view in one nlp() call, exclude=[]",
    "token_count": "tokens with not token.is_space (punctuation included)",
    "sentence_count": "len(list(doc.sents))",
    "avg_sentence_len_tokens": "token_count / sentence_count",
    "type_token_ratio": "len({t.text.casefold() for t in tokens}) / token_count over the non-space tokens",
    "moving_avg_type_token_ratio": "MATTR with window 50 over the casefolded non-space token sequence; equal to type_token_ratio when token_count <= 50 (decided by the export)",
    "entity": "doc.ents; unique_entity_count = distinct ent.text (case-sensitive); entity_density = entity_count / token_count; entity_mention_ratio = unique / entity_count",
    "entity_type_counts": "Counter of ent.label_, keys sorted",
    "top_entities": "the 10 most frequent ent.text values as [[text, count], ...] ordered by (-count, text)",
    "pronoun": "tokens with token.pos_ == 'PRON'; pronoun_density = pronoun_count / token_count * 1000",
    "all_verbs": "tokens with token.pos_ == 'VERB'",
    "imperative": "a VERB token with tag_ == 'VB' and dep_ == 'ROOT' and no child whose dep_ is nsubj, nsubjpass, csubj or expl",
    "zero_conventions": "every density and ratio is 0.0 on a zero denominator; entity_mention_ratio is 0.0 when entity_count is 0",
    "quantiles": "p25 / p75 (and any p) = the order statistic at index int(n * p) of the ascending values (the export's rule)",
    "stdev": "sample standard deviation (n - 1); null when n < 2",
    "percentile_rank": "(count strictly below + 0.5 * count equal) / n over the corpus's measured rows",
    "spearman": "mid-rank Spearman rho over rows with token_count > 0 (a declared departure from the paper's verify_stats.py, which keeps every row); null with a reason when a column is constant or fewer than 3 rows remain",
    "error_policy": "a document whose measurement raises keeps its row with error set and every metric null",
    # S055 identifier columns
    "identifier_view": "the source with fenced blocks removed (the same fence state as text_view) but inline code kept; then Markdown link targets ](...), autolinks <http...>/<https...>, image targets and HTML tags <[a-zA-Z/][^>]*> removed; front matter and Liquid tags stay (as text_view)",
    "identifier_token": "whitespace-split tokens of the identifier view, stripped of surrounding .,;:!?()[]{}\"'*~<>|` ; empty tokens dropped; identifier_view_word_count = the number of non-empty tokens",
    "identifier_classes": "fullmatch, first class wins, in order: (a) any token inside an inline code span of <= 6 whitespace tokens (a longer span contributes nothing); outside spans: (b) --[a-z0-9][a-z0-9-]*(=\\S+)? ; (c) [A-Za-z_][\\w-]*(\\.[A-Za-z_][\\w-]*)+ with >= 2 letters in every segment; (d) [\\w.-]+(/[\\w.-]+)+ not starting with http and containing . - or _ ; (e) [A-Za-z0-9]+(_[A-Za-z0-9]+)+ ; (f) [A-Za-z]*[a-z][A-Z][A-Za-z0-9]* ; (g) v?\\d+(\\.\\d+){1,3} with a v prefix or >= 3 numeric segments; (h) [A-Z0-9_-]{3,} with >= 1 letter and (>= 1 _ or >= 1 digit); hyphenated lowercase prose never qualifies -- identifier_rules_v1 (S055)",
    "identifier_count": "the number of classified tokens; unique_identifier_count = distinct token text (case-sensitive); identifier_density = identifier_count / identifier_view_word_count * 100; identifier_class_counts = Counter of classes a..h",
    "lexicon_mention": "repo_lexicon_v1 (repo_lexicon.py): an identifier-view token (inside or outside spans) that equals a lexicon entry exactly, or equals it casefolded when the entry has >= 6 characters and is in neither stop catalog; lexicon_mention_density = count / identifier_view_word_count * 100; identifier_resolution_ratio = unique identifiers that are exact entries / unique identifiers (null when no identifier); all four columns null without a lexicon",
}

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'"})
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADER = re.compile(r"^ {0,3}(#{1,6})(?:\s|$)")
_BULLET = re.compile(r"^\s*[-*+]\s+(\S.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+\S")
_CHECKBOX = re.compile(r"^\[( |x|X)\](?:\s|$)")
_SENTENCE_BASIC = re.compile(r"[.!?]+(?=\s|$)")
_INLINE_CODE = re.compile(r"`[^`]*`")
_SUBJECT_DEPS = {"nsubj", "nsubjpass", "csubj", "expl"}


# --- catalogs -----------------------------------------------------------------

def load_catalog(path: Path) -> dict[str, Any]:
    """One S051 docstyle catalog, byte-identical to the Capstone copy; entries normalized as S051 does."""
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict) or set(payload) != {"catalog_name", "catalog_version", "entries", "metadata"}:
        raise ValidationError("docstyle-catalog-shape")
    entries = tuple(" ".join(item.translate(_APOSTROPHES).casefold().split()) for item in payload["entries"])
    if len(set(entries)) != len(entries) or not entries:
        raise ValidationError("docstyle-catalog-entries")
    printed = payload["metadata"].get("printed_entry_count")
    if printed is not None and printed != len(entries):
        raise ValidationError("docstyle-catalog-entry-count")
    return {"name": payload["catalog_name"], "version": payload["catalog_version"], "entries": entries,
            "sha256": sha256_file(path), "pattern": _marker_pattern(entries),
            "paper_stated_entry_count": payload["metadata"].get("paper_stated_entry_count"),
            "printed_entry_count": len(entries)}


def load_catalogs() -> dict[str, dict[str, Any]]:
    return {key: load_catalog(CATALOG_DIR / name) for key, name in CATALOG_FILES.items()}


def _marker_pattern(entries: tuple[str, ...]) -> re.Pattern[str]:
    ordered = sorted(entries, key=lambda item: (-len(item), item))
    alternatives = "|".join(r"\s+".join(re.escape(piece) for piece in entry.split()) for entry in ordered)
    return re.compile(r"(?<![a-z0-9_'])(?:" + alternatives + r")(?![a-z0-9_'])")


def count_markers(text: str, pattern: re.Pattern[str]) -> int:
    normalized = " ".join(text.translate(_APOSTROPHES).casefold().split())
    return len(pattern.findall(normalized))


# --- Markdown structure, file and text-volume metrics (no model) -------------------------------------

def text_view(text: str) -> str:
    """R1: the source without fenced code blocks (the structure parser's fence state) and inline code spans."""
    kept = []
    in_fence = False
    fence_char = ""
    for line in text.splitlines():
        fence = _FENCE.match(line)
        if in_fence:
            if fence and fence.group(1)[0] == fence_char:
                in_fence = False
            continue
        if fence:
            in_fence = True
            fence_char = fence.group(1)[0]
            continue
        kept.append(line)
    return _INLINE_CODE.sub("", "\n".join(kept))


# --- S055: the identifier view and the two identifier counters (no model) -------------------------------

_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_AUTOLINK = re.compile(r"<https?://[^>]*>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[a-zA-Z/][^>]*>")
_STRIP_CHARS = ".,;:!?()[]{}\"'*~<>|`"
MAX_SPAN_TOKENS = 6


def identifier_view(text: str) -> str:
    """The source without fenced blocks, images, link targets, autolinks and HTML tags; inline code kept."""
    kept = []
    in_fence = False
    fence_char = ""
    for line in text.splitlines():
        fence = _FENCE.match(line)
        if in_fence:
            if fence and fence.group(1)[0] == fence_char:
                in_fence = False
            continue
        if fence:
            in_fence = True
            fence_char = fence.group(1)[0]
            continue
        kept.append(line)
    view = "\n".join(kept)
    view = _IMAGE.sub("", view)
    view = _LINK_TARGET.sub("]", view)
    view = _AUTOLINK.sub("", view)
    return _HTML_TAG.sub("", view)


PROSE, CODE_SPAN, LONG_CODE_SPAN = "prose", "code_span", "long_code_span"


def identifier_tokens(view: str) -> list[tuple[str, str]]:
    """(bare token, where it sits) for every non-empty token of the identifier view, in order.

    ``code_span``: inside an inline code span of <= 6 tokens; ``long_code_span``: inside a longer span
    (a code fragment: a view word and a possible lexicon mention, never a Counter 1 identifier -- S056
    aligns the code with the rule as written, gate FIX-3 of S055); ``prose``: outside any span.
    """
    tokens: list[tuple[str, str]] = []
    position = 0
    for match in _INLINE_CODE.finditer(view):
        for raw in view[position:match.start()].split():
            tokens.append((raw.strip(_STRIP_CHARS), PROSE))
        span = match.group(0)[1:-1].split()
        kind = CODE_SPAN if len(span) <= MAX_SPAN_TOKENS else LONG_CODE_SPAN
        for raw in span:
            tokens.append((raw.strip(_STRIP_CHARS), kind))
        position = match.end()
    for raw in view[position:].split():
        tokens.append((raw.strip(_STRIP_CHARS), PROSE))
    return [(token, kind) for token, kind in tokens if token]


def identifier_metrics(text: str, lexicon_index: tuple[frozenset[str], dict[str, str]] | None) -> dict[str, Any]:
    """Counter 1 over the identifier view; Counter 2 when a lexicon index is given, else null columns."""
    view = identifier_view(text)
    tokens = identifier_tokens(view)
    classes: Counter = Counter()
    identifiers: list[str] = []
    for token, kind in tokens:
        if kind == LONG_CODE_SPAN:
            continue
        if kind == CODE_SPAN:
            classes["a_code_span"] += 1
            identifiers.append(token)
            continue
        name = classify_identifier(token)
        if name is not None:
            classes[name] += 1
            identifiers.append(token)
    unique = set(identifiers)
    values: dict[str, Any] = {
        "identifier_view_word_count": len(tokens), "identifier_count": len(identifiers),
        "unique_identifier_count": len(unique),
        "identifier_density": _per(len(identifiers), len(tokens), 100.0),
        "identifier_class_counts": dict(sorted(classes.items())),
        "lexicon_mention_count": None, "unique_lexicon_mention_count": None, "lexicon_mention_density": None,
        "identifier_resolution_ratio": None,
    }
    if lexicon_index is not None:
        exact, folded = lexicon_index
        mentions = []
        for token, _ in tokens:
            if token in exact:
                mentions.append(token)
            elif token.casefold() in folded:
                mentions.append(folded[token.casefold()])
        values.update({
            "lexicon_mention_count": len(mentions), "unique_lexicon_mention_count": len(set(mentions)),
            "lexicon_mention_density": _per(len(mentions), len(tokens), 100.0),
            "identifier_resolution_ratio": (sum(1 for item in unique if item in exact) / len(unique)) if unique else None,
        })
    return values


def structure_metrics(text: str, raw: bytes) -> dict[str, Any]:
    """File (3), Markdown Structure (13) of Appendix B over the raw source; Text Volume (3) over the text view."""
    headers = Counter()
    fence_count = code_lines = bullets = numbered = checklists = 0
    in_fence = False
    fence_char = ""
    for line in text.splitlines():
        fence = _FENCE.match(line)
        if in_fence:
            if fence and fence.group(1)[0] == fence_char:
                in_fence = False
            else:
                code_lines += 1
            continue
        if fence:
            in_fence = True
            fence_char = fence.group(1)[0]
            fence_count += 1
            continue
        if not line.strip():
            continue
        header = _HEADER.match(line)
        bullet = _BULLET.match(line)
        if header:
            headers[len(header.group(1))] += 1
        elif bullet:
            bullets += 1
            if _CHECKBOX.match(bullet.group(1)):
                checklists += 1
        elif _NUMBERED.match(line):
            numbered += 1
    view = text_view(text)
    paragraphs = 0
    in_paragraph = False
    for line in view.splitlines():
        if not line.strip():
            in_paragraph = False
        elif not in_paragraph:
            paragraphs += 1
            in_paragraph = True
    values = {"bytes": len(raw), "chars": len(text), "lines": len(text.splitlines()),
              "header_count": sum(headers.values())}
    for level in range(1, 7):
        values[f"header_h{level}_count"] = headers[level]
    values.update({"code_fence_count": fence_count, "code_block_lines": code_lines,
                   "bullet_list_count": bullets, "numbered_list_count": numbered,
                   "checklist_count": checklists, "list_item_count": bullets + numbered,
                   "word_count": len(view.split()), "paragraph_count": paragraphs,
                   "sentence_count_basic": len(_SENTENCE_BASIC.findall(view))})
    return values


def marker_metrics(text: str, word_count: int, catalogs: dict[str, dict[str, Any]]) -> dict[str, float]:
    vague = count_markers(text, catalogs["vagueness"]["pattern"])
    constraint = count_markers(text, catalogs["constraint"]["pattern"])
    return {"vagueness_density": _per(vague, word_count, 1000.0),
            "constraint_density": _per(constraint, word_count, 1000.0)}


def _per(numerator: float, denominator: float, scale: float = 1.0) -> float:
    return numerator / denominator * scale if denominator else 0.0


# --- spaCy metrics ---------------------------------------------------------------------------------

def nlp_metrics(doc: Any) -> dict[str, Any]:
    """Basic NLP (5), NER (6) and POS (3) of Appendix B over one spaCy Doc."""
    tokens = [token for token in doc if not token.is_space]
    token_count = len(tokens)
    sentence_count = sum(1 for _ in doc.sents)
    folded = [token.text.casefold() for token in tokens]
    ttr = _per(len(set(folded)), token_count)
    if token_count <= MATTR_WINDOW:
        mattr = ttr
    else:
        windows = token_count - MATTR_WINDOW + 1
        mattr = sum(len(set(folded[start:start + MATTR_WINDOW])) / MATTR_WINDOW for start in range(windows)) / windows
    entities = list(doc.ents)
    entity_texts = Counter(entity.text for entity in entities)
    pronouns = sum(1 for token in tokens if token.pos_ == "PRON")
    verbs = [token for token in tokens if token.pos_ == "VERB"]
    imperatives = sum(1 for token in verbs
                      if token.tag_ == "VB" and token.dep_ == "ROOT"
                      and not any(child.dep_ in _SUBJECT_DEPS for child in token.children))
    return {
        "token_count": token_count, "sentence_count": sentence_count,
        "avg_sentence_len_tokens": _per(token_count, sentence_count),
        "type_token_ratio": ttr, "moving_avg_type_token_ratio": mattr,
        "entity_count": len(entities), "entity_density": _per(len(entities), token_count),
        "unique_entity_count": len(entity_texts),
        "entity_mention_ratio": _per(len(entity_texts), len(entities)),
        "entity_type_counts": dict(sorted(Counter(entity.label_ for entity in entities).items())),
        "top_entities": [[text, count] for text, count in
                         sorted(entity_texts.items(), key=lambda item: (-item[1], item[0]))[:TOP_ENTITIES]],
        "pronoun_count": pronouns, "pronoun_density": _per(pronouns, token_count, 1000.0),
        "imperative_verb_ratio": _per(imperatives, len(verbs)),
    }


def load_spacy() -> tuple[Any, dict[str, Any]]:
    """The pinned pipeline and its identity (version, meta, components, installed-directory inventory hash)."""
    import spacy

    nlp = spacy.load(SPACY_MODEL, exclude=[])
    meta = nlp.meta
    if meta.get("lang") != "en" or meta.get("name") != "core_web_sm" or meta.get("version") != SPACY_MODEL_VERSION:
        raise ValidationError("docstyle-spacy-model-identity")
    model_dir = Path(str(nlp.path)) if nlp.path is not None else None
    inventory_sha256 = None
    if model_dir is not None and model_dir.is_dir():
        rows = [{"path": path.relative_to(model_dir).as_posix(), "sha256": sha256_file(path)}
                for path in sorted(model_dir.rglob("*")) if path.is_file()]
        inventory_sha256 = sha256_bytes(canonical(rows))
    identity = {"spacy_version": spacy.__version__, "model": SPACY_MODEL,
                "meta": {key: meta.get(key) for key in ("lang", "name", "version", "spacy_version")},
                "pipeline": list(nlp.pipe_names), "model_directory_inventory_sha256": inventory_sha256}
    return nlp, identity


# --- request and corpus admission ----------------------------------------------------------------

def _relative_or_dot(value: Any, code: str) -> str:
    if (not isinstance(value, str) or not value or Path(value).is_absolute() or value.startswith("/") or "\\" in value
            or any(part in {"", ".."} for part in Path(value).parts) or (value != "." and "." in Path(value).parts)):
        raise ValidationError(code)
    return value


def validate_config(value: Any) -> dict[str, Any]:
    fields = {"schema_version", "corpus_id", "candidate", "corpus"}
    optional = {"support_path", "lexicon", "lexicon_path", "lexicon_commit_policy"}
    if not isinstance(value, dict) or not fields <= set(value) or not set(value) <= fields | optional:
        raise ValidationError("docstyle-config-fields")
    if "lexicon" in value and "lexicon_path" in value:
        raise ValidationError("docstyle-lexicon-exclusive")
    if "lexicon_commit_policy" in value and (
            "lexicon_path" not in value or value["lexicon_commit_policy"] not in LEXICON_COMMIT_POLICIES):
        raise ValidationError("docstyle-lexicon-commit-policy")
    if "lexicon" in value:
        block = value["lexicon"]
        if (not isinstance(block, dict) or set(block) != {"policy", "source_root", "exclude_root"}
                or block["policy"] != LEXICON_POLICY):
            raise ValidationError("docstyle-lexicon-block")
        _relative_or_dot(block["source_root"], "docstyle-lexicon-source-root")
        if block["exclude_root"] is not None:
            _relative_or_dot(block["exclude_root"], "docstyle-lexicon-exclude-root")
            if block["exclude_root"] == ".":
                raise ValidationError("docstyle-lexicon-exclude-root")
    if "lexicon_path" in value:
        _relative_or_dot(value["lexicon_path"], "docstyle-lexicon-path")
        if value["lexicon_path"] == ".":
            raise ValidationError("docstyle-lexicon-path")
    if value["schema_version"] != CONFIG_SCHEMA:
        raise ValidationError("docstyle-config-schema")
    if not isinstance(value["corpus_id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value["corpus_id"]):
        raise ValidationError("docstyle-corpus-id")
    candidate = value["candidate"]
    if (not isinstance(candidate, dict) or set(candidate) != {"commit", "tree"}
            or any(not isinstance(candidate[name], str) or not re.fullmatch(r"[0-9a-f]{40}", candidate[name])
                   for name in ("commit", "tree"))):
        raise ValidationError("docstyle-candidate-identity")
    corpus = value["corpus"]
    base = {"root", "extensions", "import_policy"}
    if (not isinstance(corpus, dict)
            or set(corpus) not in (base, base | {"exclude_policy"}, base | {"exclude_policy", "faq_patterns"})):
        raise ValidationError("docstyle-corpus-fields")
    _relative_or_dot(corpus["root"], "docstyle-corpus-root")
    if corpus["extensions"] != [".md"] or corpus["import_policy"] not in IMPORT_POLICIES:
        raise ValidationError("docstyle-import-policy")
    if "exclude_policy" in corpus and corpus["exclude_policy"] is not None and corpus["exclude_policy"] not in EXCLUDE_POLICIES:
        raise ValidationError("docstyle-exclude-policy")
    if (corpus.get("exclude_policy") == EXCLUDE_POLICY_CIKM_V2) != ("faq_patterns" in corpus):
        raise ValidationError("docstyle-faq-patterns")
    if "faq_patterns" in corpus:
        validate_faq_patterns(corpus["faq_patterns"])
    if "support_path" in value:
        _relative_or_dot(value["support_path"], "docstyle-support-path")
        if value["support_path"] == ".":
            raise ValidationError("docstyle-support-path")
    return value


def load_support_ids(path: Path) -> tuple[list[str], str]:
    """The campaign's support.json as it is ({records, targets}); the sorted unique target ids and the file hash."""
    if not path.is_file() or path.is_symlink():
        raise ValidationError("docstyle-support-missing")
    payload = json.loads(path.read_bytes())
    if (not isinstance(payload, dict) or set(payload) != {"records", "targets"}
            or not isinstance(payload["targets"], list)
            or any(not isinstance(row, dict) or not isinstance(row.get("target_id"), str) for row in payload["targets"])):
        raise ValidationError("docstyle-support-shape")
    return sorted({row["target_id"] for row in payload["targets"]}), sha256_file(path)


def inventory_corpus(corpus_root: Path, corpus: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The candidate_retrieval inventory rule: sorted rglob('*.md'), the import policy's id, the exclusion policy."""
    if not corpus_root.is_dir() or corpus_root.is_symlink():
        raise ValidationError("docstyle-corpus-root")
    import_policy = corpus["import_policy"]
    exclude_policy = corpus.get("exclude_policy")
    faq_patterns = corpus.get("faq_patterns")
    rows, excluded = [], []
    seen: set[str] = set()
    for source in sorted(corpus_root.rglob("*.md")):
        if source.is_symlink() or not source.is_file():
            raise ValidationError("docstyle-corpus-file")
        relative_text = source.relative_to(corpus_root).as_posix()
        imported_name, doc_id = imported_document_name(relative_text, import_policy)
        if imported_name in seen:
            raise ValidationError("docstyle-document-id-collision")
        seen.add(imported_name)
        row = {"path": relative_text, "doc_id": doc_id, "source": source}
        if excluded_by_policy(relative_text, exclude_policy, faq_patterns):
            excluded.append(row)
        else:
            rows.append(row)
    if not rows:
        raise ValidationError("docstyle-corpus-empty")
    return rows, excluded


# --- the table ------------------------------------------------------------------------------------

def measure_document(source: Path, nlp: Any, catalogs: dict[str, dict[str, Any]],
                     lexicon_index: tuple[frozenset[str], dict[str, str]] | None = None) -> dict[str, Any]:
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    values = structure_metrics(text, raw)
    view = text_view(text)
    values.update(marker_metrics(view, values["word_count"], catalogs))
    values.update(nlp_metrics(nlp(view)))
    values.update(identifier_metrics(text, lexicon_index))
    return values


def measure_rows(rows: list[dict[str, Any]], corpus_id: str, commit: str, nlp: Any,
                 catalogs: dict[str, dict[str, Any]],
                 lexicon_index: tuple[frozenset[str], dict[str, str]] | None = None) -> list[dict[str, Any]]:
    table = []
    for row in rows:
        record: dict[str, Any] = {"doc_id": row["doc_id"], "doc_path": row["path"], "corpus_id": corpus_id,
                                  "commit": commit, "error": None}
        try:
            record.update(measure_document(row["source"], nlp, catalogs, lexicon_index))
        except Exception as error:  # the per-document failure policy: the row stays, every metric null
            record["error"] = f"{type(error).__name__}: {error}"
            for column in EXPORT_METRIC_COLUMNS + EXTRA_COLUMNS + IDENTIFIER_COLUMNS:
                record[column] = None
        table.append({column: record[column] for column in ROW_COLUMNS})
    return table


def lexicon_for_config(config: dict[str, Any], candidate_root: Path, input_root: Path,
                       stops: dict[str, dict[str, Any]] | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The lexicon a config asks for -- built from the candidate or loaded from the input root -- and its manifest entry."""
    from .repo_lexicon import build_lexicon

    stops = stops if stops is not None else load_stop_catalogs()
    if "lexicon" in config:
        block = config["lexicon"]
        source_root = candidate_root if block["source_root"] == "." else candidate_root / block["source_root"]
        exclude_root = None if block["exclude_root"] is None else candidate_root / block["exclude_root"]
        lexicon = build_lexicon(source_root, exclude_root, config["candidate"], stops)
        return lexicon, {"policy": LEXICON_POLICY, "origin": "built", "commit": lexicon["commit"],
                         "entry_count": lexicon["entry_count"], "lexicon_sha256": lexicon["lexicon_sha256"],
                         "commit_policy": "same_commit"}
    if "lexicon_path" in config:
        lexicon = load_lexicon(input_root / config["lexicon_path"])
        policy = config.get("lexicon_commit_policy", "same_commit")
        if policy == "same_commit" and lexicon["commit"] != config["candidate"]["commit"]:
            raise ValidationError("docstyle-lexicon-commit-mismatch")
        return lexicon, {"policy": LEXICON_POLICY, "origin": "loaded", "path": config["lexicon_path"],
                         "commit": lexicon["commit"], "entry_count": lexicon["entry_count"],
                         "lexicon_sha256": lexicon["lexicon_sha256"], "commit_policy": policy}
    return None, {"policy": None, "origin": "absent"}


# --- summary conventions -------------------------------------------------------------------------

def order_statistic(values: list[float], p: float) -> float:
    return sorted(values)[int(len(values) * p)]


def percentile_rank(values: list[float], value: float) -> float:
    below = sum(1 for item in values if item < value)
    equal = sum(1 for item in values if item == value)
    return (below + 0.5 * equal) / len(values)


def column_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p25": None, "p75": None, "min": None, "max": None, "stdev": None}
    return {"count": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "p25": order_statistic(values, 0.25), "p75": order_statistic(values, 0.75),
            "min": min(values), "max": max(values),
            "stdev": statistics.stdev(values) if len(values) >= 2 else None}


def _midranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        rank = (start + end) / 2 + 1
        for position in range(start, end + 1):
            ranks[order[position]] = rank
        start = end + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> dict[str, Any]:
    if len(x) < 3:
        return {"rho": None, "n": len(x), "reason": "insufficient_rows"}
    if len(set(x)) == 1 or len(set(y)) == 1:
        return {"rho": None, "n": len(x), "reason": "constant_column"}
    rx, ry = _midranks(x), _midranks(y)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    var = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return {"rho": cov / var, "n": len(x), "reason": None}


def summarize(table: list[dict[str, Any]], support_ids: list[str] | None) -> dict[str, Any]:
    measured = [row for row in table if row["error"] is None]
    # S055: every column over its non-null values (the lexicon columns are null without a lexicon)
    columns = {column: column_summary([row[column] for row in measured if row[column] is not None])
               for column in NUMERIC_COLUMNS}
    correlated = [row for row in measured if row["token_count"] > 0]
    rho = {left: {right: spearman([row[left] for row in correlated], [row[right] for row in correlated])
                  for right in FOCAL_SIGNALS} for left in FOCAL_SIGNALS}
    summary: dict[str, Any] = {"schema_version": SUMMARY_SCHEMA, "row_count": len(table),
                               "measured_row_count": len(measured), "error_row_count": len(table) - len(measured),
                               "columns": columns,
                               "spearman_rho": {"signals": list(FOCAL_SIGNALS), "rows": len(correlated),
                                                "zero_token_rows_dropped": len(measured) - len(correlated),
                                                "matrix": rho},
                               "support": None}
    if support_ids is not None:
        by_id = {row["doc_id"]: row for row in measured}
        matched = [doc_id for doc_id in support_ids if doc_id in by_id]
        support_rows = {}
        for doc_id in matched:
            row = by_id[doc_id]
            support_rows[doc_id] = {"doc_path": row["doc_path"], "columns": {
                column: {"value": row[column],
                         "percentile_rank": (None if row[column] is None else percentile_rank(
                             [item[column] for item in measured if item[column] is not None], row[column]))}
                for column in NUMERIC_COLUMNS}}
        summary["support"] = {"support_id_count": len(support_ids), "matched_count": len(matched),
                              "unmatched_ids": [doc_id for doc_id in support_ids if doc_id not in by_id],
                              "rows": support_rows}
    return summary


def manifest(config: dict[str, Any], spacy_identity: dict[str, Any], catalogs: dict[str, dict[str, Any]],
             table_bytes: bytes, table: list[dict[str, Any]], excluded: list[dict[str, Any]],
             support: tuple[list[str], str] | None, python_version: str,
             lexicon: dict[str, Any] | None = None, stops: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": TABLE_SCHEMA, "corpus_id": config["corpus_id"], "candidate": config["candidate"],
        "corpus": config["corpus"], "python_version": python_version, "spacy": spacy_identity,
        "catalogs": {key: {name: catalog[name] for name in
                           ("name", "version", "sha256", "printed_entry_count", "paper_stated_entry_count")}
                     for key, catalog in catalogs.items()},
        "algorithm_choices": ALGORITHM_CHOICES, "mattr_window": MATTR_WINDOW, "top_entities": TOP_ENTITIES,
        "columns": list(ROW_COLUMNS), "row_count": len(table),
        "error_count": sum(1 for row in table if row["error"] is not None),
        "excluded_document_count": len(excluded), "excluded_document_ids": sorted(row["doc_id"] for row in excluded),
        "support": ({"path": config.get("support_path"), "sha256": support[1], "id_count": len(support[0])}
                    if support is not None else None),
        "lexicon": lexicon if lexicon is not None else {"policy": None, "origin": "absent"},
        "stop_catalogs": ({key: {name: catalog[name] for name in ("name", "version", "sha256", "entry_count")}
                           for key, catalog in stops.items()} if stops else None),
        "table_sha256": sha256_bytes(table_bytes),
    }


def table_bytes(table: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical(row) + b"\n" for row in table)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()
