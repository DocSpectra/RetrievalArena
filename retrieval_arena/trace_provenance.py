"""Retrospective provenance audit of bounded search/read/follow agent traces, a versioned offline consumer.

The contract is docs/evaluations/trace_provenance_audit.md. It reads saved traces, their event logs, the exact
variant corpus and link graph the agent read, an optional edge-class table (mapped baseline versus added), optional
pooled-support outputs of this repository (pool, page decisions, judgments) and optional graph bundles, and writes
one row per action, per citation and per trace. Every field is an observation about the saved record:
- an action is accepted, executed with a tool error, rejected or indeterminate by action *and* result;
- exposure is route-relative (listed by an earlier search, snippet text shown, lines returned, lines new);
- a page judgment is joined only on matching query id and page bytes, and a missing row is never negative;
- a judged supporting span is located in the page and compared with returned and final-context lines;
- the message history of every call is rebuilt under the declared dialect and admitted only when its SHA-256
  equals the recorded `history_sha256`; otherwise the context is `unknown`.
No field is a retrieval or answer score, a measure of reliance, or evidence that a link was necessary.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import re

from retrieval_arena.graph import magnetic_spectrum as ms

VERSION = 'trace_provenance_audit.v1'
CONFIG_SCHEMA = 'retrieval_arena.trace_provenance_config.v1'
DIALECTS = ('docspectra_complex_task_agent_trace_v2',)
TRACE_SCHEMA = 'docspectra.icsc.complex_task_agent_trace.v2'
HISTORY_POLICIES = ('working_evidence_v1',)
TASK_MESSAGE = ('Task ID: {question_id}\nTask: {question}\n'
                'Choose one action. Your tools and remaining budgets are in each observation.')
ACTION_STATUSES = ('accepted', 'executed_tool_error', 'rejected', 'indeterminate')
LABELS = ('supported', 'not_supported', 'undetermined', 'never_judged', 'identity_mismatch', 'not_applicable')
SPAN_EXPOSURE = ('full', 'partial', 'none', 'unmappable_only', 'no_valid_span', 'not_applicable')
EDGE_CLASSES = ('mapped_baseline', 'added', 'unknown')
REPLAY_POLICY = ('messages rebuilt from the system message file, the task template, each observation (allowed '
                 'actions, budgets, search-hit pages, read pages, read ranges, follow menu, notes; json sort_keys, '
                 'ASCII), each assistant action and tool result (json sort_keys, non-ASCII kept); a recorded '
                 'history_compaction is rebuilt as the working-evidence snapshot for the retained-read count whose '
                 'snapshot SHA-256 equals the recorded one; admitted per call only when SHA-256 of the canonical '
                 'message list equals the recorded call_start and step history_sha256')
SPAN_POLICY = ('a supporting span is a judgment row of the joined page bytes with verdict answers, status ok and '
               'a non-empty supporting_span; it is located as an exact substring of the page text (lines joined '
               'by newline) inside its window lines, else after collapsing whitespace; the first occurrence is '
               'used; a span not found is unmappable')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def code_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def line_ranges(numbers):
    ranges = []
    for number in sorted(numbers):
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ranges


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


# ------------------------------------------------------------------ variant (what the agent read)

class Variant:
    """The corpus and link graph exactly as the agent loop loaded them (stems, lines, file hashes, links)."""

    def __init__(self, corpus_root, graph_path):
        paths = sorted(Path(corpus_root).glob('*.md'))
        if not paths:
            raise ValueError('trace-provenance-empty-corpus')
        self.lines = {p.stem: p.read_text(encoding='utf-8').splitlines() for p in paths}
        self.file_hashes = {p.stem: sha_bytes(p.read_bytes()) for p in paths}
        raw = Path(graph_path).read_bytes()
        self.graph_file_sha256 = sha_bytes(raw)
        self.corpus_inventory_sha256 = sha_bytes(canonical(self.file_hashes))
        self.links = {d: set() for d in self.lines}
        rows = 0
        for row in csv.DictReader(io.StringIO(raw.decode('utf-8'), newline='')):
            rows += 1
            s, t = row['source'], row['target']
            if s in self.lines and t in self.lines and s != t:
                self.links[s].add(t)
        self.graph_rows = rows
        self.edges = {(s, t) for s, ts in self.links.items() for t in ts}


def load_edge_classes(path, variant):
    """{(source, target): {"class", "new_page_incident"}} over the variant graph; edges absent from the table are
    `unknown`. Rows naming an arc outside the variant graph are counted, never admitted."""
    table, outside = {}, 0
    for row in jsonl(path):
        edge = (row['source'], row['target'])
        if row['class'] not in EDGE_CLASSES[:2]:
            raise ValueError('trace-provenance-edge-class')
        if edge not in variant.edges:
            outside += 1
            continue
        table[edge] = {'class': row['class'], 'new_page_incident': bool(row.get('new_page_incident'))}
    return table, {'rows_outside_variant_graph': outside, 'classified': len(table),
                   'variant_edges': len(variant.edges), 'unclassified': len(variant.edges - set(table))}


# ------------------------------------------------------------------ pooled-support labels and spans

def _window_lines(window_id, total):
    tail = window_id.rsplit('#', 1)[-1]
    if tail == 'page':
        return 1, total
    match = re.fullmatch(r'L(\d+)-(\d+)', tail)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def locate_span(lines, span, window):
    """Line numbers covered by `span` inside the window (first occurrence), or None when not found."""
    start, end = window
    start, end = max(1, start), min(len(lines), end)
    if start > end:
        return None
    text = '\n'.join(lines[start - 1:end])
    offsets, pos = [], 0
    for number in range(start, end + 1):
        offsets.append((pos, number))
        pos += len(lines[number - 1]) + 1
    at = text.find(span)
    if at >= 0:
        first, last = at, at + len(span) - 1
    else:
        # Collapse whitespace on both sides and map back through an index of kept characters.
        kept, index, prev_space = [], [], False
        for i, ch in enumerate(text):
            if ch.isspace():
                if prev_space or not kept:
                    continue
                kept.append(' ')
                index.append(i)
                prev_space = True
            else:
                kept.append(ch)
                index.append(i)
                prev_space = False
        squeezed = ''.join(kept)
        needle = ' '.join(span.split())
        hit = squeezed.find(needle) if needle else -1
        if hit < 0:
            return None
        first, last = index[hit], index[hit + len(needle) - 1]
    covered = [n for (o, n) in offsets if o <= last and o + len(lines[n - 1]) >= first]
    return set(covered) if covered else None


class Labels:
    """Latest pooled-support decisions of one variant, joined on (query, doc) and page bytes."""

    def __init__(self, pool_path, decisions_path, judgments_path, qrels_path=None):
        self.pool = {(r['query_id'], r['doc_id']): r['page_sha256'] for r in jsonl(pool_path)}
        self.decisions = {}
        for r in jsonl(decisions_path):
            key = (r['query_id'], r['doc_id'])
            if self.pool.get(key) == r['page_sha256']:
                self.decisions[key] = r
        self.judgments = defaultdict(list)
        for r in jsonl(judgments_path):
            self.judgments[r['judgment_key']].append(r)
        self.qrels_positive = set()
        if qrels_path:
            for r in jsonl(qrels_path):
                if r['relevance'] > 0:
                    self.qrels_positive.add((r['query_id'], r['support_target_id']))
        self.files = {'pool_sha256': sha_bytes(Path(pool_path).read_bytes()),
                      'decisions_sha256': sha_bytes(Path(decisions_path).read_bytes()),
                      'judgments_sha256': sha_bytes(Path(judgments_path).read_bytes()),
                      'qrels_sha256': sha_bytes(Path(qrels_path).read_bytes()) if qrels_path else None}

    def label(self, query_id, doc_id, page_sha256):
        key = (query_id, doc_id)
        if key not in self.pool:
            return {'label': 'never_judged', 'reason': 'not-pooled'}
        if self.pool[key] != page_sha256:
            return {'label': 'identity_mismatch', 'reason': 'pool-page-bytes-differ'}
        decision = self.decisions.get(key)
        if decision is None:
            return {'label': 'never_judged', 'reason': 'pooled-without-decision'}
        return {'label': decision['decision'], 'reason': None,
                'judgment_provenance_sha256': decision.get('judgment_provenance_sha256'),
                'answering_window_ids': decision.get('answering_window_ids') or []}

    def spans(self, query_id, doc_id, page_sha256, lines):
        """Located supporting spans of the joined decision: [{"window_id", "lines"|None}]."""
        decision = self.decisions.get((query_id, doc_id))
        if decision is None or decision['page_sha256'] != page_sha256:
            return []
        out = []
        for key in decision.get('judgment_keys') or []:
            for j in self.judgments.get(key, []):
                if (j.get('page_sha256') != page_sha256 or j.get('verdict') != 'answers'
                        or j.get('status') != 'ok' or not j.get('supporting_span')):
                    continue
                window = _window_lines(j['window_id'], len(lines))
                located = locate_span(lines, j['supporting_span'], window) if window else None
                out.append({'window_id': j['window_id'], 'lines': sorted(located) if located else None})
        return out


def span_exposure(spans, shown):
    """Compare located spans with a set of line numbers shown (returned or in the final context)."""
    if not spans:
        return 'no_valid_span'
    mapped = [set(s['lines']) for s in spans if s['lines']]
    if not mapped:
        return 'unmappable_only'
    if any(m <= shown for m in mapped):
        return 'full'
    if any(m & shown for m in mapped):
        return 'partial'
    return 'none'


# ------------------------------------------------------------------ history replay (dialect)

def _compact(pinned, steps, observation, retain):
    """The working-evidence snapshot for `retain` most recent successful read/follow calls."""
    evidence, actions = {}, []
    read_calls = [s['call'] for s in steps if (s.get('action') or {}).get('action') in {'read', 'follow'}
                  and 'error' not in s['result']]
    kept = set(read_calls[-retain:] if retain else [])
    for step in steps:
        action, result, model = step.get('action') or {}, step['result'], step.get('model') or {}
        name = action.get('action')
        entry = {'call': step['call'], 'action': action}
        if model.get('call_id'):
            entry['call_id'] = model['call_id']
        if 'error' in result:
            entry['result'] = {'error': result['error']}
        elif name == 'search':
            entry['result'] = {'hits': [{'doc_id': h['doc_id'],
                                         'matches': [{'line': m['line'], 'text': m['text'][:120]}
                                                     for m in h.get('matches', [])[:1]]}
                                        for h in result.get('hits', [])]}
        elif name in {'read', 'follow'}:
            doc_id = result['doc_id']
            record = evidence.setdefault(doc_id, {'doc_id': doc_id, 'file_sha256': result['file_sha256'], 'lines': {}})
            if step['call'] in kept:
                for line in result['lines']:
                    existing = record['lines'].get(line['line'])
                    if existing is None or len(line['text']) > len(existing):
                        record['lines'][line['line']] = line['text']
            entry['result'] = {'doc_id': doc_id, 'read_lines': [x['line'] for x in result['lines']],
                               'route': result.get('route'), 'source_doc_id': result.get('source_doc_id')}
        else:
            entry['result'] = result
        actions.append(entry)
    ranges = {d: line_ranges(set(r['lines'])) for d, r in evidence.items() if r['lines']}
    snapshot = {'policy': 'working_evidence_v1', 'actions': actions,
                'evidence': [{'doc_id': d, 'file_sha256': r['file_sha256'],
                              'lines': [{'line': n, 'text': v} for n, v in sorted(r['lines'].items())]}
                             for d, r in sorted(evidence.items()) if r['lines']],
                'observation': {**observation, 'read_ranges': ranges}}
    history = [*pinned, {'role': 'user', 'content': 'Session snapshot: ' +
                         json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(',', ':'))}]
    return history, snapshot


def _accepted(step):
    """Did the loop's state change for this step? (search error results still count as a search)."""
    action, result = step.get('action') or {}, step['result']
    name = action.get('action')
    if name == 'search':
        return 'query' in result and 'hits' in result
    return 'error' not in result and name in {'read', 'follow', 'note', 'answer'}


