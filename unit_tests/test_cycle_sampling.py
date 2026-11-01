import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from retrieval_arena.graph.cycle_sampling import sample_cycles, priority
from retrieval_arena.graph import magnetic_attribution as ma
from unit_tests.test_magnetic_attribution import bundle


class SamplingTests(unittest.TestCase):
    def sample(self, nodes, edges, **kw):
        return sample_cycles(nodes, edges, corpus='fixture', seed=231031, **kw)

    def test_rotation_dedup_reordering_and_no_outcomes(self):
        nodes = list('abcdef')
        edges = [('a', 'b'), ('b', 'c'), ('c', 'a'), ('c', 'd'), ('d', 'e'), ('e', 'f'), ('f', 'c')]
        with patch.object(ma.ms, 'spectrum', side_effect=AssertionError('outcome-read')):
            a, ledger = self.sample(nodes, edges)
            b, other = self.sample(nodes[::-1], edges[::-1] + edges)
        self.assertEqual(a, b)
        self.assertEqual(ledger, other)
        self.assertEqual(a['cycles'], [list('abc'), list('cdef')])
        self.assertTrue(all(v['status'] == 'exhaustive' for v in a['by_length'].values()))
        self.assertEqual(len(ledger.splitlines()), 2)

    def test_exhaustive_vs_capped_empty_and_each_length_budget(self):
        a, _ = self.sample(list('abc'), [('a', 'b'), ('b', 'c')], budget=1)
        b, _ = self.sample(list('abc'), [('a', 'b'), ('b', 'c')], budget=100)
        self.assertTrue(all(r['expansions'] == 1 and r['empty_interpretation'] ==
                            'none_found_within_search_budget' for r in a['by_length'].values()))
        self.assertTrue(all(r['empty_interpretation'] == 'no_eligible_cycles'
                            for r in b['by_length'].values()))

    def test_quota_is_not_stopping_rule_and_no_transferred_slots(self):
        nodes = list('abcdef')
        edges = [(nodes[i], nodes[j]) for i in range(6) for j in range(6)
                 if (j-i) % 6 in (1, 2)]
        a, ledger = self.sample(nodes, edges, quota=1)
        b, _ = self.sample(nodes, edges, quota=2)
        for length, row in a['by_length'].items():
            self.assertEqual(row['expansions'], b['by_length'][length]['expansions'])
            self.assertEqual(row['candidate_stream_sha256'], b['by_length'][length]['candidate_stream_sha256'])
            candidates = [json.loads(line) for line in ledger.splitlines()
                          if json.loads(line)['length'] == int(length)]
            self.assertEqual(row['selected'], sorted(candidates, key=lambda r: (r['selection_hash'], r['cycle']))[:1])
        self.assertGreater(sum(v['candidates'] for v in a['by_length'].values()), len(a['selected']))

    def test_bruteforce_frame_and_nonreciprocal_eligibility(self):
        nodes = list('abcde')
        edges = [('a','b'), ('b','c'), ('c','a'), ('c','d'), ('d','e'), ('e','a'), ('b','a')]
        a, ledger = self.sample(nodes, edges)
        encountered = {tuple(json.loads(line)['cycle']) for line in ledger.splitlines()}
        es = set(edges)
        expected = set()
        for k in range(3, 7):
            for c in itertools.permutations(nodes, k):
                if c[0] != min(c):
                    continue
                if all((c[i], c[(i+1)%k]) in es and (c[(i+1)%k], c[i]) not in es for i in range(k)):
                    expected.add(c)
        self.assertEqual(expected, encountered)
        self.assertNotIn(tuple('abc'), encountered)

    def test_domain_separation_and_fixed_serialization(self):
        import hashlib
        raw = b'["nonreciprocal_cycle_sample.v2","selection",231031,"fixture",3,["a","b","c"]]'
        self.assertEqual(priority('selection',231031,'fixture',3,'abc'), hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(priority('selection',1,'x',3,'abc'), priority('root',1,'x',3,'abc'))

    def test_invalid_inputs(self):
        for kw in ({'budget':0}, {'quota':0}, {'lengths':[3,3]}, {'lengths':[2]}):
            with self.assertRaises(ValueError):
                self.sample(list('abc'), [], **kw)
        with self.assertRaises(ValueError):
            self.sample(list('abc'), [('a','x')])

    def test_bounded_execution_matches_v1_and_reuses_q0(self):
        nodes = list('abcd')
        edges = [('a','b'), ('b','c'), ('c','a'), ('a','d'), ('d','b')]
        changed = [('a','b'), ('b','c'), ('c','a')]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'a.json').write_text(json.dumps(bundle(nodes, edges)))
            (root/'b.json').write_text(json.dumps(bundle(nodes, ma.reverse_arcs(edges, changed))))
            config = {'schema_version':ma.CONFIG_SCHEMA, 'spectrum_config':{}, 'attribution_config':{}, 'jobs':[
                {'kind':'pair', 'job_id':'p', 'bundle_a':'a.json', 'bundle_b':'b.json', 'view':{'kind':'full'},
                 'q':[0.0,0.1,0.25], 'control':'matched_cycle_reversal', 'changed_arcs':changed}]}
            old, _ = ma.run_jobs(config, root)
            config['schema_version'] = ma.CONFIG_SCHEMA_V2
            with patch.object(ma, '_solve', wraps=ma._solve) as solve:
                new, arts = ma.run_jobs(config, root)
                self.assertEqual(solve.call_count, 5)
            for q in ('0.00','0.10','0.25'):
                for key in ('delta_lambda1','finite_change'):
                    self.assertLess(abs(old[0]['q'][q]['delta_lambda1']['raw'] -
                                        new[0]['q'][q]['delta_lambda1']['raw']), 1e-13)
                self.assertEqual(new[0]['q'][q]['variant_solve_reused_baseline_after_exact_q0_equality'], q=='0.00')
            self.assertTrue(all(np.load(__import__('io').BytesIO(data)).shape[1] == 1
                                for path,data in arts.items() if path.endswith('.npy')))

    def test_old_selection_contract_unchanged(self):
        result = ma.nonreciprocal_cycles(list('abc'), [('a','b'),('b','c'),('c','a')], max_cycles=1)
        self.assertEqual(result['cycles'], [list('abc')])
        self.assertEqual(result['status'], 'max_cycles_reached')


if __name__ == '__main__':
    unittest.main()
