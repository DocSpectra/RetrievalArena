# Connectivity constraint C v2

`structural_connectivity_constraint.v2` is C v1 ([structural target](structural_target.md)) plus two relaxations, each switched by config. It lives in `retrieval_arena/graph/structural_target_v2.py` beside v1. Tests are in `unit_tests/test_structural_target_v2.py`, which is in the `focused` suite.

Like v1, it is a standalone corpus-graph diagnostic. It is not a retrieval or answer score, not an operator command and not a corpus edit. The consuming workspace freezes the config and the baseline, and owns every admission decision.

`structural_target.py` is unchanged, so a consumer that pins v1 by file hash keeps its pin. v2 imports v1's functions and runs `connectivity_admission` v1 first.

## Call

`connectivity_admission(nodes, edges, baseline, *, mapping=None, config=ConstraintV2Config(), pages=None)`

- `nodes`, `edges`, `baseline` and `mapping` are as in v1. `baseline` is `structural_target.baseline_summary(...)` computed with `config.base`.
- `pages` maps every variant node ID to `(relative_path, text)`. Paths are POSIX and relative to the corpus root. It is required when `config.templated_pages` is set, and must cover exactly the node set (`ValueError('pages-must-cover-nodes')`).

## Rule

v2 starts from v1's reasons. It can only remove a reason, never add one, so a v1 pass is always a v2 pass.

**C2 joined pages** (`c2_joined_pages=True`, the default). This applies only when v1 reports `C2-lambda2-below-tolerance`.

- λ₂ is recomputed on v1's evaluation set plus the *joined pages*: baseline pages that were outside the baseline main component and are inside the variant's main component.
- C2 passes if that value is at least v1's threshold, (1 − `lambda2_tolerance`) × baseline λ₂.
- Why: v1's set excludes joined baseline pages so that connecting them is never penalized. But if a new page is reachable only through a joined page, v1's set is disconnected and λ₂ is 0 even though the variant's main component is connected. A genuinely low λ₂ on a connected set still fails.

**C1b templated pages** (`templated_pages`, default `None`, meaning C1b exactly as v1). This applies only when v1 reports `C1b-new-page-without-in-link`. Some site generators reach a page through templating rather than a Markdown link, so a link graph built from Markdown links can never show it an in-link. Under a `TemplatedPageRule`, a new page with no in-link is *exempt* if either:

- **Include page.** Its path starts with `include_root`, and the remainder (`name`) is referenced by some variant page. A page references an include when a match of `include_reference` (a regex with exactly one group, the referenced name) equals `name`, or equals `name` without `page_suffix`.
- **Section index.** Its path ends with `/` + `index_filename`, and some other variant page's path starts with the index's folder (a child page or a deeper descendant).

C1b passes only if every new page without an in-link is exempt. An include that no page references, an index alone in its folder and every other new page without an in-link still fail. C0, C1 and the C2 threshold are unchanged.

Example rule for a Hugo site whose includes sit in `content/includes/`. This is the consumer's config, not a default:

```python
TemplatedPageRule(include_root='content/includes/',
                  include_reference=r'\{\{[%<]\s*include\s+"([^"]+)"',
                  index_filename='_index.md', page_suffix='.md')
```

## Result

The result is v1's result dictionary with these changes:

- `version` is `structural_connectivity_constraint.v2`. `passed` and `reasons` are the v2 verdict.
- `v1_passed` and `v1_reasons` keep v1's verdict.
- `lambda2` is the value C2 was judged on. `lambda2_v1` is v1's value. `c2_joined_pages` is the joined-page count, or `None` if the relaxation did not run.
- `c1b_orphans_all` is the number of new pages without an in-link. `c1b_exempted` lists the exempt paths. Both are `None` if the relaxation did not run.
- `config_sha256` is `config_digest(config)`: SHA-256 of the canonical JSON of `{version, base, c2_joined_pages, templated_pages}`. `base_config_sha256` is v1's `config_digest(config.base)`, so a v2 receipt still names the v1 config it extends.

## Not claimed

The relaxations encode a site's templating conventions; the rule does not check that the generator actually renders a page or its links. A consumer adopting v2 for a study that was frozen under v1 records the change and replays its past verdicts under v2 before relying on it.