def replay(trace, events, system_message, links):
    """Rebuild every call's messages; returns per-call records and the loop state needed by the audit.

    Per call: `status` verified | unmatched | not_reached, the retained-read count of a compaction, the follow menu,
    and the original lines and search snippets present in that call's request messages."""
    budgets = trace['budgets']
    starts, compactions = {}, {}
    for event in events:
        if event.get('event') == 'call_start':
            starts[event['call']] = event['history_sha256']
        elif event.get('event') == 'history_compaction':
            compactions[event['call']] = event
    messages = [{'role': 'system', 'content': system_message},
                {'role': 'user', 'content': TASK_MESSAGE.format(question_id=trace['question_id'],
                                                                question=trace['question'])}]
    searched, queries, observed, visible, notes = set(), set(), {}, {}, []
    searches = reads = 0
    done, calls = [], []
    # What the request messages carry: (call, doc) -> lines of tool results appended since the last snapshot.
    carried_lines, carried_snippets = defaultdict(set), defaultdict(set)
    snapshot_lines, snapshot_snippets = defaultdict(set), defaultdict(set)
    broken = None
    for step in trace['steps']:
        call = step['call']
        allowed = []
        if searches < budgets['searches']:
            allowed.append('search')
        if reads < budgets['reads'] and (searched or observed):
            allowed.append('read')
        if trace['follow_links'] and reads < budgets['reads'] and any(links.get(d) for d in observed):
            allowed.append('follow')
        if observed and (len(queries) >= budgets['min_distinct_searches'] or searches >= budgets['searches']):
            allowed.append('answer')
        if observed and len(notes) < budgets['notes']:
            allowed.append('note')
        menu = ({d: sorted(links[d]) for d in observed if links.get(d)} if trace['follow_links'] else {})
        observation = {'allowed_actions': allowed, 'budget': {
            'searches_remaining': budgets['searches'] - searches,
            'distinct_searches_needed_for_answer': max(0, budgets['min_distinct_searches'] - len(queries)),
            'reads_remaining': budgets['reads'] - reads,
            'model_calls_remaining': budgets['model_calls'] - call + 1},
            'search_hit_pages': sorted(searched), 'read_pages': sorted(observed),
            'read_ranges': {d: line_ranges(v) for d, v in visible.items() if v},
            'follow_edges': menu, 'notes': notes}
        record = {'call': call, 'allowed_actions': allowed, 'follow_menu': menu,
                  'notes_in_observation': len(notes), 'compaction_retained_reads': None}
        if broken is not None:
            record['status'] = 'not_reached'
            calls.append(record)
            continue
        messages.append({'role': 'user', 'content': 'Observation: ' + json.dumps(observation, sort_keys=True)})
        if call in compactions:
            count = sum((s.get('action') or {}).get('action') in {'read', 'follow'} and 'error' not in s['result']
                        for s in done)
            found = None
            for retain in range(count, -1, -1):
                history, snapshot = _compact(messages[:2], done, observation, retain)
                if sha_bytes(canonical(snapshot)) == compactions[call].get('snapshot_sha256'):
                    found = (retain, history, snapshot)
                    break
            if found is None:
                broken = call
                record['status'] = 'unmatched'
                record['reason'] = 'compaction-snapshot-unmatched'
                calls.append(record)
                continue
            retain, messages, snapshot = found
            record['compaction_retained_reads'] = retain
            visible = {d: {n for a, b in r for n in range(a, b + 1)}
                       for d, r in snapshot['observation']['read_ranges'].items()}
            snapshot_lines = defaultdict(set, {e['doc_id']: {x['line'] for x in e['lines']} for e in snapshot['evidence']})
            snapshot_snippets = defaultdict(set)
            for entry in snapshot['actions']:
                for hit in (entry.get('result') or {}).get('hits', []) or []:
                    snapshot_snippets[hit['doc_id']].update(m['line'] for m in hit.get('matches', []))
            carried_lines, carried_snippets = defaultdict(set), defaultdict(set)
        start_ok = sha_bytes(canonical(messages)) == starts.get(call)
        record['request_lines'] = {d: sorted(snapshot_lines[d] | carried_lines[d])
                                   for d in set(snapshot_lines) | set(carried_lines) if snapshot_lines[d] | carried_lines[d]}
        record['request_snippet_lines'] = {d: sorted(snapshot_snippets[d] | carried_snippets[d])
                                           for d in set(snapshot_snippets) | set(carried_snippets)
                                           if snapshot_snippets[d] | carried_snippets[d]}
        record['request_notes'] = len(notes)
        action = step.get('action')
        if action is not None:
            messages.append({'role': 'assistant', 'content': json.dumps(action, sort_keys=True, ensure_ascii=False)})
        step_ok = sha_bytes(canonical(messages)) == step.get('history_sha256')
        record['status'] = 'verified' if start_ok and step_ok else 'unmatched'
        if record['status'] == 'unmatched':
            record['reason'] = 'call-start-hash' if not start_ok else 'step-hash'
            broken = call
            calls.append(record)
            continue
        calls.append(record)
        if action is None:
            break
        result = step['result']
        name = action.get('action')
        if _accepted(step):
            if name == 'search':
                searches += 1
                queries.add(action['query'].casefold().strip())
                searched.update(h['doc_id'] for h in result['hits'])
                for h in result['hits']:
                    carried_snippets[h['doc_id']].update(m['line'] for m in h.get('matches', []))
            elif name in ('read', 'follow'):
                reads += 1
                got = {x['line'] for x in result['lines']}
                observed.setdefault(result['doc_id'], set()).update(got)
                visible.setdefault(result['doc_id'], set()).update(got)
                carried_lines[result['doc_id']].update(got)
            elif name == 'note':
                notes.append({'text': action['text'], 'citations': action['citations']})
        done.append(step)
        model = step.get('model') or {}
        prefix = ('Tool result for call_id ' + model['call_id'] + ': ' if isinstance(model.get('call_id'), str)
                  else 'Tool result: ')
        messages.append({'role': 'user', 'content': prefix + json.dumps(result, sort_keys=True, ensure_ascii=False)})
    status = ('verified' if broken is None and all(c['status'] == 'verified' for c in calls) else 'unmatched')
    return {'status': status, 'first_unmatched_call': broken, 'calls': calls,
            'compactions_recorded': len(compactions)}


