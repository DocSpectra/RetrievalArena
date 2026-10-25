import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from retrieval_arena import trace_provenance as tp
from retrieval_arena.graph import magnetic_spectrum as ms

SYSTEM = 'You are a test navigator.\n\nCall exactly one function.'
# A corpus of its own: a hub linking to two pages, one page linking onward, and an unlinked page.
PAGES = {
    'hub': ['# Hub', 'See [alpha](alpha.md) and [beta](beta.md).', 'Hub footer.'],
    'alpha': ['# Alpha', 'Alpha explains the widget setting.', 'Alpha links [beta](beta.md).'],
    'beta': ['# Beta', 'Beta intro.', 'The widget timeout is 30 seconds.', 'Beta outro.'],
    'gamma': ['# Gamma', 'Unrelated widget text.'],
}
EDGES = [('hub', 'alpha'), ('hub', 'beta'), ('alpha', 'beta')]
BUDGETS = {'searches': 2, 'reads': 8, 'model_calls': 12, 'history_bytes': None, 'input_tokens': 1000,
           'wall_seconds': 60, 'notes': 2, 'note_chars': 200, 'min_distinct_searches': 1}
QUESTION = 'What is the widget timeout?'


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def file_hash(name):
    return hashlib.sha256(('\n'.join(PAGES[name]) + '\n').encode('utf-8')).hexdigest()


def read_result(doc, start, count, route, source=None, seen=()):
    lines = [{'line': n, 'text': PAGES[doc][n - 1], 'truncated_line': False}
             for n in range(start, min(len(PAGES[doc]), start + count - 1) + 1)]
    already = sum(x['line'] in seen for x in lines)
    out = {'doc_id': doc, 'lines': lines, 'total_lines': len(PAGES[doc]),
           'next_line': lines[-1]['line'] + 1 if lines[-1]['line'] < len(PAGES[doc]) else None,
           'outgoing_links': sorted(t for s, t in EDGES if s == doc), 'file_sha256': file_hash(doc),
           'previously_observed_lines': already, 'newly_observed_lines': len(lines) - already, 'route': route}
    if source:
        out['source_doc_id'] = source
    return out


