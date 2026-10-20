import random
import unittest

from retrieval_arena.graph import structural_target as v1
from retrieval_arena.graph.structural_target_v2 import (
    CONSTRAINT_VERSION, ConstraintV2Config, TemplatedPageRule, config_digest, connectivity_admission,
    templated_exempt)

RULE = TemplatedPageRule(include_root='inc/', include_reference=r'\{\{[%<]\s*include\s+"([^"]+)"',
                         index_filename='_index.md')
LOOSE = v1.StructuralConfig(lambda2_tolerance=.95)  # C2 threshold at 5% of baseline: only a disconnected set fails

# Baseline: main component a-b-c-d (a 4-cycle plus a chord) and a page x outside it; anchor a.
BASE_NODES = ['a', 'b', 'c', 'd', 'x']
BASE_EDGES = [('a', 'b'), ('b', 'c'), ('c', 'd'), ('d', 'a'), ('a', 'c')]
PATHS = {'a': 'a.md', 'b': 'b.md', 'c': 'c.md', 'd': 'd.md', 'x': 'x.md'}


def baseline(config=LOOSE):
    return v1.baseline_summary(BASE_NODES, BASE_EDGES, ['a'], config=config)


def pages(extra_paths, texts=None):
    texts = texts or {}
    paths = dict(PATHS, **extra_paths)
    return {k: (p, texts.get(k, '')) for k, p in paths.items()}


def run(nodes, edges, config, page_map=None):
    base = baseline(config.base)
    return (v1.connectivity_admission(nodes, edges, base, config=config.base),
            connectivity_admission(nodes, edges, base, config=config, pages=page_map))