# ------------------------------------------------------------------ one trace

def _status(step):
    action, result = step.get('action'), step.get('result') or {}
    if not isinstance(action, dict) or not isinstance(result, dict):
        return 'indeterminate', None
    name = action.get('action')
    if name == 'search' and 'query' in result and 'hits' in result:
        return ('executed_tool_error', result['error']) if 'error' in result else ('accepted', None)
    if 'error' in result:
        return 'rejected', result['error']
    if name in ('read', 'follow') and 'lines' in result:
        return 'accepted', None
    if name in ('note', 'answer') and result.get('accepted') is True:
        return 'accepted', None
    return 'indeterminate', None


def audit_trace(cell, trace, events, variant, system_message, *, edge_classes=None, labels=None, views=None):
    """Rows for one trace: (trace_row, action_rows, citation_rows). `cell` carries the declared identity."""
    keys = {k: cell[k] for k in ('cell_id', 'tier', 'variant_id', 'question_id', 'condition', 'replicate')}
    identity = {
        'trace_schema': trace.get('schema_version') == TRACE_SCHEMA,
        'history_policy': trace.get('history_compaction_policy') in HISTORY_POLICIES,
        'question_id': trace.get('question_id') == cell['question_id'],
        'question_sha256': (trace.get('question_sha256') == cell['question_sha256'] ==
                            sha_bytes(str(trace.get('question', '')).encode('utf-8'))),
        'graph_file_sha256': trace.get('graph_sha256') == variant.graph_file_sha256,
        'corpus_inventory_sha256': trace.get('corpus_inventory_sha256') == variant.corpus_inventory_sha256,
        'follow_condition': trace.get('follow_links') == cell['follow_links'],
    }
    rep = (replay(trace, events, system_message, variant.links) if identity['trace_schema'] and identity['history_policy']
           else {'status': 'unmatched', 'first_unmatched_call': None, 'calls': [], 'compactions_recorded': None})
    by_call = {c['call']: c for c in rep['calls']}
    qid = cell['question_id']
    listed, snippet_lines, returned = set(), defaultdict(set), defaultdict(set)
    first_route = {}                      # (doc, line) -> (call, route, source)
    page_routes = defaultdict(set)
    reached_by = {}                       # doc -> first (call, route)
    actions = []
    answer_call = None
    for step in trace['steps']:
        call = step['call']
        status, error = _status(step)
        action = step.get('action') if isinstance(step.get('action'), dict) else {}
        name = action.get('action')
        result = step.get('result') if isinstance(step.get('result'), dict) else {}
        row = dict(keys, call=call, action=name, status=status, error=error, replay=by_call.get(call, {}).get('status'))
        if name == 'search':
            row['query'] = action.get('query')
            if status in ('accepted', 'executed_tool_error'):
                hits = result.get('hits', [])
                row['hit_doc_ids'] = [h['doc_id'] for h in hits]
                for h in hits:
                    listed.add(h['doc_id'])
                    snippet_lines[h['doc_id']].update(m['line'] for m in h.get('matches', []))
        elif name in ('read', 'follow'):
            doc = action.get('doc_id')
            row['doc_id'] = doc
            if name == 'follow':
                source = action.get('source_doc_id')
                edge = (source, doc)
                prior_pages = {d for d in returned if returned[d]}
                cls = (edge_classes or {}).get(edge)
                menu = by_call.get(call, {}).get('follow_menu')
                menu_edges = [(s, t) for s, ts in (menu or {}).items() for t in ts] if menu is not None else None
                row.update(source_doc_id=source, source_opened_before=source in prior_pages,
                           edge_in_variant_graph=edge in variant.edges,
                           edge_class=(cls['class'] if cls else 'unknown') if edge in variant.edges else 'not_an_edge',
                           new_page_incident=cls['new_page_incident'] if cls else None,
                           menu_status='reconstructed' if menu_edges is not None and by_call.get(call, {}).get(
                               'status') == 'verified' else 'unknown',
                           menu_edges=len(menu_edges) if menu_edges is not None else None,
                           menu_added_edges=(sum((edge_classes or {}).get(e, {}).get('class') == 'added'
                                                 for e in menu_edges) if menu_edges is not None and edge_classes
                                             else None),
                           menu_unknown_edges=(sum(e not in (edge_classes or {}) for e in menu_edges)
                                               if menu_edges is not None and edge_classes is not None else None),
                           menu_sources=len(menu) if menu is not None else None,
                           edge_in_menu=(edge in set(menu_edges)) if menu_edges is not None else None)
                if views:
                    row['views'] = {v['view_id']: {'edge_in_view': edge in v['edges'],
                                                   'source_in_view': source in v['nodes'],
                                                   'target_in_view': doc in v['nodes']} for v in views}
            row['listed_by_prior_search'] = doc in listed
            row['snippet_lines_prior'] = len(snippet_lines.get(doc, ()))
            row['lines_returned_prior'] = len(returned.get(doc, ()))
            if status == 'accepted':
                got = [x['line'] for x in result['lines']]
                gotset = set(got)
                prior = returned[doc]
                new = gotset - prior
                row.update(returned_ranges=line_ranges(gotset), returned_line_count=len(got),
                           returned_text_sha256=sha_bytes(canonical(result['lines'])),
                           file_sha256=result.get('file_sha256'),
                           file_identity=result.get('file_sha256') == variant.file_hashes.get(doc),
                           text_identity=all(variant.lines.get(doc, [])[x['line'] - 1].startswith(x['text'])
                                             if 0 < x['line'] <= len(variant.lines.get(doc, [])) else False
                                             for x in result['lines']),
                           first_returned_lines=not prior,
                           first_page_text_exposure=not prior and not snippet_lines.get(doc),
                           new_lines=len(new), new_lines_beyond_snippets=len(new - snippet_lines.get(doc, set())),
                           recorded_newly_observed=result.get('newly_observed_lines'),
                           recorded_newly_observed_agrees=result.get('newly_observed_lines') == len(
                               [x for x in got if x not in prior]))
                if labels is not None:
                    lab = labels.label(qid, doc, result.get('file_sha256'))
                    row['label'] = lab['label']
                    row['legacy_qrels_positive'] = (qid, doc) in labels.qrels_positive
                    if lab['label'] == 'supported':
                        spans = labels.spans(qid, doc, result.get('file_sha256'), variant.lines.get(doc, []))
                        row['spans'] = len(spans)
                        row['spans_mapped'] = sum(bool(s['lines']) for s in spans)
                        row['span_exposure_this_call'] = span_exposure(spans, gotset)
                        row['span_exposure_new_lines'] = span_exposure(spans, new)
                    else:
                        row['span_exposure_this_call'] = 'not_applicable'
                else:
                    row['label'] = 'not_applicable'
                for n in got:
                    first_route.setdefault((doc, n), (call, name, action.get('source_doc_id')))
                page_routes[doc].add(name)
                reached_by.setdefault(doc, (call, name))
                returned[doc] |= gotset
        elif name == 'answer' and status == 'accepted':
            answer_call = call
        actions.append(row)
    final = by_call.get(answer_call) if answer_call is not None else None
    final_ok = final is not None and final.get('status') == 'verified'
    final_lines = {d: set(v) for d, v in final['request_lines'].items()} if final_ok else None
    citations = []
    sources = []
    if trace.get('answer'):
        sources.append(('answer', answer_call, trace['answer'].get('citations') or []))
    note_calls = [s['call'] for s in trace['steps'] if (s.get('action') or {}).get('action') == 'note'
                  and _status(s)[0] == 'accepted']
    for index, note in enumerate(trace.get('notes') or []):
        sources.append(('note', note_calls[index] if index < len(note_calls) else None, note.get('citations') or []))
    for kind, at_call, cites in sources:
        for i, c in enumerate(cites):
            doc, a, b = c.get('doc_id'), c.get('start_line'), c.get('end_line')
            cited = set(range(a, b + 1)) if isinstance(a, int) and isinstance(b, int) and a <= b else set()
            firsts = {first_route.get((doc, n)) for n in cited}
            routes = {f[1] for f in firsts if f}
            follow_sources = sorted({f[2] for f in firsts if f and f[1] == 'follow'})
            follow_classes = sorted({((edge_classes or {}).get((s, doc)) or {}).get('class', 'unknown')
                                     for s in follow_sources})
            row = dict(keys, kind=kind, call=at_call, citation_index=i, doc_id=doc, start_line=a, end_line=b,
                       lines=len(cited),
                       cited_lines_returned=all(f is not None for f in firsts) if cited else False,
                       first_exposure_routes=sorted(routes),
                       first_exposure='follow_only' if routes == {'follow'} else 'read_only' if routes == {'read'}
                       else 'mixed' if routes else 'none',
                       page_routes=sorted(page_routes.get(doc, ())),
                       page_reached_by_follow='follow' in page_routes.get(doc, ()),
                       page_reached_by_read='read' in page_routes.get(doc, ()),
                       page_first_route=(reached_by.get(doc) or (None, None))[1],
                       page_listed_by_search=doc in listed,
                       first_exposing_follow_edge_classes=follow_classes)
            if kind == 'answer':
                if final_lines is None:
                    row['final_context'] = 'unknown'
                else:
                    have = final_lines.get(doc, set())
                    row['final_context'] = ('full' if cited and cited <= have else 'partial' if cited & have
                                            else 'none')
            if labels is not None and doc in variant.file_hashes:
                lab = labels.label(qid, doc, variant.file_hashes[doc])
                row['label'] = lab['label']
                if lab['label'] == 'supported':
                    spans = labels.spans(qid, doc, variant.file_hashes[doc], variant.lines[doc])
                    row['span_overlap_cited'] = span_exposure(spans, cited)
            elif labels is not None:
                row['label'] = 'identity_mismatch'
            citations.append(row)
    accepted_follows = [r for r in actions if r['action'] == 'follow' and r['status'] == 'accepted']
    # Final context visibility of each accepted follow's newly returned lines (original text in the answer request).
    for r in accepted_follows:
        if final_lines is None:
            r['returned_lines_in_final_context'] = 'unknown' if answer_call is not None else 'no_answer'
        else:
            shown = {n for a, b in r['returned_ranges'] for n in range(a, b + 1)}
            have = final_lines.get(r['doc_id'], set())
            r['returned_lines_in_final_context'] = ('all' if shown <= have else 'some' if shown & have else 'none')
            if r.get('label') == 'supported' and labels is not None:
                spans = labels.spans(qid, r['doc_id'], r['file_sha256'], variant.lines.get(r['doc_id'], []))
                r['span_exposure_final_context'] = span_exposure(spans, have)
    counts = Counter((r['action'], r['status']) for r in actions)
    trace_row = dict(keys, trace_sha256=cell.get('trace_sha256'), identity=identity,
                     identity_ok=all(identity.values()), terminal=trace.get('terminal'),
                     answer=bool(trace.get('answer')), steps=len(trace['steps']),
                     replay=rep['status'], first_unmatched_call=rep['first_unmatched_call'],
                     compactions=rep['compactions_recorded'],
                     compaction_retained_reads=[c['compaction_retained_reads'] for c in rep['calls']
                                                if c.get('compaction_retained_reads') is not None],
                     final_context='verified' if final_ok else ('no_answer' if answer_call is None else 'unknown'),
                     final_context_lines=sum(map(len, final_lines.values())) if final_lines else None,
                     final_context_notes=final.get('request_notes') if final_ok else None,
                     notes=len(trace.get('notes') or []),
                     action_status_counts={f'{a}:{s}': n for (a, s), n in sorted(counts.items(), key=str)},
                     follows_attempted=sum(r['action'] == 'follow' for r in actions),
                     follows_accepted=len(accepted_follows),
                     follows_accepted_added=sum(r.get('edge_class') == 'added' for r in accepted_follows),
                     zero_follow=not accepted_follows)
    return trace_row, actions, citations


