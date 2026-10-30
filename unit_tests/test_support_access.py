from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from retrieval_arena.candidate_retrieval import canonical, regular_inventory, sha256_bytes
from retrieval_arena.graph import support_access as sa
from retrieval_arena.graph.phase_navigation import traverse_once, score_trial


class SupportAccessTests(unittest.TestCase):
    def setUp(self):
        self.nodes = list('abcdefgh')
        self.scores = {n: 1 - i*.1 for i,n in enumerate(self.nodes)}
        self.policy = dict(max_hops=1,neighbor_budget=20,candidate_budget=40,final_top_k=4,distance_penalty=.02,directed=True)

    def evaluate(self, targets, edges=None, policy=None):
        edges = [('a','e')] if edges is None else edges
        policy = policy or self.policy
        saved = traverse_once(self.nodes,edges,self.scores,'a',**policy)
        prediction = sa.freeze_prediction(self.nodes,edges,self.scores.items(),saved,policy)
        return sa.evaluate_prediction(self.nodes,edges,prediction,targets,policy,[1,2,4,8])

    def test_four_cells_and_exact_net_identity(self):
        rows = [self.evaluate([t]) for t in ('a','e','b','f')]
        self.assertEqual([r['cell'] for r in rows],list(sa.CELLS))
        out = sa.aggregate(rows)
        self.assertEqual(out['paired_cells'],dict.fromkeys(sa.CELLS,1))
        self.assertEqual(out['paired_differences']['G4_minus_B4'],0)
        self.assertEqual(out['recovery_fraction'],{'numerator':1,'denominator':2,'fraction':.5})
        self.assertEqual(out['loss_fraction']['fraction'],.5)

    def test_positive_seed_and_neighbor_is_not_net_rescue(self):
        row = self.evaluate(['a','e'])
        self.assertEqual(row['historical_expansion_added_hit'],1)
        self.assertEqual(row['seed_miss_graph_hit'],0)
        self.assertEqual(row['G4_minus_B1'],0)
        self.assertEqual(row['both_hit_support_relation'],'different_or_additional')
        self.assertEqual(row['graph_exclusive_known_support'],['e'])

    def test_unjudged_pages_are_not_judged_negative(self):
        row = self.evaluate(['f'])
        self.assertEqual(row['hits']['G4'],0)
        self.assertNotIn('negative_ids',row)
        self.assertEqual(row['known_target_ids_in_view'],['f'])

    def test_ties_duplicates_missing_and_nonfinite(self):
        rank,_ = sa.cached_ranking(['b','a'],[('b',.5),('a',.5)])
        self.assertEqual(rank,['a','b'])
        for pairs in ([('a',1),('a',1)], [('a',1)], [('a',1),('b',float('nan'))], [('a',1),('b',float('inf'))]):
            with self.assertRaises(ValueError): sa.cached_ranking(['a','b'],pairs)

    def test_zero_denominators_unavailable(self):
        all_success = sa.aggregate([self.evaluate(['a'])])
        self.assertIsNone(all_success['recovery_fraction']['fraction'])
        no_success = sa.aggregate([self.evaluate(['f'])])
        self.assertIsNone(no_success['loss_fraction']['fraction'])
        self.assertEqual(sa.aggregate([])['status'],'unavailable')

    def test_underfilled_actual_count_and_seed_retention(self):
        row = self.evaluate(['b'])
        self.assertEqual(row['selected_counts'],{'B1':1,'B4':4,'Bm':2,'G4':2})
        self.assertEqual(row['G4_minus_B4'],-1)
        self.assertEqual(row['G4_minus_Bm'],-1)
        self.assertTrue(row['seed_retained'])
        dead = self.evaluate(['b'],edges=[])
        self.assertTrue(dead['dead_end'])
        self.assertEqual(dead['G4_minus_Bm'],0)

    def test_view_filtering_and_target_projection(self):
        row = self.evaluate(['outside','e'])
        self.assertEqual(row['known_target_ids_in_view'],['e'])
        empty = self.evaluate(['outside'])
        self.assertFalse(empty['eligible'])
        self.assertIsNone(empty['known_positive_set_coverage']['G4'])
        rank,_ = sa.cached_ranking(['b','c'],self.scores.items())
        self.assertEqual(rank,['b','c'])

    def test_all_four_failure_stages_and_trace_explanations(self):
        final = self.evaluate(['e'], [('a',n) for n in 'bcde'])
        self.assertEqual(final['failure']['stage'],'final_selection')
        self.assertEqual(final['candidate_hit'],1)
        policy = {**self.policy,'neighbor_budget':1}
        admission = self.evaluate(['f'],[('a','e'),('a','f')],policy)
        self.assertEqual(admission['failure']['stage'],'traversal_admission')
        self.assertEqual(admission['failure']['admission_exclusions'],[{'target_id':'f','neighbor_position':2,'reason':'neighbor_budget','recorded_offer':False}])
        beyond = self.evaluate(['f'],[('a','e'),('e','f')])
        self.assertEqual(beyond['failure']['stage'],'beyond_hop_limit')
        self.assertEqual(beyond['failure']['nearest_target_route'],['a','e','f'])
        self.assertEqual(beyond['failure']['minimum_directed_distance'],2)
        unreachable = self.evaluate(['f'])
        self.assertEqual(unreachable['failure']['stage'],'directed_unreachable')
        self.assertEqual(unreachable['failure']['unreachable_subtype'],'different_weak_components')

    def test_wrong_direction_and_deterministic_shortest_route(self):
        wrong = self.evaluate(['f'],[('f','a')])
        self.assertEqual(wrong['failure']['unreachable_subtype'],'weakly_connected_wrong_direction')
        route = self.evaluate(['h'],[('a','f'),('f','h'),('a','e'),('e','h')])
        self.assertEqual(route['failure']['nearest_target_route'],['a','e','h'])

    def test_changed_prediction_is_refused(self):
        trial = traverse_once(self.nodes,[('a','e')],self.scores,'a',**self.policy)
        trial['offered_ids'] = []
        with self.assertRaisesRegex(ValueError,'saved-prediction-mismatch'):
            sa.freeze_prediction(self.nodes,[('a','e')],self.scores.items(),trial,self.policy)

    def test_unavailable_depth_is_not_censored_as_low_rank(self):
        trial = traverse_once(self.nodes,[],self.scores,'a',**self.policy)
        pred = sa.freeze_prediction(self.nodes,[],self.scores.items(),trial,self.policy)
        with self.assertRaisesRegex(ValueError,'depth-unavailable'):
            sa.evaluate_prediction(self.nodes,[],pred,['h'],self.policy,[16])

    def test_duplicate_text_bootstrap_is_joint_deterministic_query_macro(self):
        rows = []
        for query, text, value in [('q1','same',1),('q2','same',1),('q3','other',-1)]:
            for job, sign in [('v1',1),('v2',-1)]:
                rows.append({'query_id':query,'query_text_group':text,'job_id':job,'eligible':True,
                             **{m:value*sign for m in ('G4_minus_B4','G4_minus_B1','G4_minus_Bm')}})
        out = sa.paired_intervals(rows,job_ids=['v1','v2'],draws=2000,seed=30061029)
        self.assertEqual(out,sa.paired_intervals(rows,job_ids=['v1','v2'],draws=2000,seed=30061029))
        self.assertAlmostEqual(out['v1']['G4_minus_B4']['estimate'],1/3)
        self.assertEqual(out['v1']['G4_minus_B4']['interval_95'],[-1,1])
        self.assertEqual(out['v2']['G4_minus_B4']['estimate'],-out['v1']['G4_minus_B4']['estimate'])

    def test_operator_hand_computable_fixture_and_inventory(self):
        path = Path(__file__).resolve().parents[1] / 'containers/verification/support_access.py'
        spec = importlib.util.spec_from_file_location('support_access_operator_fixture',path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inp = root/'input'; out = root/'output'
            inp.mkdir();out.mkdir();(out/'.retrieval-arena-output-owner').touch()
            (inp/'corpus').mkdir()
            for n in self.nodes: (inp/'corpus'/f'{n}.md').write_text(f'# Page {n}\nExample content for {n}.',encoding='utf-8')
            def write(name,value): (inp/name).write_bytes(canonical(value)+b'\n')
            def jsonl(name,values): (inp/name).write_bytes(b''.join(canonical(v)+b'\n' for v in values))
            write('bundle.json',{'nodes':self.nodes,'edges':[['a','e']]})
            queries=[]; trials=[]; targets=[]; references=[]
            saved=traverse_once(self.nodes,[('a','e')],self.scores,'a',**self.policy)
            for i,target in enumerate('aebf'):
                qid=f'q{i}'; question=f'Question {i}'
                queries.append({'query_id':qid,'question':question,'scores':self.scores})
                trials.append({'job_id':'view','query_id':qid,'question':question,'semantic':saved})
                targets.append({'query_id':qid,'known_target_ids':[target]})
                st=score_trial(saved,[target])
                references.append({'job_id':'view','query_id':qid,'known_target_count_in_view':1,
                    'scores':dict(C_semantic=st['hit'],semantic_seed_hit=st['seed_hit'],semantic_candidate_hit=st['candidate_hit'],
                       semantic_expansion_added_access=st['expansion_added_hit'],semantic_selected_count=st['selected_count'],
                       semantic_candidate_count=st['candidate_count'],semantic_dead_end=st['dead_end'])})
            for name,values in [('queries',queries),('trials',trials),('targets',targets),('references',references)]: jsonl(name+'.jsonl',values)
            config={'schema_version':'retrieval_arena.support_access_config.v1','queries_path':'queries.jsonl',
                'trials_path':'trials.jsonl','targets_path':'targets.jsonl','reference_scores_path':'references.jsonl','corpus_path':'corpus',
                'jobs':[{'job_id':'view','family':'fixture','kind':'full_scope','bundle':'bundle.json','node_ids':self.nodes}],
                'primary_job':'view','traversal':self.policy,'semantic_depths':[1,2,4,8], 'bootstrap_draws':20,
                'bootstrap_seed':30061029,'max_output_bytes':1024**2,'max_runtime_seconds':60}
            write('config.json',config)
            inv=regular_inventory(inp)
            write('operator-request.json',{'schema_version':'retrieval_arena.support_access_operator_request.v1','command':module.COMMAND,
                'config_path':'config.json','input_inventory':inv,'input_inventory_sha256':sha256_bytes(canonical(inv))})
            summary=module.run(inp,out)
            view=summary['views']['view']
            self.assertEqual(view['paired_cells'],dict.fromkeys(sa.CELLS,1))
            self.assertEqual(view['hit_rates']['B1']['numerator'],1)
            self.assertEqual(view['hit_rates']['B4']['numerator'],2)
            self.assertEqual(view['hit_rates']['G4']['numerator'],2)
            self.assertEqual(summary['historical_replay_rows_verified'],4)
            receipt=json.loads((out/'operator_receipt.v1.json').read_bytes())
            actual=receipt['receipt_sha256'];receipt['receipt_sha256']='0'*64
            self.assertEqual(actual,sha256_bytes(canonical(receipt)))
            (inp/'queries.jsonl').write_text('changed',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'input-inventory'): module.run(inp,out)


if __name__ == '__main__': unittest.main()
