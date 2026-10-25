import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from retrieval_arena import answer_element_comparison as aec

# Two tasks of their own (two and three elements), three arms, two conditions, two replicates.
TASKS = {
    't1': {'question': 'How do I rotate the widget key?',
           'elements': [('E1', 'Name the rotate command.', 'Mentions widget rotate.'),
                        ('E2', 'Warn about downtime.', 'Says rotation restarts the widget.')]},
    't2': {'question': 'Which ports does the gadget need?',
           'elements': [('E1', 'List the API port.', 'Port 8080.'),
                        ('E2', 'List the metrics port.', 'Port 9090.'),
                        ('E3', 'Say ports are TCP.', 'States TCP.')]},
}
ARMS, CONDS, REPS = ['X', 'Y', 'Z'], ['c1', 'c2'], [1, 2]
SENTENCES = {('t1', 'E1'): 'Run widget rotate to rotate the key.',
             ('t1', 'E2'): 'Rotation restarts the widget, so expect brief downtime.',
             ('t2', 'E1'): 'The API listens on port 8080.',
             ('t2', 'E2'): 'Metrics are served on port 9090.',
             ('t2', 'E3'): 'Both ports use TCP.'}


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def answer_text(task, satisfied, tag):
    parts = [SENTENCES[(task, e)] for e in sorted(satisfied)]
    return ' '.join(parts + [f'(Answer {tag}.)'])


def default_satisfied(arm, task, cond, rep):
    ids = [e[0] for e in TASKS[task]['elements']]
    if arm == 'X':
        return ids if (cond, rep) != ('c2', 2) else ids[:-1]
    if arm == 'Y':
        return ids[:-1]
    return ids


class Fixture:
    """Writes a config, answer files and judgment records; edits are applied through `tweak`."""

    def __init__(self, root, satisfied=default_satisfied, selection=True):
        self.root = Path(root)
        self.satisfied = satisfied
        self.answers, self.judgments, self.texts = [], [], {}
        tasks = []
        for tid, t in TASKS.items():
            tasks.append({'task_id': tid, 'question': t['question'], 'question_sha256': sha(t['question']),
                          'elements': [{'element_id': e, 'requirement': r, 'acceptance': a} for e, r, a in t['elements']],
                          'references': [{'label': f'{tid}-source', 'path': f'refs/{tid}.md'}]})
            (self.root / 'refs').mkdir(parents=True, exist_ok=True)
            (self.root / f'refs/{tid}.md').write_text(f'Reference page for {tid}.\n', encoding='utf-8')
        (self.root / 'answers').mkdir(exist_ok=True)
        (self.root / 'records').mkdir(exist_ok=True)
        for arm in ARMS:
            for tid, t in TASKS.items():
                for cond in CONDS:
                    for rep in REPS:
                        aid = f'{arm}-{tid}-{cond}-r{rep}'
                        sat = satisfied(arm, tid, cond, rep)
                        text = answer_text(tid, sat, aid.replace('-', ' '))
                        self.texts[aid] = text
                        (self.root / f'answers/{aid}.txt').write_text(text, encoding='utf-8')
                        self.answers.append({'answer_id': aid, 'task_id': tid, 'arm': arm, 'condition': cond,
                                             'replicate': rep, 'terminal': 'answer', 'answer_path': f'answers/{aid}.txt',
                                             'answer_sha256': sha(text), 'question_sha256': sha(t['question'])})
                        verdicts = [{'element_id': e, 'verdict': 'satisfied' if e in sat else 'not_satisfied',
                                     'answer_span': SENTENCES[(tid, e)] if e in sat else None,
                                     'reason': f'Reason text for {aid} element {e} long enough.'}
                                    for e, _, _ in t['elements']]
                        self.write_record(f'j-{aid}', [aid], t, text, verdicts)
        self.config = {'schema_version': aec.CONFIG_SCHEMA, 'arms': ARMS, 'conditions': CONDS, 'replicates': REPS,
                       'contrasts': [['X', 'Y'], ['X', 'Z'], ['Z', 'Y']], 'tasks': tasks,
                       'answers': self.answers, 'judgments': self.judgments}
        if selection:
            self.config['selection'] = {'salt': 'test|1', 'focal': 'X', 'contrasts': [['X', 'Y'], ['X', 'Z']],
                                        'max_pairs': 8, 'forbidden_strings': ['opaque-variant-x']}

    def write_record(self, jid, answer_ids, task, text, verdicts, status='valid', question=None, elements=None):
        record = {'schema_version': aec.RECORD_SCHEMA, 'judgment_id': jid, 'question': question or task['question'],
                  'answer': text, 'status': status, 'selected_attempt': 1 if status == 'valid' else None,
                  'elements': elements or [{'element_id': e, 'requirement': r, 'acceptance': a}
                                           for e, r, a in task['elements']],
                  'raw_verdicts': verdicts if status == 'valid' else None,
                  'strict_verdicts': copy.deepcopy(verdicts) if status == 'valid' else None,
                  'sources': {'fixture': 'none'}}
        (self.root / f'records/{jid}.json').write_text(json.dumps(record), encoding='utf-8')
        self.judgments = [j for j in self.judgments if j['judgment_id'] != jid]
        self.judgments.append({'judgment_id': jid, 'answer_ids': answer_ids, 'record_path': f'records/{jid}.json'})
        if hasattr(self, 'config'):
            self.config['judgments'] = self.judgments

    def record(self, jid):
        return json.loads((self.root / f'records/{jid}.json').read_text(encoding='utf-8'))

    def rewrite(self, jid, record):
        (self.root / f'records/{jid}.json').write_text(json.dumps(record), encoding='utf-8')

    def run(self):
        return aec.run(self.config, self.root)