# ------------------------------------------------------------------ summaries

GROUP = ('tier', 'variant_id', 'condition')
FOLLOW_FIELDS = ('status', 'edge_class', 'new_page_incident', 'source_opened_before', 'edge_in_menu', 'menu_status',
                 'listed_by_prior_search', 'first_returned_lines', 'first_page_text_exposure', 'label',
                 'span_exposure_this_call', 'span_exposure_new_lines', 'returned_lines_in_final_context',
                 'span_exposure_final_context', 'file_identity', 'text_identity', 'recorded_newly_observed_agrees')
CITATION_FIELDS = ('kind', 'first_exposure', 'page_reached_by_follow', 'page_reached_by_read', 'page_first_route',
                   'page_listed_by_search', 'final_context', 'label', 'span_overlap_cited', 'cited_lines_returned')
TRACE_FIELDS = ('identity_ok', 'terminal', 'answer', 'replay', 'final_context', 'zero_follow')


def _key(row):
    return '/'.join(str(row.get(k)) for k in GROUP)


def summarize(traces, actions, citations):
    """Counts per (tier, variant, condition): every categorical field of follow rows, citation rows and traces,
    with denominators. Repeated observations within a question stay rows; nothing here is a sampling unit."""
    out = defaultdict(lambda: {'traces': Counter(), 'actions': Counter(), 'follows': defaultdict(Counter),
                               'citations': defaultdict(Counter), 'trace_fields': defaultdict(Counter)})
    for t in traces:
        g = out[_key(t)]
        g['traces']['n'] += 1
        for f in TRACE_FIELDS:
            g['trace_fields'][f][str(t.get(f))] += 1
    for a in actions:
        g = out[_key(a)]
        g['actions'][f"{a['action']}:{a['status']}"] += 1
        if a['action'] == 'follow':
            for f in FOLLOW_FIELDS:
                g['follows'][f][str(a.get(f))] += 1
    for c in citations:
        g = out[_key(c)]
        for f in CITATION_FIELDS:
            g['citations'][f'{c["kind"]}:{f}'][str(c.get(f))] += 1
    plain = {}
    for k, g in sorted(out.items()):
        plain[k] = {'traces': dict(g['traces']), 'actions': dict(sorted(g['actions'].items())),
                    'follows': {f: dict(sorted(v.items())) for f, v in sorted(g['follows'].items())},
                    'citations': {f: dict(sorted(v.items())) for f, v in sorted(g['citations'].items())},
                    'trace_fields': {f: dict(sorted(v.items())) for f, v in sorted(g['trace_fields'].items())}}
    return plain


