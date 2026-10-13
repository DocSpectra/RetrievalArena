import random
import unittest

from retrieval_arena.graph.structural_target import (
    StructuralConfig, anchored_reachability, baseline_summary, config_digest,
    connectivity_admission, link_guard, main_component, normalized_lambda2)


def tree():
    # Binary tree of depth 3 rooted at r: 15 nodes, edges written parent -> child.
    nodes, edges = ['r'], []
    for level in range(3):
        for parent in [n for n in nodes if len(n) == level + 1]:
            for c in '01':
                nodes.append(parent + c)
                edges.append((parent, parent + c))
    return nodes, edges


def rev(edges):
    return [(t, s) for s, t in edges]


class StructuralTargetTests(unittest.TestCase):
    def test_forest_direction_anchors_first_versus_leaves_first(self):
        # The directional test the 09b subspace descriptor failed: orientation of a forest.
        ns, es = tree()
        second = [('x', 'x0'), ('x', 'x1')]
        ns2, es2 = ns + ['x', 'x0', 'x1'], es + second + [('r', 'x')]
        down = anchored_reachability(ns2, es2, ['r'], 3)
        up = anchored_reachability(ns2, rev(es2), ['r'], 3)
        self.assertEqual(down['value'], 1.0)
        self.assertAlmostEqual(up['value'], 1 / 18)
        self.assertEqual(down['main_component_size'], up['main_component_size'])
        self.assertLess(anchored_reachability(ns2, es2, ['r'], 2)['value'], 1.0)

    def test_reversal_changes_T_unless_anchors_symmetric(self):
        path = ['a', 'b', 'c']
        es = [('a', 'b'), ('b', 'c')]
        # Path, anchor at one end: T = 1 forward, 1/3 reversed.
        self.assertEqual(anchored_reachability(path, es, ['a'], 3)['value'], 1.0)
        self.assertAlmostEqual(anchored_reachability(path, rev(es), ['a'], 3)['value'], 1 / 3)
        # Symmetric anchor set {a, c} under the reflection a<->c: reversal maps the
        # graph to its mirror image, so T is unchanged.
        self.assertEqual(anchored_reachability(path, es, ['a', 'c'], 1)['value'],
                         anchored_reachability(path, rev(es), ['a', 'c'], 1)['value'])
        # Directed cycle with one anchor: reversal is the reflection fixing the anchor.
        cyc = [('a', 'b'), ('b', 'c'), ('c', 'a')]
        for d in (0, 1, 2):
            self.assertEqual(anchored_reachability(path, cyc, ['a'], d)['value'],
                             anchored_reachability(path, rev(cyc), ['a'], d)['value'])
        # All-reciprocal graphs are reversal invariant for any anchors.
        two = es + rev(es)
        self.assertEqual(anchored_reachability(path, two, ['b'], 1), anchored_reachability(path, rev(two), ['b'], 1))

    def test_split_with_links_keeps_T_and_orphan_split_fails_C(self):
        ns = ['r', 'x', 'y', 'z']
        es = [('r', 'x'), ('x', 'y'), ('x', 'z')]
        base = baseline_summary(ns, es, ['r'])
        t0 = anchored_reachability(ns, es, ['r'], 3)
        kept = ns + ['x2']
        kept_es = [('r', 'x'), ('x', 'y'), ('x', 'x2'), ('x2', 'x'), ('x2', 'z')]
        t1 = anchored_reachability(kept, kept_es, ['r'], 3)
        self.assertEqual((t0['value'], t1['value']), (1.0, 1.0))
        self.assertEqual((t0['reached_count'], t1['reached_count']), (4, 5))
        ok = connectivity_admission(kept, kept_es, base, config=StructuralConfig(use_lambda2=False))
        self.assertTrue(ok['passed'], ok['reasons'])
        # Orphan: new page keeps the moved out-link but nobody links to it; it stays
        # weakly connected, so only C1b catches it.
        orphan_es = [('r', 'x'), ('x', 'y'), ('y', 'z'), ('x2', 'z')]
        bad = connectivity_admission(kept, orphan_es, base, config=StructuralConfig(use_lambda2=False))
        self.assertEqual(bad['reasons'], ['C1b-new-page-without-in-link'])
        # Isolated new page.
        iso = connectivity_admission(kept, es, base, config=StructuralConfig(use_lambda2=False))
        self.assertIn('C1-new-page-outside-main-component', iso['reasons'])
        self.assertLess(anchored_reachability(kept, orphan_es, ['r'], 3)['value'], 1.0)

    def test_bridge_deletion_fails_C_whatever_T_does(self):
        tri = lambda a, b, c: [(a, b), (b, c), (c, a)]
        ns = list('abcdef')
        es = tri('a', 'b', 'c') + tri('d', 'e', 'f') + [('c', 'd')]
        base = baseline_summary(ns, es, ['a'])
        cut = [e for e in es if e != ('c', 'd')]
        r = connectivity_admission(ns, cut, base)
        self.assertFalse(r['passed'])
        self.assertIn('C1-baseline-page-left-main-component', r['reasons'])
        self.assertEqual(r['baseline_pages_left_main_component'], 3)
        # T on the fragmented graph rises (the main component shrinks to the anchor side)
        # yet C rejects it: C is an admission rule, not a score trade-off.
        self.assertGreater(anchored_reachability(ns, cut, ['a'], 1)['value'],
                           anchored_reachability(ns, es, ['a'], 1)['value'])

    def test_link_padding_beyond_guard_is_rejected(self):
        ns = [f'n{i:02d}' for i in range(40)]
        es = [(ns[i], ns[i + 1]) for i in range(39)]
        base = baseline_summary(ns, es, ['n00'])
        t0 = anchored_reachability(ns, es, ['n00'], 3)['value']
        padded = es + [('n00', v) for v in ns[2:]]
        t1 = anchored_reachability(ns, padded, ['n00'], 3)['value']
        self.assertEqual(t1, 1.0)
        self.assertGreater(t1, t0)
        g = link_guard(padded, base, nodes=ns)
        self.assertFalse(g['passed'])
        self.assertEqual(g['reasons'], ['G1-edge-budget-exceeded', 'G2-outdegree-cap-exceeded'])
        # Within budget and cap the guard admits it. One added link can bring a page's
        # whole (d-1)-hop forward ball into reach, so the gain can exceed added/|M|.
        small = es + [('n00', v) for v in ns[30:37]]
        self.assertTrue(link_guard(small, base, nodes=ns)['passed'])
        gain = anchored_reachability(ns, small, ['n00'], 3)['value'] - t0
        self.assertAlmostEqual(gain, 9 / 40)
        self.assertGreater(gain, 7 / 40)

    def test_order_and_duplicate_invariance(self):
        ns, es = tree()
        ns += ['x', 'y']
        es += [('x', 'y'), ('r000', 'r'), ('r01', 'r1')]
        base = baseline_summary(ns, es, ['r'])
        t = anchored_reachability(ns, es, ['r'], 3)
        c = connectivity_admission(ns, es, base)
        g = link_guard(es, base, nodes=ns)
        rng = random.Random(7)
        for _ in range(10):
            pn, pe = ns[:], es + es[:5] + [('r', 'r'), ('r', 'ghost')]
            rng.shuffle(pn)
            rng.shuffle(pe)
            self.assertEqual(anchored_reachability(pn, pe, ['r', 'r'], 3), t)
            self.assertEqual(connectivity_admission(pn, pe, baseline_summary(pn, pe, ['r'])), c)
            self.assertEqual(link_guard(pe, base, nodes=pn), g)
            self.assertEqual(main_component(pn, pe), main_component(ns, es))
        with self.assertRaises(ValueError):
            anchored_reachability(['a', 'a'], [], ['a'])

    def test_lambda2_tolerance_merge_mapping_and_joined_pages(self):
        a, b = ['a0', 'a1', 'a2', 'a3'], ['b0', 'b1', 'b2', 'b3']
        clique = lambda xs: [(x, y) for i, x in enumerate(xs) for y in xs[i + 1:]]
        es = clique(a) + clique(b) + [('a0', 'b0'), ('a1', 'b1'), ('a2', 'b2')]
        ns = a + b + ['iso']
        base = baseline_summary(ns, es, ['a0'])
        self.assertAlmostEqual(base['lambda2'], .3596, places=3)
        self.assertEqual(normalized_lambda2(['a', 'b', 'c'], [('a', 'b')]), 0.)
        # Dropping one of three cross links lowers lambda2 by about 35% without
        # disconnecting anything: only C2 rejects.
        weak = connectivity_admission(ns, es[:-1], base)
        self.assertEqual(weak['reasons'], ['C2-lambda2-below-tolerance'])
        # Joining an outside page never enters the C2 evaluation set.
        joined = connectivity_admission(ns, es + [('iso', 'b3')], base)
        self.assertTrue(joined['passed'], joined['reasons'])
        self.assertEqual(joined['lambda2'], base['lambda2'])
        # Merging a1 into a0: mapped pages are retained, anchors survive through the map.
        merged_nodes = [v for v in ns if v != 'a1']
        merged_es = [(s.replace('a1', 'a0'), t.replace('a1', 'a0')) for s, t in es]
        m = connectivity_admission(merged_nodes, merged_es, base, mapping={'a1': 'a0'},
                                   config=StructuralConfig(use_lambda2=False))
        self.assertTrue(m['passed'], m['reasons'])
        gone = connectivity_admission(merged_nodes, merged_es, baseline_summary(ns, es, ['a1']),
                                      config=StructuralConfig(use_lambda2=False))
        self.assertIn('C0-anchor-missing', gone['reasons'])
        self.assertIn('C1-baseline-page-left-main-component', gone['reasons'])

    def test_versions_and_config_digest(self):
        r = anchored_reachability(['a', 'b'], [('a', 'b')], ['a'])
        self.assertEqual(r['version'], 'anchored_forward_reachability.v1')
        self.assertEqual(r['config_sha256'], config_digest())
        self.assertNotEqual(config_digest(), config_digest(StructuralConfig(depth=2)))
        self.assertEqual(r['profile']['inf'], 1.0)


if __name__ == '__main__':
    unittest.main()
