"""Connectivity constraint C v2: C v1 plus two declared relaxations; standalone, not an operator.

The contract is docs/evaluations/structural_connectivity_constraint_v2.md. C v1
(`structural_target.connectivity_admission`) runs unchanged; v2 can only remove
a v1 reason, never add one:

- C2 joined pages: on a C2 failure, lambda2 is recomputed on the v1 evaluation
  set plus the baseline pages outside the baseline main component that the
  variant joined to its main component.
- C1b templated pages: on a C1b failure, a new page reached through the site's
  templating rather than a Markdown link (a referenced include, or a section
  index whose folder holds another page) is not counted. The include root, the
  include reference pattern and the index filename are config; no corpus is
  named here.

structural_target.py is not edited, so consumers pinning v1 by file hash keep
their pin.
"""
from dataclasses import asdict, dataclass
import re

from retrieval_arena.graph import structural_target as v1

CONSTRAINT_VERSION = 'structural_connectivity_constraint.v2'
C2_REASON = 'C2-lambda2-below-tolerance'
C1B_REASON = 'C1b-new-page-without-in-link'


@dataclass(frozen=True)
class TemplatedPageRule:
    """How a corpus reaches pages through templating. Paths are POSIX, relative to the corpus root."""
    include_root: str           # folder prefix of include pages, with a trailing '/'
    include_reference: str      # regex with one group: the referenced include name
    index_filename: str         # section index file name
    page_suffix: str = '.md'    # an include may be referenced with or without it


@dataclass(frozen=True)
class ConstraintV2Config:
    base: v1.StructuralConfig = v1.StructuralConfig()
    c2_joined_pages: bool = True
    templated_pages: TemplatedPageRule = None  # None: C1b exactly as v1


def _rule_form(rule):
    return None if rule is None else asdict(rule)


def config_digest(config=ConstraintV2Config()):
    """SHA-256 of the canonical JSON form; the base part is the v1 config's own form."""
    base = {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(config.base).items()}
    return v1.digest({'version': CONSTRAINT_VERSION, 'base': base, 'c2_joined_pages': config.c2_joined_pages,
                      'templated_pages': _rule_form(config.templated_pages)})


def _reference_pattern(rule):
    pattern = re.compile(rule.include_reference)
    if pattern.groups != 1:
        raise ValueError('include-reference-needs-one-group')
    if not rule.include_root.endswith('/') or '/' in rule.index_filename or not rule.index_filename:
        raise ValueError('templated-rule-malformed')
    return pattern


def templated_exempt(rel, rels, refs, rule):
    """True if page `rel` is reached through templating under `rule`.

    rels: every variant page path; refs: every include name referenced by any variant page.
    """
    if rel.startswith(rule.include_root):
        name = rel[len(rule.include_root):]
        suffix = rule.page_suffix
        return name in refs or bool(suffix and name.endswith(suffix) and name[:-len(suffix)] in refs)
    if rel.endswith('/' + rule.index_filename):
        folder = rel[:-len(rule.index_filename)]
        return any(r != rel and r.startswith(folder) for r in rels)
    return False


def connectivity_admission(nodes, edges, baseline, *, mapping=None, config=ConstraintV2Config(), pages=None):
    """C v2: pass/fail with reasons. `pages` maps every variant node id to (relative path, text); it is required
    when `config.templated_pages` is set."""
    mapping = mapping or {}
    ns = v1.canonical_nodes(nodes)
    rule = config.templated_pages
    if rule is not None:
        pattern = _reference_pattern(rule)
        if pages is None or set(pages) != set(ns):
            raise ValueError('pages-must-cover-nodes')
    res = v1.connectivity_admission(ns, edges, baseline, mapping=mapping, config=config.base)
    reasons = list(res['reasons'])
    out = dict(res, version=CONSTRAINT_VERSION, v1_passed=res['passed'], v1_reasons=list(res['reasons']),
               base_config_sha256=res['config_sha256'], config_sha256=config_digest(config),
               c2_joined_pages=None, lambda2_v1=res['lambda2'], c1b_orphans_all=None, c1b_exempted=None)
    known = set(ns)
    es = v1.canonical_edges(ns, edges)
    base_nodes = set(baseline['nodes'])
    new = sorted(known - base_nodes)
    if C2_REASON in reasons and config.c2_joined_pages:
        m = v1.main_component(ns, es)
        base_main = set(baseline['main_component'])
        evaluation = ({mapping.get(v, v) for v in base_main} | set(new)) & known
        joined = {v for v in m if v in base_nodes and v not in base_main}
        ev = evaluation | joined
        lam = v1.normalized_lambda2(sorted(ev), [e for e in es if e[0] in ev and e[1] in ev])
        out.update(lambda2=lam, c2_joined_pages=len(joined))
        if lam >= res['lambda2_threshold']:
            reasons.remove(C2_REASON)
    if C1B_REASON in reasons and rule is not None:
        has_in = {t for _, t in es}
        orphans = [v for v in new if v not in has_in]
        rels = {rel for rel, _ in pages.values()}
        refs = {name for _, text in pages.values() for name in pattern.findall(text)}
        exempt = [pages[v][0] for v in orphans if templated_exempt(pages[v][0], rels, refs, rule)]
        out.update(c1b_orphans_all=len(orphans), c1b_exempted=exempt)
        if len(exempt) == len(orphans):
            reasons.remove(C1B_REASON)
    out.update(reasons=reasons, passed=not reasons)
    return out
