"""Element-level comparison of saved answers and saved rubric judgments across arms, a versioned offline consumer.

The contract is docs/evaluations/answer_element_comparison.md. It reads normalized records: tasks with frozen
rubric elements, one record per scheduled answer (arm, condition, replicate, terminal, text) and one record per
saved judgment (the judged question, answer and elements, the selected attempt's raw and strict verdicts). It
never calls a model and never re-grades. Every output is a decomposition of the recorded verdicts:
- each scheduled answer x element is one row; a raw `satisfied` verdict whose span fails `quote_match_relaxed_v1`
  (`quote_rejected`) stays distinct from a negative verdict and from no judgment (`not_observed`);
- a judgment is joined only when its answer text, question hash and element list equal the answer's and the
  task's; mismatches, missing or duplicate verdicts are reported, never repaired;
- rates per task x condition x element x arm keep the scheduled denominator next to the observed one;
- aggregation follows `replicate_condition_task_v1`: replicates within condition, conditions within task, tasks
  equally; elements, conditions, replicates and scoring passes are not independent samples;
- an optional bounded review packet is selected by a fixed hash rule within outcome strata and blinded.
A matching quotation shows that text exists in the answer, not that it is correct. Nothing here measures
answer quality independently of the judge that produced the verdicts.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import unicodedata

VERSION = 'answer_element_comparison.v1'
CONFIG_SCHEMA = 'retrieval_arena.answer_element_comparison_config.v1'
RECORD_SCHEMA = 'retrieval_arena.element_judgment_record.v1'
PACKET_SCHEMA = 'retrieval_arena.blinded_answer_review_packet.v1'
RESPONSE_SCHEMA = 'retrieval_arena.blinded_answer_review_response.v1'
KEY_SCHEMA = 'retrieval_arena.blinded_answer_review_key.v1'
AGGREGATION = 'replicate_condition_task_v1'
RELAXED_RULE = 'quote_match_relaxed_v1'
OUTCOMES = ('relaxed_satisfied', 'quote_rejected', 'judged_not_satisfied', 'not_observed')
JUDGMENT_STATUSES = ('judged', 'no_answer', 'no_judgment', 'no_valid_completion', 'identity_mismatch',
                     'verdict_missing', 'verdict_duplicate')
STRATA = ('net_gain', 'net_loss', 'tradeoff', 'label_agreement')
REVIEW_LABELS = ('satisfied', 'partly satisfied', 'not satisfied', 'unsure')
OVERALL_LABELS = ('A', 'B', 'tie', 'unsure')
AGGREGATION_POLICY = ('per answer: counted elements / elements; per arm x task x condition: mean over replicates '
                      'with a value; per arm x task: mean over conditions with a value; a contrast: mean over tasks '
                      'with both values of the per-task difference, tasks weighted equally. Per element the same '
                      'order applies to the counted indicator, so element contributions add to the task value')


# ------------------------------------------------------------------ quote_match_relaxed_v1 (exact semantics)
# Normalization: NFKC; drop inline citation tags; strip `*`, backticks and straight/curly quotes; collapse
# whitespace. Rules in order: exact substring; ellipsis pieces in order (ellipsis spans use only this rule);
# normalized substring; at least 90% of the span's words in order (LCS over the whole answer).

WORD_THRESHOLD = 0.90
_CITATION = re.compile(r'\[[^\[\]\n]*\blines?\b[^\[\]\n]*\]|\([^()\n]*\blines?\b[^()\n]*\)', re.IGNORECASE)
_STRIP = dict.fromkeys(map(ord, '*`"\'‘’‚‛“”„‟′″'), None)
_ELLIPSIS = re.compile(r'\.\.\.+|…')


def normalize(text):
    text = unicodedata.normalize('NFKC', text)
    text = _CITATION.sub(' ', text)
    text = text.translate(_STRIP)
    return ' '.join(text.split())


def _lcs(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def word_coverage(span, answer):
    sw, aw = normalize(span).split(), normalize(answer).split()
    return (_lcs(sw, aw) if sw else 0), len(sw)


def relaxed_match(span, answer):
    """(admitted, rule, detail) for one asserted span against the saved answer text."""
    if not span or span == 'null':
        return False, None, {'reason': 'no-span'}
    if span in answer:
        return True, 'exact', {}
    na = normalize(answer)
    if _ELLIPSIS.search(span):
        pieces = [normalize(p) for p in _ELLIPSIS.split(span)]
        pieces = [p for p in pieces if p]
        pos, found = 0, 0
        for p in pieces:
            k = na.find(p, pos)
            if k < 0:
                break
            found, pos = found + 1, k + len(p)
        ok = bool(pieces) and found == len(pieces)
        return ok, 'ellipsis_pieces' if ok else None, {'pieces': len(pieces), 'pieces_found_in_order': found}
    if normalize(span) in na:
        return True, 'exact_normalized', {}
    hit, total = word_coverage(span, answer)
    ok = total > 0 and hit / total >= WORD_THRESHOLD
    return ok, 'word_order_90' if ok else None, {'words': total, 'words_in_order': hit}


def quote_location(span, answer, rule):
    """Where a span sits in the saved answer: character offsets only for a verbatim span."""
    if not span or span == 'null':
        return {'status': 'no_span'}
    if rule == 'exact':
        start = answer.find(span)
        return {'status': 'verbatim', 'char_start': start, 'char_end': start + len(span)}
    if rule:
        return {'status': f'located_{rule}'}
    return {'status': 'not_located'}


# ------------------------------------------------------------------ helpers

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def sha_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def code_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def mean(xs):
    xs = [float(x) for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def contrast_label(pair):
    return f'{pair[0]}-{pair[1]}'


# ------------------------------------------------------------------ config

def validate_config(config):
    if not isinstance(config, dict) or config.get('schema_version') != CONFIG_SCHEMA:
        raise ValueError('answer-element-config-schema')
    for key in ('arms', 'conditions', 'replicates', 'contrasts', 'tasks', 'answers', 'judgments'):
        if key not in config:
            raise ValueError(f'answer-element-config-missing:{key}')
    if config.get('aggregation', AGGREGATION) != AGGREGATION or config.get('relaxed_rule', RELAXED_RULE) != RELAXED_RULE:
        raise ValueError('answer-element-config-policy')
    arms = config['arms']
    for pair in config['contrasts']:
        if len(pair) != 2 or pair[0] not in arms or pair[1] not in arms or pair[0] == pair[1]:
            raise ValueError('answer-element-config-contrast')
    tasks = [t['task_id'] for t in config['tasks']]
    if len(set(tasks)) != len(tasks):
        raise ValueError('answer-element-duplicate-task')
    for t in config['tasks']:
        ids = [e['element_id'] for e in t['elements']]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError('answer-element-task-elements')
        if sha_text(t['question']) != t['question_sha256']:
            raise ValueError('answer-element-task-question-hash')
    answers = [a['answer_id'] for a in config['answers']]
    if len(set(answers)) != len(answers):
        raise ValueError('answer-element-duplicate-answer')
    slots = Counter((a['arm'], a['task_id'], a['condition'], a['replicate']) for a in config['answers'])
    if any(n > 1 for n in slots.values()):
        raise ValueError('answer-element-duplicate-schedule-slot')
    for a in config['answers']:
        if (a['arm'] not in arms or a['task_id'] not in tasks or a['condition'] not in config['conditions']
                or a['replicate'] not in config['replicates']):
            raise ValueError('answer-element-answer-outside-schedule')
    judged = [j['judgment_id'] for j in config['judgments']]
    if len(set(judged)) != len(judged):
        raise ValueError('answer-element-duplicate-judgment')
    sel = config.get('selection')
    if sel is not None:
        for key in ('salt', 'focal', 'contrasts', 'max_pairs'):
            if key not in sel:
                raise ValueError(f'answer-element-selection-missing:{key}')
        if any(p[0] != sel['focal'] or list(p) not in [list(c) for c in config['contrasts']] for p in sel['contrasts']):
            raise ValueError('answer-element-selection-contrast')
        if not isinstance(sel['max_pairs'], int) or not 1 <= sel['max_pairs'] <= len(sel['contrasts']) * len(STRATA):
            raise ValueError('answer-element-selection-bound')


def input_paths(config):
    paths = {a['answer_path'] for a in config['answers'] if a.get('answer_path')}
    paths |= {j['record_path'] for j in config['judgments']}
    for t in config['tasks']:
        paths |= {r['path'] for r in t.get('references', [])}
    return paths


# ------------------------------------------------------------------ joins

def _verdict_index(verdicts, element_ids):
    """{element_id: verdict} for ids judged exactly once, plus missing, duplicate and foreign ids."""
    counts = Counter(v.get('element_id') for v in verdicts)
    index = {v['element_id']: v for v in verdicts if counts[v.get('element_id')] == 1}
    return index, {'missing': sorted(set(element_ids) - set(counts)),
                   'duplicate': sorted(e for e, n in counts.items() if n > 1),
                   'foreign': sorted(str(e) for e in set(counts) - set(element_ids))}


def join_judgments(config, root):
    """{answer_id: (judgment_id, record)} for aliases, plus a list of join issues."""
    by_answer, issues = defaultdict(list), []
    known = {a['answer_id'] for a in config['answers']}
    for j in config['judgments']:
        record = json.loads((Path(root) / j['record_path']).read_text(encoding='utf-8'))
        if record.get('schema_version') != RECORD_SCHEMA or record.get('judgment_id') != j['judgment_id']:
            raise ValueError(f'answer-element-record-schema:{j["judgment_id"]}')
        for answer_id in j['answer_ids']:
            if answer_id not in known:
                issues.append({'issue': 'judgment_for_unscheduled_answer', 'judgment_id': j['judgment_id'],
                               'answer_id': answer_id})
                continue
            by_answer[answer_id].append((j['judgment_id'], record))
    out = {}
    for answer_id, found in by_answer.items():
        if len(found) > 1:
            issues.append({'issue': 'answer_has_several_judgments', 'answer_id': answer_id,
                           'judgment_ids': sorted(f[0] for f in found)})
        out[answer_id] = found
    return out, issues


def check_identity(answer, text, task, record):
    """Reasons a judgment record does not belong to this answer and task ([] when it does)."""
    reasons = []
    if record['answer'] != text:
        reasons.append('answer_text')
    if sha_text(record['question']) != answer['question_sha256'] or answer['question_sha256'] != task['question_sha256']:
        reasons.append('question')
    frozen = [(e['element_id'], e['requirement'], e['acceptance']) for e in task['elements']]
    judged = [(e.get('element_id'), e.get('requirement'), e.get('acceptance')) for e in record['elements']]
    if judged != frozen:
        ids = [e[0] for e in judged]
        if sorted(ids) != sorted(e[0] for e in frozen):
            reasons.append('element_ids')
        elif sorted(judged, key=str) != sorted(frozen, key=str):
            reasons.append('element_text')
        else:
            reasons.append('element_order')
    return reasons


# ------------------------------------------------------------------ element rows

def element_rows(config, root):
    """One row per scheduled answer x frozen element, the per-answer records and the join/coverage issues."""
    root = Path(root)
    tasks = {t['task_id']: t for t in config['tasks']}
    joined, issues = join_judgments(config, root)
    rows, answers_out = [], []
    for a in sorted(config['answers'], key=lambda x: (x['task_id'], x['arm'], x['condition'], x['replicate'])):
        task = tasks[a['task_id']]
        element_ids = [e['element_id'] for e in task['elements']]
        text = None
        if a.get('answer_path'):
            text = (root / a['answer_path']).read_text(encoding='utf-8')
            if sha_text(text) != a['answer_sha256']:
                raise ValueError(f'answer-element-answer-hash:{a["answer_id"]}')
        found = joined.get(a['answer_id'], [])
        status, judgment_id, record, reasons = 'judged', None, None, []
        raw_index = strict_index = {}
        raw_problems = strict_problems = {'missing': [], 'duplicate': [], 'foreign': []}
        if text is None:
            status = 'no_answer'
        elif not found:
            status = 'no_judgment'
        elif len(found) > 1:
            status, judgment_id = 'identity_mismatch', None
            reasons = ['several_judgments']
        else:
            judgment_id, record = found[0]
            reasons = check_identity(a, text, task, record)
            if reasons:
                status = 'identity_mismatch'
            elif record['status'] != 'valid':
                status = 'no_valid_completion'
            else:
                raw_index, raw_problems = _verdict_index(record['raw_verdicts'], element_ids)
                strict_index, strict_problems = _verdict_index(record['strict_verdicts'], element_ids)
        if reasons:
            issues.append({'issue': 'identity_mismatch', 'answer_id': a['answer_id'], 'judgment_id': judgment_id,
                           'reasons': reasons})
        for kind, problems in (('raw', raw_problems), ('strict', strict_problems)):
            for what, ids in problems.items():
                if ids:
                    issues.append({'issue': f'{kind}_verdict_{what}', 'answer_id': a['answer_id'],
                                   'judgment_id': judgment_id, 'element_ids': ids})
        counted_total, observed_total = 0, 0
        for e in task['elements']:
            eid = e['element_id']
            row = {'answer_id': a['answer_id'], 'task_id': a['task_id'], 'arm': a['arm'], 'condition': a['condition'],
                   'replicate': a['replicate'], 'terminal': a['terminal'], 'answer_sha256': a.get('answer_sha256'),
                   'judgment_id': judgment_id, 'selected_attempt': record.get('selected_attempt') if record else None,
                   'record_sources': record.get('sources') if record else None,
                   'element_id': eid, 'requirement': e['requirement'], 'acceptance': e['acceptance'],
                   'raw_verdict': None, 'raw_reason': None, 'raw_answer_span': None, 'strict_verdict': None,
                   'relaxed_eligible': None, 'relaxed_rule': None, 'relaxed_detail': None, 'quote_location': None,
                   'judgment_status': status, 'outcome': 'not_observed', 'observed': False, 'counted': None}
            if status == 'judged':
                raw, strict = raw_index.get(eid), strict_index.get(eid)
                if raw is None:
                    row['judgment_status'] = ('verdict_duplicate' if eid in raw_problems['duplicate']
                                              else 'verdict_missing')
                else:
                    row.update(raw_verdict=raw.get('verdict'), raw_reason=raw.get('reason'),
                               raw_answer_span=raw.get('answer_span'),
                               strict_verdict=strict.get('verdict') if strict else None, observed=True)
                    if raw.get('verdict') == 'satisfied':
                        ok, rule, detail = relaxed_match(raw.get('answer_span'), text)
                        row.update(relaxed_eligible=ok, relaxed_rule=rule, relaxed_detail=detail,
                                   quote_location=quote_location(raw.get('answer_span'), text, rule),
                                   outcome='relaxed_satisfied' if ok else 'quote_rejected', counted=ok)
                    else:
                        row.update(relaxed_eligible=False, outcome='judged_not_satisfied', counted=False)
            observed_total += row['observed']
            counted_total += bool(row['counted'])
            rows.append(row)
        complete = status == 'judged' and observed_total == len(element_ids)
        answers_out.append({'answer_id': a['answer_id'], 'task_id': a['task_id'], 'arm': a['arm'],
                            'condition': a['condition'], 'replicate': a['replicate'], 'terminal': a['terminal'],
                            'judgment_id': judgment_id, 'judgment_status': status, 'elements': len(element_ids),
                            'elements_observed': observed_total, 'elements_counted': counted_total,
                            'complete': complete,
                            # The historical per-answer score: counted / elements for a valid joined judgment.
                            'relaxed_score': counted_total / len(element_ids) if status == 'judged' else None})
    return rows, answers_out, issues


# ------------------------------------------------------------------ aggregation

def aggregate(values, tasks, arms, conditions):
    """values: {(arm, task, condition, replicate): x or None} -> {arm: {task: value}} under AGGREGATION."""
    cells = defaultdict(list)
    for (arm, task, cond, _), x in values.items():
        cells[(arm, task, cond)].append(x)
    out = {arm: {} for arm in arms}
    for arm in arms:
        for task in tasks:
            out[arm][task] = mean(mean(cells.get((arm, task, c), [])) for c in conditions)
    return out


def contrast(per_task, pair, tasks):
    a, b = pair
    diffs = {t: per_task[a][t] - per_task[b][t] for t in tasks
             if per_task[a].get(t) is not None and per_task[b].get(t) is not None}
    return {'estimate': mean(diffs.values()), 'n': len(diffs),
            'mean_a': mean(per_task[a][t] for t in diffs), 'mean_b': mean(per_task[b][t] for t in diffs),
            'per_task': diffs}


def score_values(config, answers_out):
    """{measure: {(arm, task, cond, rep): value}}: the relaxed score plus any declared external per-answer scores
    (several grades of one answer are averaged first, missing grades dropped)."""
    slot = {a['answer_id']: (a['arm'], a['task_id'], a['condition'], a['replicate']) for a in config['answers']}
    out = {'rubric_relaxed': {slot[a['answer_id']]: a['relaxed_score'] for a in answers_out}}
    for measure, grades in sorted((config.get('scores') or {}).items()):
        out[measure] = {s: mean((grades.get(aid) or [])) for aid, s in slot.items()}
    return out


def historical(config, answers_out):
    tasks = [t['task_id'] for t in config['tasks']]
    out = {}
    for measure, values in score_values(config, answers_out).items():
        per_task = aggregate(values, tasks, config['arms'], config['conditions'])
        out[measure] = {contrast_label(p): contrast(per_task, p, tasks) for p in config['contrasts']}
        for res in out[measure].values():
            res['per_task'] = {t: res['per_task'][t] for t in sorted(res['per_task'])}
    return out


# ------------------------------------------------------------------ cells and contrasts

def cell_table(config, rows):
    """One record per task x condition x element x arm: counted, observed and scheduled, by replicate."""
    by = defaultdict(dict)
    for r in rows:
        by[(r['task_id'], r['condition'], r['element_id'], r['arm'])][r['replicate']] = r
    scheduled = Counter((a['task_id'], a['condition'], a['arm']) for a in config['answers'])
    out = []
    for t in config['tasks']:
        for cond in config['conditions']:
            for e in t['elements']:
                for arm in config['arms']:
                    reps = by.get((t['task_id'], cond, e['element_id'], arm), {})
                    observed = [r for r in reps.values() if r['observed']]
                    counted = sum(bool(r['counted']) for r in observed)
                    out.append({'task_id': t['task_id'], 'condition': cond, 'element_id': e['element_id'], 'arm': arm,
                                'planned': len(config['replicates']),
                                'scheduled': scheduled[(t['task_id'], cond, arm)], 'observed': len(observed),
                                'counted': counted, 'rate': counted / len(observed) if observed else None,
                                'complete': len(observed) == len(config['replicates']),
                                'by_replicate': {str(k): reps[k]['outcome'] if k in reps else 'not_scheduled'
                                                 for k in config['replicates']}})
    return out


def _direction(x):
    return None if x is None else (0 if x == 0 else (1 if x > 0 else -1))


def persistence(dirs):
    """Across numbered replicate comparisons: persists, reverses, one_sided, unchanged or incomplete."""
    if any(d is None for d in dirs):
        return 'incomplete'
    nonzero = {d for d in dirs if d}
    if not nonzero:
        return 'unchanged'
    if len(nonzero) == 2:
        return 'reverses'
    return 'persists' if all(dirs) else 'one_sided'


def element_contrasts(config, cells):
    """Per contrast x task x condition x element: rates, the difference, the numbered-replicate directions."""
    idx = {(c['task_id'], c['condition'], c['element_id'], c['arm']): c for c in cells}
    out = []
    for pair in config['contrasts']:
        label = contrast_label(pair)
        for t in config['tasks']:
            for cond in config['conditions']:
                for e in t['elements']:
                    ca, cb = (idx[(t['task_id'], cond, e['element_id'], arm)] for arm in pair)
                    dirs = []
                    for rep in config['replicates']:
                        oa, ob = ca['by_replicate'][str(rep)], cb['by_replicate'][str(rep)]
                        if {oa, ob} & {'not_observed', 'not_scheduled'}:
                            dirs.append(None)
                        else:
                            dirs.append((oa == 'relaxed_satisfied') - (ob == 'relaxed_satisfied'))
                    diff = None if ca['rate'] is None or cb['rate'] is None else ca['rate'] - cb['rate']
                    out.append({'contrast': label, 'task_id': t['task_id'], 'condition': cond,
                                'element_id': e['element_id'], 'rate_a': ca['rate'], 'rate_b': cb['rate'],
                                'counted_a': ca['counted'], 'observed_a': ca['observed'],
                                'counted_b': cb['counted'], 'observed_b': cb['observed'],
                                'scheduled_a': ca['scheduled'], 'scheduled_b': cb['scheduled'], 'difference': diff,
                                'complete': ca['complete'] and cb['complete'],
                                'direction': _direction(diff),
                                'replicate_directions': {str(r): d for r, d in zip(config['replicates'], dirs)},
                                'persistence': persistence(dirs),
                                'shared': ('shared_omission' if ca['rate'] == 0 and cb['rate'] == 0 else
                                           'shared_satisfied' if ca['rate'] == 1 and cb['rate'] == 1 else None)})
    return out


def element_summary(config, contrasts):
    """Per contrast x task x element over conditions: the mean difference (historical order and complete cells
    only), its additive contribution to the overall estimate, and counts of directions and persistence."""
    n_tasks = len(config['tasks'])
    sizes = {t['task_id']: len(t['elements']) for t in config['tasks']}
    groups = defaultdict(list)
    for c in contrasts:
        groups[(c['contrast'], c['task_id'], c['element_id'])].append(c)
    out = []
    for (label, task, eid), cs in sorted(groups.items()):
        diff = mean(c['difference'] for c in cs)
        complete = mean(c['difference'] for c in cs if c['complete'])
        dirs = Counter(c['direction'] for c in cs)
        reps = Counter(d for c in cs for d in c['replicate_directions'].values())
        out.append({'contrast': label, 'task_id': task, 'element_id': eid, 'conditions': len(cs),
                    'mean_difference': diff, 'mean_difference_complete_cells': complete,
                    'complete_conditions': sum(c['complete'] for c in cs),
                    'contribution_to_overall': None if diff is None else diff / sizes[task] / n_tasks,
                    'conditions_favourable': dirs[1], 'conditions_unfavourable': dirs[-1], 'conditions_unchanged': dirs[0],
                    'conditions_undefined': dirs[None],
                    'replicate_pairs_gain': reps[1], 'replicate_pairs_loss': reps[-1], 'replicate_pairs_same': reps[0],
                    'replicate_pairs_incomplete': reps[None],
                    'persistence': dict(sorted(Counter(c['persistence'] for c in cs).items())),
                    'shared_omission_conditions': sum(c['shared'] == 'shared_omission' for c in cs),
                    'shared_satisfied_conditions': sum(c['shared'] == 'shared_satisfied' for c in cs)})
    return out


def task_summary(config, elements):
    """Per contrast x task: the element-mean difference (equals the historical task difference), and how many
    elements move each way, so a zero net can show a tradeoff."""
    groups = defaultdict(list)
    for e in elements:
        groups[(e['contrast'], e['task_id'])].append(e)
    out = []
    for (label, task), es in sorted(groups.items()):
        out.append({'contrast': label, 'task_id': task, 'elements': len(es),
                    'mean_difference': mean(e['mean_difference'] for e in es),
                    'mean_difference_complete_cells': mean(e['mean_difference_complete_cells'] for e in es),
                    'elements_favourable': sum((e['mean_difference'] or 0) > 0 for e in es),
                    'elements_unfavourable': sum((e['mean_difference'] or 0) < 0 for e in es),
                    'elements_unchanged': sum(e['mean_difference'] == 0 for e in es),
                    'replicate_pairs_gain': sum(e['replicate_pairs_gain'] for e in es),
                    'replicate_pairs_loss': sum(e['replicate_pairs_loss'] for e in es)})
    return out


def breakdowns(config, contrasts):
    """Descriptive overall differences per condition and per numbered replicate (never a selection)."""
    sizes = {t['task_id']: len(t['elements']) for t in config['tasks']}
    out = {}
    for pair in config['contrasts']:
        label = contrast_label(pair)
        cs = [c for c in contrasts if c['contrast'] == label]
        per_cond = {}
        for cond in config['conditions']:
            per_task = defaultdict(list)
            for c in cs:
                if c['condition'] == cond:
                    per_task[c['task_id']].append(c['difference'])
            per_cond[cond] = mean(mean(v) for v in per_task.values())
        per_rep = {}
        for rep in config['replicates']:
            per_task = defaultdict(list)
            for c in cs:
                per_task[(c['task_id'])].append(c['replicate_directions'][str(rep)])
            per_rep[str(rep)] = {
                'mean_difference': mean(mean(v) for v in per_task.values()),
                'gains': sum(d == 1 for v in per_task.values() for d in v),
                'losses': sum(d == -1 for v in per_task.values() for d in v),
                'incomplete': sum(d is None for v in per_task.values() for d in v)}
        complete = defaultdict(list)
        for c in cs:
            if c['complete']:
                complete[(c['task_id'], c['element_id'])].append(c['difference'])
        by_task = defaultdict(list)
        for (task, _), v in complete.items():
            by_task[task].append(mean(v))
        out[label] = {'by_condition': per_cond, 'by_replicate': per_rep,
                      'complete_cells_overall': mean(mean(v) for v in by_task.values()),
                      'cells': len(cs), 'complete_cells': sum(c['complete'] for c in cs),
                      'element_count_check': sum(sizes.values())}
    return out


# ------------------------------------------------------------------ replicate pairs and selection

def replicate_pairs(config, answers_out, rows):
    """Per contrast x task x condition x numbered replicate: gains, losses and stratum for complete pairs."""
    ans = {(a['arm'], a['task_id'], a['condition'], a['replicate']): a for a in answers_out}
    counted = defaultdict(set)
    for r in rows:
        if r['counted']:
            counted[r['answer_id']].add(r['element_id'])
    out = []
    for pair in config['contrasts']:
        for t in config['tasks']:
            for cond in config['conditions']:
                for rep in config['replicates']:
                    a, b = (ans.get((arm, t['task_id'], cond, rep)) for arm in pair)
                    complete = bool(a and b and a['complete'] and b['complete'])
                    rec = {'contrast': contrast_label(pair), 'task_id': t['task_id'], 'condition': cond,
                           'replicate': rep, 'answer_a': a['answer_id'] if a else None,
                           'answer_b': b['answer_id'] if b else None, 'complete': complete,
                           'gains': None, 'losses': None, 'stratum': None}
                    if complete:
                        ga = sorted(counted[a['answer_id']] - counted[b['answer_id']])
                        lo = sorted(counted[b['answer_id']] - counted[a['answer_id']])
                        rec.update(gains=ga, losses=lo, stratum=(
                            'net_gain' if len(ga) > len(lo) else 'net_loss' if len(lo) > len(ga)
                            else 'tradeoff' if ga else 'label_agreement'))
                    out.append(rec)
    return out


def selection_key(salt, label, task, cond, rep, purpose=None):
    parts = [salt] + ([purpose] if purpose else []) + [label, task, cond, str(rep)]
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


def select_pairs(config, pairs):
    """The lowest selection key within each stratum of each selection contrast; LT's side by a separate key."""
    sel = config['selection']
    chosen, receipt = [], {'salt': sel['salt'], 'focal': sel['focal'], 'contrasts': {}}
    for pair in sel['contrasts']:
        label = contrast_label(pair)
        cand = [p for p in pairs if p['contrast'] == label and p['complete']]
        strata = {}
        picked = []
        for s in STRATA:
            members = sorted(((selection_key(sel['salt'], label, p['task_id'], p['condition'], p['replicate']), p)
                              for p in cand if p['stratum'] == s), key=lambda x: x[0])
            strata[s] = {'candidates': len(members),
                         'selected': ({'key': members[0][0], 'task_id': members[0][1]['task_id'],
                                       'condition': members[0][1]['condition'],
                                       'replicate': members[0][1]['replicate']} if members else None)}
            if members:
                picked.append((s, members[0][1]))
        order = sorted(picked, key=lambda x: selection_key(sel['salt'], label, x[1]['task_id'], x[1]['condition'],
                                                            x[1]['replicate'], 'position'))
        for i, (s, p) in enumerate(order):
            chosen.append({**p, 'stratum': s, 'focal_side': 'A' if i % 2 == 0 else 'B'})
        receipt['contrasts'][label] = {'complete_candidates': len(cand),
                                       'incomplete_pairs': sum(1 for p in pairs if p['contrast'] == label and not p['complete']),
                                       'strata': strata}
    chosen = sorted(chosen, key=lambda p: selection_key(sel['salt'], p['contrast'], p['task_id'], p['condition'],
                                                         p['replicate'], 'order'))[:sel['max_pairs']]
    for i, p in enumerate(chosen, 1):
        p['pair_id'] = f'P{i:02d}'
    sides = Counter((p['contrast'], p['focal_side']) for p in chosen)
    receipt['focal_sides'] = {f'{c}|{s}': n for (c, s), n in sorted(sides.items())}
    receipt['pairs'] = [{k: p[k] for k in ('pair_id', 'contrast', 'task_id', 'condition', 'replicate', 'stratum',
                                           'focal_side')} for p in chosen]
    return chosen, receipt