class ConstraintV2Tests(unittest.TestCase):
    def test_c2_joined_orphan_passes_in_v2_only(self):
        # The variant links c -> x (x joins M) and adds page n reached only from x. v1's evaluation set
        # (baseline M plus new pages) leaves n isolated, so lambda2 = 0; v2 adds the joined page x.
        nodes = BASE_NODES + ['n']
        edges = BASE_EDGES + [('c', 'x'), ('x', 'n')]
        old, new = run(nodes, edges, ConstraintV2Config(base=LOOSE))
        self.assertEqual(old['reasons'], ['C2-lambda2-below-tolerance'])
        self.assertEqual(old['lambda2'], 0.)
        self.assertTrue(new['passed'], new)
        self.assertEqual(new['c2_joined_pages'], 1)
        self.assertEqual(new['lambda2_v1'], 0.)
        ev = ['a', 'b', 'c', 'd', 'n', 'x']
        self.assertEqual(new['lambda2'], v1.normalized_lambda2(ev, [e for e in edges if set(e) <= set(ev)]))
        self.assertEqual(new['v1_reasons'], old['reasons'])
        self.assertFalse(new['v1_passed'])
        self.assertEqual(new['version'], CONSTRAINT_VERSION)

    def test_c2_genuinely_low_lambda2_still_fails(self):
        nodes = BASE_NODES + ['n']
        edges = BASE_EDGES + [('c', 'x'), ('x', 'n')]
        strict = v1.StructuralConfig(lambda2_tolerance=.0)
        _, new = run(nodes, edges, ConstraintV2Config(base=strict))
        self.assertEqual(new['reasons'], ['C2-lambda2-below-tolerance'])
        self.assertGreater(new['lambda2'], 0.)

    def test_c2_relaxation_can_be_disabled(self):
        nodes = BASE_NODES + ['n']
        edges = BASE_EDGES + [('c', 'x'), ('x', 'n')]
        _, new = run(nodes, edges, ConstraintV2Config(base=LOOSE, c2_joined_pages=False))
        self.assertEqual(new['reasons'], ['C2-lambda2-below-tolerance'])
        self.assertIsNone(new['c2_joined_pages'])

    def test_section_index_with_child_is_exempt(self):
        # New section s/_index.md links to its child; nothing links to the index.
        nodes = BASE_NODES + ['s_idx', 's_child']
        edges = BASE_EDGES + [('a', 's_child'), ('s_idx', 's_child')]
        pm = pages({'s_idx': 's/_index.md', 's_child': 's/child.md'})
        old, new = run(nodes, edges, ConstraintV2Config(base=LOOSE, templated_pages=RULE), pm)
        self.assertEqual(old['reasons'], ['C1b-new-page-without-in-link'])
        self.assertTrue(new['passed'], new)
        self.assertEqual(new['c1b_exempted'], ['s/_index.md'])
        self.assertEqual(new['c1b_orphans_all'], 1)

    def test_section_index_alone_still_fails(self):
        nodes = BASE_NODES + ['s_idx']
        edges = BASE_EDGES + [('s_idx', 'a')]
        pm = pages({'s_idx': 's/_index.md'})
        _, new = run(nodes, edges, ConstraintV2Config(base=LOOSE, templated_pages=RULE), pm)
        self.assertEqual(new['reasons'], ['C1b-new-page-without-in-link'])
        self.assertEqual(new['c1b_exempted'], [])

    def test_referenced_include_is_exempt_with_or_without_suffix(self):
        nodes = BASE_NODES + ['inc1']
        edges = BASE_EDGES + [('inc1', 'a')]
        for ref in ('snippet.md', 'snippet'):
            pm = pages({'inc1': 'inc/snippet.md'}, {'b': 'Intro\n{{< include "%s" >}}\n' % ref})
            old, new = run(nodes, edges, ConstraintV2Config(base=LOOSE, templated_pages=RULE), pm)
            self.assertEqual(old['reasons'], ['C1b-new-page-without-in-link'])
            self.assertTrue(new['passed'], (ref, new))
            self.assertEqual(new['c1b_exempted'], ['inc/snippet.md'])

    def test_unreferenced_include_still_fails_c1b(self):
        nodes = BASE_NODES + ['inc1']
        edges = BASE_EDGES + [('inc1', 'a')]
        pm = pages({'inc1': 'inc/snippet.md'}, {'b': '{{< include "other.md" >}}'})
        _, new = run(nodes, edges, ConstraintV2Config(base=LOOSE, templated_pages=RULE), pm)
        self.assertEqual(new['reasons'], ['C1b-new-page-without-in-link'])
        self.assertEqual(new['c1b_exempted'], [])
        self.assertFalse(new['passed'])

    def test_one_unexempt_orphan_keeps_c1b(self):
        nodes = BASE_NODES + ['inc1', 'lone']
        edges = BASE_EDGES + [('inc1', 'a'), ('lone', 'a')]
        pm = pages({'inc1': 'inc/snippet.md', 'lone': 'lone.md'}, {'b': '{{% include "snippet.md" %}}'})
        _, new = run(nodes, edges, ConstraintV2Config(base=LOOSE, templated_pages=RULE), pm)
        self.assertEqual(new['reasons'], ['C1b-new-page-without-in-link'])
        self.assertEqual(new['c1b_exempted'], ['inc/snippet.md'])
        self.assertEqual(new['c1b_orphans_all'], 2)

    def test_without_rule_c1b_is_v1(self):
        nodes = BASE_NODES + ['s_idx', 's_child']
        edges = BASE_EDGES + [('a', 's_child'), ('s_idx', 's_child')]
        _, new = run(nodes, edges, ConstraintV2Config(base=LOOSE))
        self.assertEqual(new['reasons'], ['C1b-new-page-without-in-link'])
        self.assertIsNone(new['c1b_exempted'])

    def test_pages_required_and_must_cover_nodes(self):
        base = baseline()
        config = ConstraintV2Config(base=LOOSE, templated_pages=RULE)
        with self.assertRaisesRegex(ValueError, 'pages-must-cover-nodes'):
            connectivity_admission(BASE_NODES, BASE_EDGES, base, config=config)
        with self.assertRaisesRegex(ValueError, 'pages-must-cover-nodes'):
            connectivity_admission(BASE_NODES, BASE_EDGES, base, config=config, pages={'a': ('a.md', '')})
        bad = TemplatedPageRule(include_root='inc/', include_reference='include', index_filename='_index.md')
        with self.assertRaisesRegex(ValueError, 'one-group'):
            connectivity_admission(BASE_NODES, BASE_EDGES, base, config=ConstraintV2Config(templated_pages=bad),
                                   pages=pages({}))

    def test_templated_exempt_paths(self):
        rels = {'s/_index.md', 's/t/deep.md', 'u/_index.md', 'inc/x.md'}
        self.assertTrue(templated_exempt('s/_index.md', rels, set(), RULE))   # descendant counts
        self.assertFalse(templated_exempt('u/_index.md', rels, set(), RULE))
        self.assertTrue(templated_exempt('inc/x.md', rels, {'x'}, RULE))
        self.assertFalse(templated_exempt('inc/x.md', rels, {'y.md'}, RULE))
        self.assertFalse(templated_exempt('other.md', rels, {'other.md'}, RULE))

    def test_v2_only_removes_reasons(self):
        rng = random.Random(7)
        config = ConstraintV2Config(base=v1.StructuralConfig(), templated_pages=RULE)
        base = baseline(config.base)
        for trial in range(60):
            extra = [f'n{i}' for i in range(rng.randint(0, 4))]
            nodes = BASE_NODES + extra
            edges = [(rng.choice(nodes), rng.choice(nodes)) for _ in range(rng.randint(3, 12))]
            paths = {n: (f'inc/{n}.md' if rng.random() < .3 else f'sec{n}/_index.md') for n in extra}
            pm = pages(paths, {'a': ' '.join('{{< include "%s" >}}' % n for n in extra if rng.random() < .5)})
            old = v1.connectivity_admission(nodes, edges, base, config=config.base)
            new = connectivity_admission(nodes, edges, base, config=config, pages=pm)
            self.assertTrue(set(new['reasons']) <= set(old['reasons']), trial)
            self.assertEqual(new['v1_reasons'], old['reasons'])
            self.assertEqual(new['passed'], not new['reasons'])

    def test_config_digest(self):
        self.assertEqual(config_digest(), config_digest(ConstraintV2Config()))
        self.assertNotEqual(config_digest(), config_digest(ConstraintV2Config(templated_pages=RULE)))
        self.assertNotEqual(config_digest(), config_digest(ConstraintV2Config(c2_joined_pages=False)))
        self.assertNotEqual(config_digest(), v1.config_digest())
        _, new = run(BASE_NODES, BASE_EDGES, ConstraintV2Config(base=LOOSE))
        self.assertEqual(new['base_config_sha256'], v1.config_digest(LOOSE))
        self.assertEqual(new['config_sha256'], config_digest(ConstraintV2Config(base=LOOSE)))


if __name__ == '__main__':
    unittest.main()