def build(script, *, follow=True, compact_at=None, retain=None):
    """Forward-build a trace and its events the way the loop writes them: each call's observation, the action,
    the step hash, the tool result. `compact_at` replaces the history with a working-evidence snapshot."""
    links = {s: {t for a, t in EDGES if a == s} for s in PAGES}
    messages = [{'role': 'system', 'content': SYSTEM},
                {'role': 'user', 'content': tp.TASK_MESSAGE.format(question_id='q1', question=QUESTION)}]
    searched, queries, observed, visible, notes = set(), set(), {}, {}, []
    searches = reads = 0
    steps, events, answer = [], [], None
    for call, (action, result) in enumerate(script, 1):
        allowed = ['search'] if searches < BUDGETS['searches'] else []
        if reads < BUDGETS['reads'] and (searched or observed):
            allowed.append('read')
        if follow and reads < BUDGETS['reads'] and any(links[d] for d in observed):
            allowed.append('follow')
        if observed and queries:
            allowed.append('answer')
        if observed and len(notes) < BUDGETS['notes']:
            allowed.append('note')
        observation = {'allowed_actions': allowed, 'budget': {
            'searches_remaining': BUDGETS['searches'] - searches, 'distinct_searches_needed_for_answer':
                max(0, 1 - len(queries)), 'reads_remaining': BUDGETS['reads'] - reads,
            'model_calls_remaining': BUDGETS['model_calls'] - call + 1},
            'search_hit_pages': sorted(searched), 'read_pages': sorted(observed),
            'read_ranges': {d: tp.line_ranges(v) for d, v in visible.items() if v},
            'follow_edges': {d: sorted(links[d]) for d in observed if links[d]} if follow else {}, 'notes': notes}
        messages.append({'role': 'user', 'content': 'Observation: ' + json.dumps(observation, sort_keys=True)})
        if call == compact_at:
            messages, snapshot = tp._compact(messages[:2], steps, observation, retain)
            visible = {d: {n for a, b in r for n in range(a, b + 1)}
                       for d, r in snapshot['observation']['read_ranges'].items()}
            events.append({'event': 'history_compaction', 'call': call,
                           'snapshot_sha256': tp.sha_bytes(tp.canonical(snapshot))})
        events.append({'event': 'call_start', 'call': call, 'history_sha256': tp.sha_bytes(tp.canonical(messages))})
        messages.append({'role': 'assistant', 'content': json.dumps(action, sort_keys=True, ensure_ascii=False)})
        model = {'call_id': f'c{call}'}
        steps.append({'call': call, 'action': action, 'result': result, 'model': model,
                      'history_sha256': tp.sha_bytes(tp.canonical(messages))})
        name = action['action']
        if 'error' not in result:
            if name == 'search':
                searches += 1
                queries.add(action['query'])
                searched.update(h['doc_id'] for h in result['hits'])
            elif name in ('read', 'follow'):
                reads += 1
                got = {x['line'] for x in result['lines']}
                observed.setdefault(result['doc_id'], set()).update(got)
                visible.setdefault(result['doc_id'], set()).update(got)
            elif name == 'note':
                notes.append({'text': action['text'], 'citations': action['citations']})
            elif name == 'answer':
                answer = {'text': action['text'], 'citations': action['citations']}
        messages.append({'role': 'user', 'content': f'Tool result for call_id c{call}: ' +
                         json.dumps(result, sort_keys=True, ensure_ascii=False)})
    graph = 'source,target\n' + ''.join(f'{s},{t}\n' for s, t in EDGES)
    trace = {'schema_version': tp.TRACE_SCHEMA, 'history_compaction_policy': 'working_evidence_v1',
             'question_id': 'q1', 'question': QUESTION, 'question_sha256': sha(QUESTION),
             'graph_sha256': sha(graph), 'corpus_inventory_sha256': tp.sha_bytes(tp.canonical(
                 {d: file_hash(d) for d in sorted(PAGES)})),
             'follow_links': follow, 'budgets': BUDGETS, 'steps': steps,
             'terminal': 'answer' if answer else 'model_call_budget_exhausted', 'answer': answer, 'notes': notes}
    return trace, events, graph


SEARCH_HITS = {'query': 'widget timeout', 'query_terms': ['widget', 'timeout'], 'match_mode': 'bm25_document_v1',
               'hits': [{'doc_id': 'alpha', 'score': 2.0, 'matches': [{'line': 2, 'text': PAGES['alpha'][1],
                                                                      'matched_terms': ['widget']}]},
                        {'doc_id': 'hub', 'score': 1.0, 'matches': []}], 'matching_files': 3}


def main_script():
    return [
        ({'action': 'search', 'query': 'widget timeout'}, SEARCH_HITS),
        ({'action': 'read', 'doc_id': 'hub', 'start_line': 1, 'line_count': 3}, read_result('hub', 1, 3, 'read')),
        # alpha: listed by search with a snippet line before this follow
        ({'action': 'follow', 'source_doc_id': 'hub', 'doc_id': 'alpha', 'start_line': 1, 'line_count': 3},
         read_result('alpha', 1, 3, 'follow', 'hub')),
        # a follow the loop rejects (gamma was never opened)
        ({'action': 'follow', 'source_doc_id': 'gamma', 'doc_id': 'beta', 'start_line': 1, 'line_count': 2},
         {'error': 'link-not-offered-by-read-page'}),
        # beta: first text exposure by follow, but only lines 1-2; the supporting span is line 3
        ({'action': 'follow', 'source_doc_id': 'hub', 'doc_id': 'beta', 'start_line': 1, 'line_count': 2},
         read_result('beta', 1, 2, 'follow', 'hub')),
        # a later read exposes the span; overlapping line 2
        ({'action': 'read', 'doc_id': 'beta', 'start_line': 2, 'line_count': 3},
         read_result('beta', 2, 3, 'read', seen={1, 2})),
        ({'action': 'note', 'text': 'Timeout is 30 s.', 'citations': [{'doc_id': 'beta', 'start_line': 3,
                                                                         'end_line': 3}]},
         {'accepted': True, 'note_index': 1, 'citation_count': 1}),
        ({'action': 'answer', 'text': 'It is 30 seconds.',
          'citations': [{'doc_id': 'beta', 'start_line': 3, 'end_line': 3},
                        {'doc_id': 'beta', 'start_line': 2, 'end_line': 3}]},
         {'accepted': True, 'citation_count': 2}),
    ]