# ------------------------------------------------------------------ jobs

def validate_config(config):
    if not isinstance(config, dict) or config.get('schema_version') != CONFIG_SCHEMA:
        raise ValueError('trace-provenance-config-schema')
    if config.get('dialect') not in DIALECTS:
        raise ValueError('trace-provenance-dialect')
    for key in ('system_message', 'variants', 'cells'):
        if key not in config:
            raise ValueError(f'trace-provenance-config-missing:{key}')
    ids = [v['variant_id'] for v in config['variants']]
    if len(set(ids)) != len(ids):
        raise ValueError('trace-provenance-duplicate-variant')
    cells = [c['cell_id'] for c in config['cells']]
    if len(set(cells)) != len(cells):
        raise ValueError('trace-provenance-duplicate-cell')
    for c in config['cells']:
        if c['variant_id'] not in ids:
            raise ValueError('trace-provenance-cell-variant')
        for key in ('tier', 'question_id', 'question_sha256', 'condition', 'replicate', 'follow_links',
                    'trace', 'events', 'trace_sha256', 'labels'):
            if key not in c:
                raise ValueError(f'trace-provenance-cell-missing:{key}')


def input_paths(config):
    paths = {config['system_message']}
    for v in config['variants']:
        paths.add(v['graph'])
        for key in ('edge_classes',):
            if v.get(key):
                paths.add(v[key])
        for key in ('pool', 'page_decisions', 'judgments', 'qrels'):
            if (v.get('labels') or {}).get(key):
                paths.add(v['labels'][key])
        for view in v.get('views') or []:
            paths.add(view['bundle'])
    for c in config['cells']:
        paths.update((c['trace'], c['events']))
    return paths