# ------------------------------------------------------------------ blinded packet

PACKET_INSTRUCTIONS = (
    'For each pair, read the task, each frozen requirement and its acceptance criterion, then answers A and B in '
    'full. For every requirement mark each answer satisfied, partly satisfied, not satisfied or unsure, with a short '
    'quote from the answer and a reason; note any contradiction or unsupported addition. Use unsure when the '
    'reference material does not settle it. Only after the element review, give an overall completeness '
    'comparison: A, B, tie or unsure. Record your identity, role, the date, the packet hash, how many pairs you '
    'reviewed and anything that revealed which version produced an answer before the key is joined.')


def build_review(config, root, chosen, answers_text, forbidden=()):
    """(packet, blank response, private key). The packet holds task, elements, references and answers A/B only."""
    tasks = {t['task_id']: t for t in config['tasks']}
    root = Path(root)
    pairs, key_pairs, response_pairs = [], [], []
    focal = config['selection']['focal']
    for p in chosen:
        task = tasks[p['task_id']]
        other = p['contrast'].split('-', 1)[1]
        side = {'A': p['answer_a'], 'B': p['answer_b']} if p['focal_side'] == 'A' else {'A': p['answer_b'], 'B': p['answer_a']}
        arms = {'A': focal, 'B': other} if p['focal_side'] == 'A' else {'A': other, 'B': focal}
        refs = [{'label': r['label'], 'text': (root / r['path']).read_text(encoding='utf-8')}
                for r in task.get('references', [])]
        pairs.append({'pair_id': p['pair_id'], 'question': task['question'],
                      'elements': [{'element_id': e['element_id'], 'requirement': e['requirement'],
                                    'acceptance': e['acceptance']} for e in task['elements']],
                      'references': refs, 'answers': {s: answers_text[side[s]] for s in ('A', 'B')}})
        key_pairs.append({'pair_id': p['pair_id'], 'contrast': p['contrast'], 'task_id': p['task_id'],
                          'condition': p['condition'], 'replicate': p['replicate'], 'stratum': p['stratum'],
                          'A': {'arm': arms['A'], 'answer_id': side['A']}, 'B': {'arm': arms['B'], 'answer_id': side['B']}})
        blank = {'label': None, 'quote': None, 'reason': None, 'contradiction_or_unsupported': None}
        response_pairs.append({'pair_id': p['pair_id'],
                               'elements': [{'element_id': e['element_id'], 'A': dict(blank), 'B': dict(blank)}
                                            for e in task['elements']],
                               'overall': {'completeness': None, 'note': None}})
    packet = {'schema_version': PACKET_SCHEMA, 'labels': list(REVIEW_LABELS), 'overall_labels': list(OVERALL_LABELS),
              'instructions': PACKET_INSTRUCTIONS, 'pairs': pairs}
    packet_hash = hashlib.sha256(canonical(packet)).hexdigest()
    response = {'schema_version': RESPONSE_SCHEMA, 'packet_sha256': packet_hash,
                'reviewer': {'identity': None, 'role': None, 'independent_of_author': None},
                'date': None, 'reviewed_pairs': None, 'unblinding': {'occurred': None, 'notes': None},
                'pairs': response_pairs}
    key = {'schema_version': KEY_SCHEMA, 'packet_sha256': packet_hash, 'pairs': key_pairs}
    findings = leakage(packet, forbidden)
    if findings:
        raise ValueError('answer-element-review-leak:' + ','.join(findings[:5]))
    return packet, response, key