class Fixture:
    def __init__(self, tmp, graph, *, decisions=None):
        self.root = Path(tmp)
        corpus = self.root / 'corpus'
        corpus.mkdir()
        for name, lines in PAGES.items():
            (corpus / f'{name}.md').write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))
        (self.root / 'graph_edges.csv').write_bytes(graph.encode('utf-8'))
        self.variant = tp.Variant(corpus, self.root / 'graph_edges.csv')
        pool = [{'query_id': 'q1', 'doc_id': d, 'page_sha256': file_hash(d)} for d in ('alpha', 'beta')]
        pool.append({'query_id': 'q1', 'doc_id': 'hub', 'page_sha256': '0' * 64})          # stale page bytes
        decisions = decisions or [
            {'query_id': 'q1', 'doc_id': 'beta', 'page_sha256': file_hash('beta'), 'decision': 'supported',
             'judgment_keys': ['k-beta'], 'answering_window_ids': ['beta#L2-4']},
            {'query_id': 'q1', 'doc_id': 'alpha', 'page_sha256': file_hash('alpha'), 'decision': 'not_supported',
             'judgment_keys': ['k-alpha'], 'answering_window_ids': []}]
        judgments = [{'judgment_key': 'k-beta', 'page_sha256': file_hash('beta'), 'window_id': 'beta#L2-4',
                      'verdict': 'answers', 'status': 'ok', 'supporting_span': 'widget   timeout is 30'},
                     {'judgment_key': 'k-alpha', 'page_sha256': file_hash('alpha'), 'window_id': 'alpha#L1-3',
                      'verdict': 'not_answer', 'status': 'ok', 'supporting_span': ''}]
        qrels = [{'query_id': 'q1', 'support_target_id': 'beta', 'relevance': 1}]
        for name, rows in (('pool', pool), ('decisions', decisions), ('judgments', judgments), ('qrels', qrels)):
            (self.root / f'{name}.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
        self.labels = tp.Labels(self.root / 'pool.jsonl', self.root / 'decisions.jsonl',
                                self.root / 'judgments.jsonl', self.root / 'qrels.jsonl')
        self.classes = {('hub', 'alpha'): {'class': 'mapped_baseline', 'new_page_incident': False},
                        ('hub', 'beta'): {'class': 'added', 'new_page_incident': False}}


def cell(trace_record, **extra):
    return dict({'cell_id': 'c1', 'tier': 't2', 'variant_id': 'v1', 'question_id': 'q1',
                 'question_sha256': sha(QUESTION), 'condition': 's1-follow', 'replicate': 1,
                 'follow_links': trace_record['follow_links'], 'trace_sha256': None}, **extra)


class TraceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.trace, self.events, graph = build(main_script())
        self.fx = Fixture(self.tmp.name, graph)

    def tearDown(self):
        self.tmp.cleanup()

    def audit(self, trace=None, events=None, **kw):
        trace = trace or self.trace
        return tp.audit_trace(cell(trace, **kw), trace, events or self.events, self.fx.variant, SYSTEM,
                              edge_classes=self.fx.classes, labels=self.fx.labels)

    def follows(self, actions):
        return [a for a in actions if a['action'] == 'follow']

    def test_replay_verifies_every_call_and_tampering_makes_context_unknown(self):
        t, actions, citations = self.audit()
        self.assertTrue(t['identity_ok'], t['identity'])
        self.assertEqual(t['replay'], 'verified')
        self.assertEqual(t['final_context'], 'verified')
        bad = copy.deepcopy(self.trace)
        bad['steps'][2]['result']['lines'][0]['text'] = '# Alpha (edited)'
        t2, _, cites = self.audit(trace=bad)
        self.assertEqual(t2['replay'], 'unmatched')
        self.assertEqual(t2['first_unmatched_call'], 4)       # the edited result enters call 4's request
        self.assertEqual(t2['final_context'], 'unknown')
        self.assertEqual({c['final_context'] for c in cites if c['kind'] == 'answer'}, {'unknown'})

    def test_failed_follow_is_retained_and_not_counted_as_success(self):
        t, actions, _ = self.audit()
        f = self.follows(actions)
        self.assertEqual([x['status'] for x in f], ['accepted', 'rejected', 'accepted'])
        self.assertEqual(f[1]['error'], 'link-not-offered-by-read-page')
        self.assertEqual(f[1]['edge_class'], 'not_an_edge')
        self.assertEqual((t['follows_attempted'], t['follows_accepted'], t['follows_accepted_added']), (3, 2, 1))

    def test_search_listing_snippet_and_first_text_exposure_are_distinct(self):
        _, actions, _ = self.audit()
        alpha, beta = self.follows(actions)[0], self.follows(actions)[2]
        self.assertTrue(alpha['listed_by_prior_search'])
        self.assertEqual(alpha['snippet_lines_prior'], 1)
        self.assertTrue(alpha['first_returned_lines'])
        self.assertFalse(alpha['first_page_text_exposure'])
        self.assertEqual(alpha['new_lines_beyond_snippets'], 2)
        self.assertFalse(beta['listed_by_prior_search'])
        self.assertTrue(beta['first_page_text_exposure'])
        self.assertEqual((beta['edge_class'], alpha['edge_class']), ('added', 'mapped_baseline'))
        self.assertTrue(beta['source_opened_before'])
        self.assertEqual(beta['menu_status'], 'reconstructed')
        self.assertEqual((beta['menu_edges'], beta['menu_added_edges'], beta['menu_unknown_edges']), (3, 1, 1))
        self.assertTrue(beta['edge_in_menu'])

    def test_overlapping_reads_count_only_new_lines(self):
        _, actions, _ = self.audit()
        read_beta = [a for a in actions if a['action'] == 'read' and a.get('doc_id') == 'beta'][0]
        self.assertFalse(read_beta['first_returned_lines'])
        self.assertEqual((read_beta['returned_line_count'], read_beta['new_lines']), (3, 2))
        self.assertTrue(read_beta['recorded_newly_observed_agrees'])

    def test_page_positive_label_with_unseen_span(self):
        _, actions, _ = self.audit()
        beta = self.follows(actions)[2]
        self.assertEqual(beta['label'], 'supported')
        self.assertTrue(beta['legacy_qrels_positive'])
        self.assertEqual((beta['spans'], beta['spans_mapped']), (1, 1))
        self.assertEqual(beta['span_exposure_this_call'], 'none')
        read_beta = [a for a in actions if a['action'] == 'read' and a.get('doc_id') == 'beta'][0]
        self.assertEqual(read_beta['span_exposure_this_call'], 'full')

    def test_missing_and_mismatched_judgments_are_not_negative(self):
        _, actions, _ = self.audit()
        hub = [a for a in actions if a['action'] == 'read' and a.get('doc_id') == 'hub'][0]
        self.assertEqual(hub['label'], 'identity_mismatch')
        self.assertEqual(self.fx.labels.label('q1', 'gamma', file_hash('gamma'))['label'], 'never_judged')
        self.assertEqual(self.fx.labels.label('q2', 'beta', file_hash('beta'))['label'], 'never_judged')
        self.assertEqual(self.follows(actions)[0]['label'], 'not_supported')

    def test_citation_after_follow_and_later_read_is_not_follow_attributed(self):
        _, _, citations = self.audit()
        answer = [c for c in citations if c['kind'] == 'answer']
        self.assertEqual(answer[0]['first_exposure'], 'read_only')     # line 3 first returned by the read
        self.assertEqual(answer[1]['first_exposure'], 'mixed')         # line 2 by the follow, line 3 by the read
        self.assertEqual(answer[1]['first_exposing_follow_edge_classes'], ['added'])
        self.assertTrue(answer[0]['page_reached_by_follow'] and answer[0]['page_reached_by_read'])
        self.assertEqual(answer[0]['page_first_route'], 'follow')
        self.assertEqual(answer[0]['final_context'], 'full')
        self.assertEqual(answer[0]['span_overlap_cited'], 'full')
        self.assertEqual(answer[0]['label'], 'supported')

    def test_notes_are_rows_of_their_own(self):
        t, _, citations = self.audit()
        notes = [c for c in citations if c['kind'] == 'note']
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['call'], 7)
        self.assertNotIn('final_context', notes[0])
        self.assertEqual(t['final_context_notes'], 1)

    def test_compaction_eviction_is_replayed_and_reported(self):
        # Compact at the answer call keeping only the last successful read (beta 2-4): alpha's lines leave.
        trace, events, _ = build(main_script(), compact_at=8, retain=1)
        t, actions, citations = self.audit(trace=trace, events=events)
        self.assertEqual(t['replay'], 'verified')
        self.assertEqual(t['compaction_retained_reads'], [1])
        alpha, beta = self.follows(actions)[0], self.follows(actions)[2]
        self.assertEqual(alpha['returned_lines_in_final_context'], 'none')
        self.assertEqual(beta['returned_lines_in_final_context'], 'some')      # line 2 kept, line 1 evicted
        self.assertEqual(beta['span_exposure_final_context'], 'full')
        self.assertEqual(t['final_context_lines'], 3)
        # A recorded snapshot hash no retained count reproduces: history unavailable from there on.
        broken = [dict(e, snapshot_sha256='f' * 64) if e['event'] == 'history_compaction' else e for e in events]
        t2, _, cites = self.audit(trace=trace, events=broken)
        self.assertEqual((t2['replay'], t2['first_unmatched_call'], t2['final_context']), ('unmatched', 8, 'unknown'))

    def test_question_and_graph_identity_checks(self):
        t, _, _ = self.audit(question_sha256=sha('another question'))
        self.assertFalse(t['identity']['question_sha256'])
        self.assertFalse(t['identity_ok'])
        bad = dict(self.trace, graph_sha256='0' * 64)
        t2, _, _ = self.audit(trace=bad)
        self.assertFalse(t2['identity']['graph_file_sha256'])

    def test_locate_span_exact_whitespace_and_unmappable(self):
        lines = PAGES['beta']
        self.assertEqual(tp.locate_span(lines, 'timeout is 30', (1, 4)), {3})
        self.assertEqual(tp.locate_span(lines, 'intro.\nThe widget', (1, 4)), {2, 3})
        self.assertEqual(tp.locate_span(lines, 'intro.   The   widget', (1, 4)), {2, 3})
        self.assertIsNone(tp.locate_span(lines, 'timeout is 30', (1, 2)))
        self.assertIsNone(tp.locate_span(lines, 'not in the page', (1, 4)))
        self.assertEqual(tp.span_exposure([{'window_id': 'w', 'lines': None}], {1}), 'unmappable_only')
        self.assertEqual(tp.span_exposure([], {1}), 'no_valid_span')

    def test_no_follow_condition_has_empty_menu_and_zero_follows(self):
        script = [s for s in main_script() if s[0]['action'] != 'follow']
        script[2] = ({'action': 'read', 'doc_id': 'beta', 'start_line': 2, 'line_count': 3},
                     {'error': 'page-not-offered-by-search'})
        trace, events, _ = build(script, follow=False)
        t, actions, _ = self.audit(trace=trace, events=events, condition='s1-no-follow')
        self.assertEqual(t['replay'], 'verified')
        self.assertTrue(t['zero_follow'])
        self.assertEqual(t['follows_attempted'], 0)

    def test_summary_counts_by_group(self):
        t, actions, citations = self.audit()
        s = tp.summarize([t], actions, citations)
        g = s['t2/v1/s1-follow']
        self.assertEqual(g['actions']['follow:accepted'], 2)
        self.assertEqual(g['actions']['follow:rejected'], 1)
        self.assertEqual(g['follows']['edge_class'], {'added': 1, 'mapped_baseline': 1, 'not_an_edge': 1})
        self.assertEqual(g['citations']['answer:first_exposure'], {'mixed': 1, 'read_only': 1})

    def test_run_jobs_quarantines_changed_trace_bytes_and_joins_views(self):
        root = self.fx.root
        (root / 't.json').write_text(json.dumps(self.trace), encoding='utf-8')
        (root / 't.events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in self.events), encoding='utf-8')
        (root / 'system.txt').write_bytes(SYSTEM.encode('utf-8'))
        nodes = sorted(PAGES)
        admitted = ms.admit_graph(nodes, EDGES)
        bundle = {'schema_version': ms.BUNDLE_SCHEMA, 'graph_id': 'g', 'nodes': admitted['nodes'],
                  'edges': [list(e) for e in admitted['edges']],
                  'canonical_graph_sha256': ms.graph_sha256(admitted['nodes'], admitted['edges'])}
        (root / 'bundle.json').write_text(json.dumps(bundle), encoding='utf-8')
        (root / 'edges.jsonl').write_text(''.join(json.dumps({'source': s, 'target': t, 'class': c})
                                                  + '\n' for (s, t), c in (
                                                      (('hub', 'alpha'), 'mapped_baseline'),
                                                      (('hub', 'beta'), 'added'),
                                                      (('gamma', 'hub'), 'added'))), encoding='utf-8')
        good = cell(self.trace, trace='t.json', events='t.events.jsonl', labels=True,
                    trace_sha256=tp.sha_bytes((root / 't.json').read_bytes()))
        config = {'schema_version': tp.CONFIG_SCHEMA, 'dialect': tp.DIALECTS[0], 'system_message': 'system.txt',
                  'variants': [{'variant_id': 'v1', 'corpus': 'corpus', 'graph': 'graph_edges.csv',
                                'edge_classes': 'edges.jsonl',
                                'labels': {'pool': 'pool.jsonl', 'page_decisions': 'decisions.jsonl',
                                           'judgments': 'judgments.jsonl', 'qrels': 'qrels.jsonl'},
                                'views': [{'view_id': 'full', 'bundle': 'bundle.json', 'view': {'kind': 'full'}},
                                          {'view_id': 'lwcc', 'bundle': 'bundle.json', 'view': {'kind': 'lwcc'}}]}],
                  'cells': [good, dict(good, cell_id='c2', trace_sha256='0' * 64)]}
        traces, actions, citations, variants = tp.run_jobs(config, root)
        self.assertEqual(traces[1], {'cell_id': 'c2', 'tier': 't2', 'variant_id': 'v1', 'question_id': 'q1',
                                     'condition': 's1-follow', 'replicate': 1, 'quarantined': 'trace-bytes-mismatch'})
        self.assertEqual(traces[0]['replay'], 'verified')
        self.assertEqual(variants[0]['edge_classes']['rows_outside_variant_graph'], 1)
        ident = variants[0]['graph_identity'][0]
        self.assertEqual((ident['edges_only_agent'], ident['edges_only_bundle'], ident['nodes_only_agent']), (0, 0, 0))
        beta = [a for a in actions if a['action'] == 'follow' and a['status'] == 'accepted'][1]
        self.assertEqual(beta['views']['lwcc'], {'edge_in_view': True, 'source_in_view': True, 'target_in_view': True})
        self.assertEqual(sorted(tp.input_paths(config)), sorted(
            ['system.txt', 'graph_edges.csv', 'edges.jsonl', 'pool.jsonl', 'decisions.jsonl', 'judgments.jsonl',
             'qrels.jsonl', 'bundle.json', 't.json', 't.events.jsonl']))
        with self.assertRaises(ValueError):
            tp.validate_config(dict(config, dialect='other'))


if __name__ == '__main__':
    unittest.main()