def _views(variant_cfg, root):
    out, records = [], []
    for view in variant_cfg.get('views') or []:
        bundle = json.loads((root / view['bundle']).read_text(encoding='utf-8'))
        ms.validate_bundle(bundle)
        admitted = ms.admit_graph(bundle['nodes'], [tuple(e) for e in bundle['edges']])
        nodes, edges, rec = ms.select_view(admitted['nodes'], admitted['edges'], view['view'])
        out.append({'view_id': view['view_id'], 'nodes': set(nodes), 'edges': set(edges)})
        records.append({'view_id': view['view_id'], 'graph_id': bundle['graph_id'],
                        'bundle_canonical_graph_sha256': bundle['canonical_graph_sha256'],
                        'bundle_canonical_recomputed': ms.graph_sha256(admitted['nodes'], admitted['edges']),
                        'selection': rec})
    return out, records


def graph_identity(variant, bundle_nodes, bundle_edges):
    """Compare the agent's admitted link graph with a bundle's admitted graph (different hash namespaces)."""
    nodes = set(variant.lines)
    return {'agent_nodes': len(nodes), 'bundle_nodes': len(bundle_nodes),
            'nodes_only_agent': len(nodes - bundle_nodes), 'nodes_only_bundle': len(bundle_nodes - nodes),
            'agent_edges': len(variant.edges), 'bundle_edges': len(bundle_edges),
            'edges_only_agent': len(variant.edges - bundle_edges), 'edges_only_bundle': len(bundle_edges - variant.edges),
            'agent_graph_canonical_sha256': ms.graph_sha256(sorted(nodes), sorted(variant.edges)),
            'agent_graph_file_sha256': variant.graph_file_sha256}