PACKET_PAIR_KEYS = {'pair_id', 'question', 'elements', 'references', 'answers'}
PACKET_ELEMENT_KEYS = {'element_id', 'requirement', 'acceptance'}


def leakage(packet, forbidden=()):
    """Structural and string checks that the packet carries no arm, score, verdict, stratum or file identity."""
    out = []
    if set(packet) != {'schema_version', 'labels', 'overall_labels', 'instructions', 'pairs'}:
        out.append('packet-keys')
    for p in packet['pairs']:
        if set(p) != PACKET_PAIR_KEYS or set(p['answers']) != {'A', 'B'}:
            out.append(f'pair-keys:{p.get("pair_id")}')
        if any(set(e) != PACKET_ELEMENT_KEYS for e in p['elements']):
            out.append(f'element-keys:{p.get("pair_id")}')
        if any(set(r) != {'label', 'text'} for r in p['references']):
            out.append(f'reference-keys:{p.get("pair_id")}')
    text = canonical(packet).decode('utf-8')
    for s in forbidden:
        if s and s in text:
            out.append(f'string:{s}')
    return out


# ------------------------------------------------------------------ run

def summarize(config, rows, answers_out, issues, contrasts, elements):
    status = Counter(a['judgment_status'] for a in answers_out)
    outcome = Counter((r['arm'], r['outcome']) for r in rows)
    rules = Counter(r['relaxed_rule'] for r in rows if r['raw_verdict'] == 'satisfied')
    by_contrast = {}
    for pair in config['contrasts']:
        label = contrast_label(pair)
        cs = [c for c in contrasts if c['contrast'] == label]
        es = [e for e in elements if e['contrast'] == label]
        by_contrast[label] = {
            'cells': len(cs), 'cells_complete': sum(c['complete'] for c in cs),
            'cell_directions': {str(k): v for k, v in sorted(Counter(c['direction'] for c in cs).items(), key=str)},
            'cell_persistence': dict(sorted(Counter(c['persistence'] for c in cs).items())),
            'cell_shared': dict(sorted(Counter(c['shared'] or 'differs_or_partial' for c in cs).items())),
            'elements_favourable': sum((e['mean_difference'] or 0) > 0 for e in es),
            'elements_unfavourable': sum((e['mean_difference'] or 0) < 0 for e in es),
            'elements_unchanged': sum(e['mean_difference'] == 0 for e in es),
            'elements_undefined': sum(e['mean_difference'] is None for e in es),
            'sum_of_contributions': sum(e['contribution_to_overall'] or 0 for e in es)}
    return {'answers': len(answers_out), 'element_rows': len(rows),
            'answers_by_status': dict(sorted(status.items())),
            'answers_by_arm_status': {f'{a}|{s}': n for (a, s), n in sorted(
                Counter((x['arm'], x['judgment_status']) for x in answers_out).items())},
            'outcomes_by_arm': {f'{a}|{o}': n for (a, o), n in sorted(outcome.items())},
            'raw_satisfied_by_rule': {str(k): v for k, v in sorted(rules.items(), key=str)},
            'issues': dict(sorted(Counter(i['issue'] for i in issues).items())),
            'contrasts': by_contrast}