def row(result, aid, eid):
    return next(r for r in result['rows'] if r['answer_id'] == aid and r['element_id'] == eid)


def answer(result, aid):
    return next(a for a in result['answers'] if a['answer_id'] == aid)


class QuoteRuleTests(unittest.TestCase):
    def test_relaxed_rules_in_order(self):
        text = 'Use **docker compose up** then check [page lines 3-5] the “logs” output.'
        self.assertEqual(aec.relaxed_match('docker compose up', text)[1], 'exact')
        self.assertEqual(aec.relaxed_match('Use docker compose up then check the logs output.', text)[1],
                         'exact_normalized')
        self.assertEqual(aec.relaxed_match('Use docker ... the logs', text)[1], 'ellipsis_pieces')
        self.assertFalse(aec.relaxed_match('the logs ... Use docker', text)[0])
        self.assertEqual(aec.relaxed_match('Use docker compose up then check the logs output. today', text)[1],
                         'word_order_90')
        self.assertEqual(aec.relaxed_match(None, text), (False, None, {'reason': 'no-span'}))
        self.assertEqual(aec.relaxed_match('null', text)[0], False)
        self.assertFalse(aec.relaxed_match('completely different words here', text)[0])

    def test_quote_location(self):
        text = 'alpha beta gamma'
        self.assertEqual(aec.quote_location('beta', text, 'exact'), {'status': 'verbatim', 'char_start': 6, 'char_end': 10})
        self.assertEqual(aec.quote_location('x', text, None), {'status': 'not_located'})
        self.assertEqual(aec.quote_location(None, text, None), {'status': 'no_span'})


class ElementComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rows_cover_every_scheduled_answer_and_element(self):
        res = self.fx.run()
        self.assertEqual(len(res['answers']), 3 * 2 * 2 * 2)
        self.assertEqual(len(res['rows']), 3 * 2 * 2 * (2 + 3))
        self.assertEqual(res['summary']['answers_by_status'], {'judged': 24})
        self.assertEqual(res['issues'], [])

    def test_identity_mismatch_is_not_observed(self):
        rec = self.fx.record('j-X-t1-c1-r1')
        rec['answer'] = rec['answer'] + ' edited'
        self.fx.rewrite('j-X-t1-c1-r1', rec)
        rec = self.fx.record('j-X-t1-c1-r2')
        rec['question'] = 'Another question?'
        self.fx.rewrite('j-X-t1-c1-r2', rec)
        res = self.fx.run()
        self.assertEqual(answer(res, 'X-t1-c1-r1')['judgment_status'], 'identity_mismatch')
        self.assertEqual(row(res, 'X-t1-c1-r1', 'E1')['outcome'], 'not_observed')
        reasons = {i['answer_id']: i['reasons'] for i in res['issues'] if i['issue'] == 'identity_mismatch'}
        self.assertEqual(reasons, {'X-t1-c1-r1': ['answer_text'], 'X-t1-c1-r2': ['question']})

    def test_element_count_mismatch_and_text_change(self):
        rec = self.fx.record('j-Y-t2-c1-r1')
        rec['elements'] = rec['elements'][:2]
        self.fx.rewrite('j-Y-t2-c1-r1', rec)
        rec = self.fx.record('j-Y-t2-c1-r2')
        rec['elements'][0]['acceptance'] = 'Port 8081.'
        self.fx.rewrite('j-Y-t2-c1-r2', rec)
        res = self.fx.run()
        reasons = {i['answer_id']: i['reasons'] for i in res['issues'] if i['issue'] == 'identity_mismatch'}
        self.assertEqual(reasons, {'Y-t2-c1-r1': ['element_ids'], 'Y-t2-c1-r2': ['element_text']})

    def test_alias_one_judgment_for_two_answers(self):
        # Make two answers byte-identical and grade them with one record.
        fx = self.fx
        text = fx.texts['Z-t1-c1-r1']
        (fx.root / 'answers/Z-t1-c1-r2.txt').write_text(text, encoding='utf-8')
        a = next(x for x in fx.answers if x['answer_id'] == 'Z-t1-c1-r2')
        a['answer_sha256'] = sha(text)
        fx.judgments = [j for j in fx.judgments if j['judgment_id'] != 'j-Z-t1-c1-r2']
        fx.config['judgments'] = fx.judgments
        next(j for j in fx.judgments if j['judgment_id'] == 'j-Z-t1-c1-r1')['answer_ids'].append('Z-t1-c1-r2')
        res = fx.run()
        self.assertEqual(answer(res, 'Z-t1-c1-r2')['judgment_id'], 'j-Z-t1-c1-r1')
        self.assertEqual(answer(res, 'Z-t1-c1-r2')['judgment_status'], 'judged')
        # Two answers, one judgment: the replicate count comes from the schedule, not from judgments.
        cell = next(c for c in res['cells'] if (c['task_id'], c['condition'], c['element_id'], c['arm'])
                    == ('t1', 'c1', 'E1', 'Z'))
        self.assertEqual((cell['observed'], cell['scheduled']), (2, 2))

    def test_missing_and_invalid_completions_and_no_answer(self):
        fx = self.fx
        task = {'question': TASKS['t1']['question'], 'elements': TASKS['t1']['elements']}
        fx.write_record('j-X-t1-c1-r1', ['X-t1-c1-r1'], task, fx.texts['X-t1-c1-r1'], None, status='timeout')
        fx.judgments = [j for j in fx.judgments if j['judgment_id'] != 'j-X-t1-c1-r2']
        fx.config['judgments'] = fx.judgments
        a = next(x for x in fx.answers if x['answer_id'] == 'X-t1-c2-r1')
        a.update(terminal='invalid_action_limit', answer_path=None, answer_sha256=None)
        fx.judgments = [j for j in fx.judgments if j['judgment_id'] != 'j-X-t1-c2-r1']
        fx.config['judgments'] = fx.judgments
        res = fx.run()
        self.assertEqual(answer(res, 'X-t1-c1-r1')['judgment_status'], 'no_valid_completion')
        self.assertEqual(answer(res, 'X-t1-c1-r2')['judgment_status'], 'no_judgment')
        self.assertEqual(answer(res, 'X-t1-c2-r1')['judgment_status'], 'no_answer')
        for aid in ('X-t1-c1-r1', 'X-t1-c1-r2', 'X-t1-c2-r1'):
            self.assertIsNone(answer(res, aid)['relaxed_score'])
            self.assertIsNone(row(res, aid, 'E1')['counted'])
        cell = next(c for c in res['cells'] if (c['task_id'], c['condition'], c['element_id'], c['arm'])
                    == ('t1', 'c1', 'E1', 'X'))
        self.assertEqual((cell['observed'], cell['scheduled'], cell['rate'], cell['complete']), (0, 2, None, False))
        con = next(c for c in res['contrasts'] if (c['contrast'], c['task_id'], c['condition'], c['element_id'])
                   == ('X-Y', 't1', 'c1', 'E1'))
        self.assertEqual((con['difference'], con['persistence']), (None, 'incomplete'))

    def test_quote_rejected_is_distinct_from_negative(self):
        rec = self.fx.record('j-Z-t2-c2-r1')
        rec['raw_verdicts'][1]['answer_span'] = 'Metrics live on a port that nobody documents anywhere.'
        self.fx.rewrite('j-Z-t2-c2-r1', rec)
        res = self.fx.run()
        r = row(res, 'Z-t2-c2-r1', 'E2')
        self.assertEqual((r['raw_verdict'], r['outcome'], r['counted'], r['quote_location']),
                         ('satisfied', 'quote_rejected', False, {'status': 'not_located'}))
        self.assertEqual(row(res, 'Y-t2-c2-r1', 'E3')['outcome'], 'judged_not_satisfied')
        self.assertAlmostEqual(answer(res, 'Z-t2-c2-r1')['relaxed_score'], 2 / 3)

    def test_duplicate_and_missing_verdicts(self):
        rec = self.fx.record('j-Z-t2-c1-r1')
        rec['raw_verdicts'] = [rec['raw_verdicts'][0], rec['raw_verdicts'][0], rec['raw_verdicts'][1]]
        self.fx.rewrite('j-Z-t2-c1-r1', rec)
        res = self.fx.run()
        self.assertEqual(row(res, 'Z-t2-c1-r1', 'E1')['judgment_status'], 'verdict_duplicate')
        self.assertEqual(row(res, 'Z-t2-c1-r1', 'E3')['judgment_status'], 'verdict_missing')
        self.assertFalse(answer(res, 'Z-t2-c1-r1')['complete'])
        kinds = sorted(i['issue'] for i in res['issues'])
        self.assertEqual(kinds, ['raw_verdict_duplicate', 'raw_verdict_missing'])

    def test_balanced_aggregation_and_additive_decomposition(self):
        res = self.fx.run()
        # X misses its last element only in c2 r2; Y always misses its last element; Z satisfies everything.
        # t1 (2 elements): X = mean(c1 1, c2 mean(1, .5)) = .875; Y = .5.  t2: X = mean(1, mean(1, 2/3)) = 11/12; Y = 2/3.
        h = res['historical']['rubric_relaxed']['X-Y']
        self.assertAlmostEqual(h['estimate'], ((0.875 - 0.5) + (11 / 12 - 2 / 3)) / 2)
        self.assertEqual(h['n'], 2)
        total = sum(e['contribution_to_overall'] for e in res['elements'] if e['contrast'] == 'X-Y')
        self.assertAlmostEqual(total, h['estimate'])
        task = next(t for t in res['tasks'] if (t['contrast'], t['task_id']) == ('X-Y', 't1'))
        self.assertAlmostEqual(task['mean_difference'], 0.375)
        self.assertEqual((task['elements_favourable'], task['elements_unfavourable']), (1, 0))

    def test_scoring_passes_are_averaged_not_replicates(self):
        grades = {a['answer_id']: [4, 2] if a['arm'] == 'X' else [3, None] for a in self.fx.answers}
        self.fx.config['scores'] = {'completeness': grades}
        res = self.fx.run()
        h = res['historical']['completeness']['X-Y']
        self.assertAlmostEqual(h['estimate'], 0.0)
        self.assertAlmostEqual(h['mean_a'], 3.0)
        cell = next(c for c in res['cells'] if (c['task_id'], c['condition'], c['element_id'], c['arm'])
                    == ('t1', 'c1', 'E1', 'X'))
        self.assertEqual(cell['planned'], 2)

    def test_persistence_classes(self):
        res = self.fx.run()
        get = lambda *k: next(c for c in res['contrasts'] if (c['contrast'], c['task_id'], c['condition'], c['element_id']) == k)
        self.assertEqual(get('X-Y', 't1', 'c1', 'E2')['persistence'], 'persists')
        self.assertEqual(get('X-Y', 't1', 'c2', 'E2')['persistence'], 'one_sided')
        self.assertEqual(get('X-Y', 't1', 'c1', 'E1')['persistence'], 'unchanged')
        self.assertEqual(get('X-Y', 't1', 'c1', 'E1')['shared'], 'shared_satisfied')
        self.assertEqual(get('X-Z', 't1', 'c2', 'E2')['replicate_directions'], {'1': 0, '2': -1})

    def test_reversal_and_shared_omission(self):
        def sat(arm, task, cond, rep):
            ids = [e[0] for e in TASKS[task]['elements']]
            if arm == 'X':
                return ids[:1] if rep == 1 else ids[1:]
            if arm == 'Y':
                return ids[1:] if rep == 1 else ids[:1]
            return ids
        with tempfile.TemporaryDirectory() as tmp:
            res = Fixture(tmp, satisfied=sat).run()
        c = next(c for c in res['contrasts'] if (c['contrast'], c['task_id'], c['condition'], c['element_id'])
                 == ('X-Y', 't1', 'c1', 'E1'))
        self.assertEqual((c['persistence'], c['difference']), ('reverses', 0.0))
        pairs = [p for p in res['pairs'] if p['contrast'] == 'X-Y']
        self.assertEqual({p['stratum'] for p in pairs}, {'tradeoff', 'net_gain', 'net_loss'})

    def test_selection_is_fixed_by_hash_and_balanced(self):
        res = self.fx.run()
        sel = res['selection']
        self.assertLessEqual(len(sel['pairs']), 8)
        for label in ('X-Y', 'X-Z'):
            strata = sel['contrasts'][label]['strata']
            cand = [p for p in res['pairs'] if p['contrast'] == label and p['complete']]
            for s, info in strata.items():
                members = [p for p in cand if p['stratum'] == s]
                self.assertEqual(info['candidates'], len(members))
                if members:
                    best = min(members, key=lambda p: aec.selection_key('test|1', label, p['task_id'], p['condition'], p['replicate']))
                    self.assertEqual((info['selected']['task_id'], info['selected']['condition'], info['selected']['replicate']),
                                     (best['task_id'], best['condition'], best['replicate']))
                else:
                    self.assertIsNone(info['selected'])
            sides = [p['focal_side'] for p in sel['pairs'] if p['contrast'] == label]
            self.assertLessEqual(abs(sides.count('A') - sides.count('B')), 1)
        # X-Y has no net-loss pair in this fixture: the stratum stays empty, nothing substitutes.
        self.assertIsNone(sel['contrasts']['X-Y']['strata']['net_loss']['selected'])

    def test_packet_is_blind_and_key_is_separate(self):
        res = self.fx.run()
        packet, key = res['packet'], res['key']
        text = json.dumps(packet)
        for a in self.fx.answers:
            self.assertNotIn(a['answer_id'], text)
        self.assertNotIn('opaque-variant-x', text)
        self.assertNotIn('Reason text for', text)
        self.assertNotIn('satisfied"', text.replace('"satisfied"', '').replace('"partly satisfied"', '').replace('"not satisfied"', ''))
        self.assertEqual(aec.leakage(packet, ['opaque-variant-x']), [])
        self.assertEqual(res['response']['packet_sha256'], hashlib.sha256(aec.canonical(packet)).hexdigest())
        self.assertTrue(all(e['A']['label'] is None for p in res['response']['pairs'] for e in p['elements']))
        for kp, pp in zip(key['pairs'], packet['pairs']):
            self.assertEqual(kp['pair_id'], pp['pair_id'])
            self.assertEqual(pp['answers']['A'], self.fx.texts[kp['A']['answer_id']])
            self.assertEqual({kp['A']['arm'], kp['B']['arm']}, set(kp['contrast'].split('-')))
        self.assertEqual(packet['pairs'][0]['references'][0]['text'].startswith('Reference page'), True)
        leaky = copy.deepcopy(packet)
        leaky['pairs'][0]['arm'] = 'X'
        self.assertTrue(aec.leakage(leaky))
        leaky = copy.deepcopy(packet)
        leaky['pairs'][0]['answers']['A'] += ' opaque-variant-x'
        self.assertIn('string:opaque-variant-x', aec.leakage(leaky, ['opaque-variant-x']))

    def test_deterministic(self):
        a = aec.canonical({k: v for k, v in self.fx.run().items()})
        b = aec.canonical({k: v for k, v in self.fx.run().items()})
        self.assertEqual(a, b)

    def test_config_refusals(self):
        bad = copy.deepcopy(self.fx.config)
        bad['answers'].append(dict(bad['answers'][0], answer_id='dup-slot'))
        with self.assertRaisesRegex(ValueError, 'duplicate-schedule-slot'):
            aec.validate_config(bad)
        bad = copy.deepcopy(self.fx.config)
        bad['selection']['contrasts'] = [['Z', 'Y']]
        with self.assertRaisesRegex(ValueError, 'selection-contrast'):
            aec.validate_config(bad)
        bad = copy.deepcopy(self.fx.config)
        bad['tasks'][0]['question'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'question-hash'):
            aec.validate_config(bad)
        bad = copy.deepcopy(self.fx.config)
        bad['selection']['max_pairs'] = 9
        with self.assertRaisesRegex(ValueError, 'selection-bound'):
            aec.validate_config(bad)


if __name__ == '__main__':
    unittest.main()