def run_jobs(config, root):
    """All cells, variant by variant. Returns (trace_rows, action_rows, citation_rows, variant_records)."""
    validate_config(config)
    root = Path(root)
    system_message = (root / config['system_message']).read_text(encoding='utf-8')
    traces, actions, citations, variants = [], [], [], []
    for vcfg in config['variants']:
        variant = Variant(root / vcfg['corpus'], root / vcfg['graph'])
        classes, class_record = (load_edge_classes(root / vcfg['edge_classes'], variant) if vcfg.get('edge_classes')
                                 else (None, None))
        lab_cfg = vcfg.get('labels')
        labels = (Labels(root / lab_cfg['pool'], root / lab_cfg['page_decisions'], root / lab_cfg['judgments'],
                         root / lab_cfg['qrels'] if lab_cfg.get('qrels') else None) if lab_cfg else None)
        views, view_records = _views(vcfg, root)
        identity = []
        for vr, v in zip(view_records, views):
            if vr['selection']['kind'] == 'full':
                identity.append(dict(graph_identity(variant, v['nodes'], v['edges']), view_id=vr['view_id']))
        variants.append({'variant_id': vcfg['variant_id'], 'graph_file_sha256': variant.graph_file_sha256,
                         'graph_rows': variant.graph_rows, 'admitted_edges': len(variant.edges),
                         'documents': len(variant.lines), 'corpus_inventory_sha256': variant.corpus_inventory_sha256,
                         'edge_classes': class_record, 'labels': labels.files if labels else None,
                         'views': view_records, 'graph_identity': identity})
        for cell in config['cells']:
            if cell['variant_id'] != vcfg['variant_id']:
                continue
            raw = (root / cell['trace']).read_bytes()
            if sha_bytes(raw) != cell['trace_sha256']:
                traces.append({k: cell[k] for k in ('cell_id', 'tier', 'variant_id', 'question_id', 'condition',
                                                    'replicate')} | {'quarantined': 'trace-bytes-mismatch'})
                continue
            trace = json.loads(raw)
            events = jsonl(root / cell['events'])
            t, a, c = audit_trace(cell, trace, events, variant, system_message, edge_classes=classes,
                                  labels=labels if cell['labels'] else None, views=views or None)
            traces.append(t)
            actions.extend(a)
            citations.extend(c)
    return traces, actions, citations, variants