def run(config, root):
    """Every table, plus the review artifacts when a selection is declared."""
    validate_config(config)
    rows, answers_out, issues = element_rows(config, root)
    cells = cell_table(config, rows)
    contrasts = element_contrasts(config, cells)
    elements = element_summary(config, contrasts)
    tasks = task_summary(config, elements)
    pairs = replicate_pairs(config, answers_out, rows)
    result = {'rows': rows, 'answers': answers_out, 'issues': issues, 'cells': cells, 'contrasts': contrasts,
              'elements': elements, 'tasks': tasks, 'pairs': pairs,
              'historical': historical(config, answers_out), 'breakdowns': breakdowns(config, contrasts)}
    result['summary'] = summarize(config, rows, answers_out, issues, contrasts, elements)
    if config.get('selection'):
        chosen, receipt = select_pairs(config, pairs)
        texts = {}
        for a in config['answers']:
            if a.get('answer_path'):
                texts[a['answer_id']] = (Path(root) / a['answer_path']).read_text(encoding='utf-8')
        forbidden = set(config['selection'].get('forbidden_strings', []))
        forbidden |= {a['answer_id'] for a in config['answers']} | {j['judgment_id'] for j in config['judgments']}
        forbidden |= {r['raw_reason'] for r in rows if r['raw_reason'] and len(r['raw_reason']) >= 20}
        packet, response, key = build_review(config, root, chosen, texts, sorted(forbidden))
        receipt['packet_sha256'] = response['packet_sha256']
        result.update(selection=receipt, packet=packet, response=response, key=key)
    return result
