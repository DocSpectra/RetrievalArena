"""Purpose annotation, leakage groups and blind human exchange; no corpus defaults.

Model access is injected or obtained from model_providers. Nothing in this module
confers authorization, human approval, or annotation validity.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import time

VERSION = 'retrieval_arena.page_role_annotation.v1'
VERSION_V2 = 'retrieval_arena.page_role_annotation.v2'
STRICT_EVIDENCE = 'exact_raw_quote.v1'
MAPPED_EVIDENCE = 'mapped_display_quote.v2'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def raw_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def body(text):
    match = re.match(r'\A---[^\S\n]*\r?\n.*?\r?\n---(?:\r?\n|$)', text, re.S)
    return text[match.end():] if match else text


def view(text):
    """Static source prose, retaining headings/code. Never run templates or HTML."""
    value = body(text)
    flags = {'empty_body': not value.strip(), 'unrendered_template': bool(re.search(r'{{[<%]', value)),
             'image_content_unavailable': bool(re.search(r'!\[', value))}
    def prose(part):
        part = re.sub(r'<!--.*?-->', '', part, flags=re.S)
        part = re.sub(r'{{[<%].*?[%>]}}', '[unrendered template]', part, flags=re.S)
        part = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'[image unavailable: \1]', part)
        part = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', part)
        part = re.sub(r'^\s*\[[^\]]+\]:\s*\S+.*$', '', part, flags=re.M)
        part = re.sub(r'https?://[^\s<>]+', '[destination omitted]', part)
        return re.sub(r'<[^>]+>', '[markup omitted]', part)
    # Code is literal content. Embedded paths/URLs in examples are unavoidable
    # content cues, not separately provided source IDs or graph destinations.
    parts = re.split(r'(```.*?```|~~~.*?~~~|`[^`\n]+`)', value, flags=re.S)
    value = ''.join(part if i % 2 else prose(part) for i, part in enumerate(parts))
    return value, flags


def mapped_view(text):
    """Reproduce view() exactly, retaining a raw-source interval for each char.

    Replacements identify their origin rather than pretending to be source text.
    Code remains literal. This is an alignment view, never rendered execution.
    """
    value = body(text); offset = len(text)-len(value)
    source = [(offset+i, offset+i+1, 'source') for i in range(len(value))]

    def prose(value, source):
        def rewrite(pattern, replacement, flags=0):
            nonlocal value, source
            parts, positions = [], []; previous = 0
            for match in re.finditer(pattern, value, flags):
                parts.append(value[previous:match.start()]); positions.extend(source[previous:match.start()])
                new_text, new_positions = replacement(match, source)
                require(len(new_text) == len(new_positions), 'quote-map-shape')
                parts.append(new_text); positions.extend(new_positions); previous = match.end()
            parts.append(value[previous:]); positions.extend(source[previous:])
            value, source = ''.join(parts), positions

        def omit(match, positions): return '', []
        def marker(label, kind):
            def replace(match, positions):
                selected = positions[match.start():match.end()]
                span = (selected[0][0], selected[-1][1], kind)
                return label, [span]*len(label)
            return replace
        def image(match, positions):
            prefix, suffix = '[image unavailable: ', ']'
            selected = positions[match.start():match.end()]
            span = (selected[0][0], selected[-1][1], 'unavailable_image')
            return prefix+match[1]+suffix, [span]*len(prefix)+positions[match.start(1):match.end(1)]+[span]
        rewrite(r'<!--.*?-->', omit, re.S)
        rewrite(r'{{[<%].*?[%>]}}', marker('[unrendered template]', 'unrendered_template'), re.S)
        rewrite(r'!\[([^\]]*)\]\([^)]*\)', image)
        rewrite(r'\[([^\]]*)\]\([^)]*\)', lambda m, p: (m[1], p[m.start(1):m.end(1)]))
        rewrite(r'^\s*\[[^\]]+\]:\s*\S+.*$', omit, re.M)
        rewrite(r'https?://[^\s<>]+', marker('[destination omitted]', 'redacted_destination'))
        rewrite(r'<[^>]+>', marker('[markup omitted]', 'omitted_markup'))
        return value, source

    output, positions = [], []; previous = 0
    for match in re.finditer(r'```.*?```|~~~.*?~~~|`[^`\n]+`', value, re.S):
        part, mapped = prose(value[previous:match.start()], source[previous:match.start()])
        output.extend([part, match[0]]); positions.extend(mapped); positions.extend(source[match.start():match.end()])
        previous = match.end()
    part, mapped = prose(value[previous:], source[previous:])
    output.append(part); positions.extend(mapped)
    return ''.join(output), positions


def _fold_whitespace(value):
    output, positions = [], []
    for match in re.finditer(r'\s+|\S+', value):
        if match[0].isspace():
            output.append(' '); positions.append((match.start(), match.end()))
        else:
            output.append(match[0]); positions.extend((i, i+1) for i in range(match.start(), match.end()))
    return ''.join(output), positions


def resolve_display_quote(quote, text):
    """Whitespace-only display match, projected to auditable raw-source spans.

    No case/punctuation normalization or fuzzy/semantic matching. A destination
    marker must be complete and have source-grounded words on both sides. Other
    generated markers, and quotes consisting only of whitespace, are refused.
    """
    display, positions = mapped_view(text)
    folded, folded_positions = _fold_whitespace(display)
    candidate = _fold_whitespace(quote)[0].strip()
    require(candidate, 'missing-evidence')
    start = folded.find(candidate)
    require(start >= 0, 'unsupported-evidence')
    view_start = folded_positions[start][0]
    view_end = folded_positions[start+len(candidate)-1][1]
    selected = positions[view_start:view_end]
    require(selected and any(kind == 'source' for _, _, kind in selected), 'quote-generated-only')

    # Inspect complete generated runs, so partial marker substrings cannot pass.
    i = 0
    while i < len(positions):
        span = positions[i]
        if span[2] == 'source':
            i += 1; continue
        end = i+1
        while end < len(positions) and positions[end] == span: end += 1
        if i < view_end and end > view_start:
            require(span[2] == 'redacted_destination', 'quote-unavailable-content')
            require(view_start <= i and end <= view_end, 'quote-partial-marker')
            left = any(display[j].isalnum() and positions[j][2] == 'source' for j in range(view_start, i))
            right = any(display[j].isalnum() and positions[j][2] == 'source' for j in range(end, view_end))
            require(left and right, 'quote-unanchored-marker')
        i = end

    # Image alternative text can map inside an omitted image, but the generated
    # image wrapper above is never accepted. Every accepted interval is in body.
    raw_start, raw_end = selected[0][0], selected[-1][1]
    require(len(text)-len(body(text)) <= raw_start < raw_end <= len(text), 'quote-source-bounds')
    require(all(a <= b and a >= raw_start and b <= raw_end for a, b, _ in selected), 'quote-source-order')
    segments = []
    for a, b, kind in selected:
        if segments and segments[-1]['kind'] == kind and a <= segments[-1]['end']:
            require(a >= segments[-1]['start'], 'quote-source-order')
            segments[-1]['end'] = max(b, segments[-1]['end'])
        else:
            require(not segments or a >= segments[-1]['end'], 'quote-source-order')
            segments.append({'start': a, 'end': b, 'kind': kind})
    return {'start': raw_start, 'end': raw_end, 'unit': 'decoded_unicode_codepoints',
            'match_policy': 'first_whitespace_folded_display_occurrence.v2',
            'occurrences': folded.count(candidate), 'view_start': view_start, 'view_end': view_end,
            'view_sha256': raw_hash(display), 'display_quote': display[view_start:view_end],
            'source_quote': text[raw_start:raw_end], 'source_segments': segments,
            'whitespace_normalized': quote != display[view_start:view_end],
            'display_transformed': display[view_start:view_end] != text[raw_start:raw_end]}


def leakage_groups(pages, texts, *, threshold, shingle_words, minimum_tokens):
    """Conservative transitive union: declared source, normalized body, includes,
    and full 5-word-shingle Jaccard. Empty bodies form one unavailable family.
    Parameters are supplied by the study before any labels are read.
    """
    ids = sorted(texts)
    require(ids == sorted(p['doc_id'] for p in pages), 'group-page-join')
    require(0 < threshold <= 1 and shingle_words >= 1 and minimum_tokens >= shingle_words,
            'group-policy')
    parent = {v: v for v in ids}
    def find(v):
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v
    reasons = []
    def union(a, b, reason):
        x, y = find(a), find(b)
        if x != y:
            parent[max(x, y)] = min(x, y)
            reasons.append({'left': a, 'right': b, 'reason': reason})
    seen = {}
    shingles = {}
    for page in sorted(pages, key=lambda p: p['doc_id']):
        v = page['doc_id']
        prose, _ = view(texts[v])
        tokens = re.findall(r'\b\w+\b', prose.lower())
        signature = ' '.join(tokens)
        keys = [('source', page['source_family']), ('normalized_body', raw_hash(signature))]
        keys += [('include', inc) for inc in re.findall(
            r'{{[<%]\s*(?:include|readfile)\s+([^}]+)[%>]}}', body(texts[v]))]
        for key in keys:
            if key in seen:
                union(v, seen[key], key[0])
            else:
                seen[key] = v
        shingles[v] = {tuple(tokens[i:i+shingle_words]) for i in range(len(tokens)-shingle_words+1)} if len(tokens) >= minimum_tokens else set()
    # Exact all-pairs comparison: no stochastic LSH recall assumption.
    for i, a in enumerate(ids):
        left = shingles[a]
        if not left:
            continue
        for b in ids[i+1:]:
            right = shingles[b]
            if not right or min(len(left), len(right)) / max(len(left), len(right)) < threshold:
                continue
            common = len(left & right)
            if common / (len(left) + len(right) - common) >= threshold:
                union(a, b, 'shingle_jaccard')
    groups = {v: 'g-' + raw_hash(find(v))[:20] for v in ids}
    return {'groups': groups, 'unions': reasons, 'policy': {
        'threshold': threshold, 'shingle_words': shingle_words, 'minimum_tokens': minimum_tokens,
        'source_family_union': True, 'empty_body_union': True}}


def sample_pages(pages, groups, discovery, count, seed):
    """Hamilton proportional page allocation, then SRS without replacement per
    stratum. Small strata may receive zero: report rather than silently enrich.
    """
    require(type(count) is int and count > 0, 'sample-count')
    require(set(discovery) <= set(groups), 'discovery-join')
    excluded = {groups[v] for v in discovery}
    strata = defaultdict(list)
    for p in sorted(pages, key=lambda p: p['doc_id']):
        if groups[p['doc_id']] not in excluded:
            strata[p['topic_stratum']].append(p['doc_id'])
    total = sum(map(len, strata.values()))
    require(count <= total, 'sample-population')
    allocation = {s: count * len(v) // total for s, v in strata.items()}
    remaining = count - sum(allocation.values())
    order = sorted(strata, key=lambda s: (-(count * len(strata[s]) % total), s))
    for s in order[:remaining]:
        allocation[s] += 1
    rng = random.Random(seed)
    chosen = []
    for s in sorted(strata):
        for v in sorted(rng.sample(strata[s], allocation[s])):
            chosen.append({'doc_id': v, 'stratum': s, 'group': groups[v],
                           'inclusion_probability': allocation[s] / len(strata[s])})
    return {'seed': seed, 'population': total, 'excluded_groups': sorted(excluded),
            'excluded_pages': sorted(v for v in groups if groups[v] in excluded),
            'allocation': {s: {'population': len(strata[s]), 'sample': allocation[s]} for s in sorted(strata)},
            'rows': chosen, 'rule': 'Hamilton proportions, lexicographic ties, within-stratum SRS'}


def export_blind(ids, texts, groups, rubric_sha256, salt):
    cards, keys = [], {}
    for v in ids:
        card_id = 'P-' + digest([salt, v])[:16]
        require(card_id not in keys, 'card-collision')
        prose, flags = view(texts[v])
        cards.append({'card_id': card_id, 'text': prose, 'availability': flags})
        keys[card_id] = {'doc_id': v, 'raw_sha256': raw_hash(texts[v]), 'group': groups[v],
                         'view_sha256': raw_hash(prose)}
    cards.sort(key=lambda c: c['card_id'])
    packet = {'schema_version': VERSION, 'rubric_sha256': rubric_sha256, 'cards': cards}
    packet_id = digest(packet)
    packet['packet_sha256'] = packet_id
    key = {'packet_sha256': packet_id, 'rubric_sha256': rubric_sha256, 'cards': keys}
    form = {'packet_sha256': packet_id, 'rubric_sha256': rubric_sha256,
            'reviewer': None, 'kind': 'initial_human', 'rows': [
                {'card_id': c['card_id'], 'role': None, 'note': '', 'seconds': None} for c in cards]}
    return packet, key, form


def import_human(form, packet, key, roles, texts, *, previous=None):
    expected = digest({k: v for k, v in packet.items() if k != 'packet_sha256'})
    require(expected == packet['packet_sha256'] == key['packet_sha256'] == form['packet_sha256'], 'stale-packet')
    require(form['rubric_sha256'] == packet['rubric_sha256'] == key['rubric_sha256'], 'stale-rubric')
    require(form['kind'] == 'initial_human' and isinstance(form['reviewer'], str) and form['reviewer'].strip(), 'human-provenance')
    require({c['card_id'] for c in packet['cards']} == set(key['cards']), 'key-join')
    ids = [r['card_id'] for r in form['rows']]
    require(len(ids) == len(set(ids)) and set(ids) == set(key['cards']), 'human-card-join')
    output = []
    for row in form['rows']:
        require(set(row) == {'card_id', 'role', 'note', 'seconds'}, 'human-row-fields')
        require(row['role'] is None or row['role'] in roles, 'human-role')
        require(isinstance(row['note'], str), 'human-note')
        require(row['seconds'] is None or type(row['seconds']) in (int, float) and 0 <= row['seconds'] < 86400, 'human-timing')
        k = key['cards'][row['card_id']]
        require(raw_hash(texts[k['doc_id']]) == k['raw_sha256'] and raw_hash(view(texts[k['doc_id']])[0]) == k['view_sha256'], 'stale-content')
        output.append({**k, **row, 'reviewer': form['reviewer'], 'rubric_sha256': form['rubric_sha256'], 'kind': 'initial_human'})
    result = {'schema_version': VERSION, 'packet_sha256': expected, 'rows': output,
              'resolved': sum(r['role'] is not None for r in output), 'input_sha256': digest(form)}
    require(previous is None or digest(previous) == digest(result), 'conflicting-human-import-use-new-version')
    return result


def _facet_specs(facets):
    facets = {} if facets is None else facets
    require(isinstance(facets, dict), 'annotation-facet-config')
    for name, spec in facets.items():
        require(isinstance(name, str) and re.fullmatch(r'[a-z][a-z0-9_]*', name) is not None and isinstance(spec, dict), 'annotation-facet-config')
        values = spec.get('values')
        require(isinstance(values, list) and values and all(isinstance(v, str) and v.strip() for v in values), 'annotation-facet-values')
        require(len(values) == len(set(values)) and spec.get('missing_value') in values, 'annotation-facet-values')
    return facets


def annotation_schema(roles, facets=None):
    facets = _facet_specs(facets)
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['role', 'quote', 'rationale', 'ambiguity', 'incomplete'],
            'properties': {'role': {'type': 'string', 'enum': roles}, 'quote': {'type': 'string'},
                           'rationale': {'type': 'string'}, 'ambiguity': {'type': 'boolean'}, 'incomplete': {'type': 'boolean'}}}
    if facets:
        schema['required'].append('facets')
        schema['properties']['facets'] = {'type': 'object', 'additionalProperties': False, 'required': sorted(facets),
            'properties': {name: {'type': 'string', 'enum': spec['values']} for name, spec in sorted(facets.items())}}
    return schema


def validate_annotation(value, text, roles, unknown_role, *, evidence_policy=STRICT_EVIDENCE, facets=None):
    require(evidence_policy in (STRICT_EVIDENCE, MAPPED_EVIDENCE), 'annotation-evidence-policy')
    facets = _facet_specs(facets)
    expected = {'role', 'quote', 'rationale', 'ambiguity', 'incomplete'} | ({'facets'} if facets else set())
    require(isinstance(value, dict) and set(value) == expected, 'annotation-fields')
    require(value['role'] in roles and type(value['ambiguity']) is bool and type(value['incomplete']) is bool, 'annotation-role-flags')
    require(all(isinstance(value[k], str) for k in ('quote', 'rationale')), 'annotation-strings')
    require(0 < len(value['rationale']) <= 1200 and len(value['quote']) <= 1200, 'annotation-length')
    quote = value['quote']
    prose, _ = view(text)
    if facets:
        require(isinstance(value['facets'], dict) and set(value['facets']) == set(facets), 'annotation-facet-fields')
        require(all(value['facets'][name] in spec['values'] for name, spec in facets.items()), 'annotation-facet-value')
        if not prose.strip():
            require(all(value['facets'][name] == spec['missing_value'] for name, spec in facets.items()), 'annotation-facet-missing-content')
    if quote:
        if evidence_policy == MAPPED_EVIDENCE:
            span = resolve_display_quote(quote, text)
        else:
            require(quote in prose and quote in body(text), 'unsupported-evidence')
            start = text.index(quote, len(text)-len(body(text)))
            span = {'start': start, 'end': start+len(quote), 'unit': 'decoded_unicode_codepoints',
                    'match_policy': 'first_exact_body_occurrence', 'occurrences': body(text).count(quote)}
    else:
        require(value['role'] == unknown_role and value['incomplete'], 'missing-evidence')
        span = None
    require(value['role'] == unknown_role or prose.strip(), 'empty-content-role')
    result = {**value, 'evidence_span': span}
    if evidence_policy == MAPPED_EVIDENCE: result['evidence_policy'] = evidence_policy
    return result


def cache_identity(text, rubric, prompt, settings, roles, *, evidence_policy=STRICT_EVIDENCE, facets=None):
    require(evidence_policy in (STRICT_EVIDENCE, MAPPED_EVIDENCE), 'annotation-evidence-policy')
    identity = {'raw_sha256': raw_hash(text), 'view_sha256': raw_hash(view(text)[0]),
                   'rubric_sha256': digest(rubric), 'prompt_sha256': raw_hash(prompt),
                   'settings': settings, 'schema': annotation_schema(roles, facets), 'version': VERSION}
    if evidence_policy == MAPPED_EVIDENCE: identity['evidence_policy'] = evidence_policy
    return digest(identity)


async def run_batch(texts, rubric, prompt, manifest, approval, output, transport):
    """Bounded sequential first-valid-attempt policy. Persist starts before calls;
    interrupted starts consume budget. Every attempt, including schema failures,
    counts. No automatic provider/model fallback. Fake transports are for tests.
    """
    require(approval.get('decision') == 'yes' and approval.get('author') and approval.get('record')
            and approval.get('manifest_sha256') == digest(manifest), 'annotation-not-authorized')
    require(manifest['rubric_sha256'] == digest(rubric) and manifest['prompt_sha256'] == raw_hash(prompt), 'annotation-protocol-identity')
    facets = _facet_specs(rubric.get('facets'))
    require(manifest['schema_sha256'] == digest(annotation_schema(rubric['roles'], facets)), 'annotation-schema-identity')
    evidence_policy = manifest.get('evidence_policy', STRICT_EVIDENCE)
    require(evidence_policy in (STRICT_EVIDENCE, MAPPED_EVIDENCE), 'annotation-evidence-policy')
    require(manifest['page_hashes'] == {v: raw_hash(s) for v, s in sorted(texts.items())}, 'annotation-page-identity')
    require(manifest['max_pages'] >= len(texts) and 0 <= manifest['retries'] <= 2, 'annotation-page-retry-limit')
    require(0 < manifest['max_attempts'] <= 10000 and 0 < manifest['max_seconds'] <= 14400, 'annotation-budget')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    binding = output/'manifest.json'
    if binding.exists():
        require(json.loads(binding.read_bytes()) == manifest, 'resume-manifest-changed')
    else:
        binding.write_bytes(canonical(manifest)+b'\n')
    journal = output/'attempts.jsonl'
    events = [json.loads(s) for s in journal.read_bytes().splitlines()] if journal.exists() else []
    starts = [e for e in events if e['event'] == 'start']
    completed = {e['doc_id']: e for e in events if e['event'] == 'valid'}
    # A crash may have spent the entire reserved duration; require explicit new
    # manifest/output for ambiguous interrupted calls rather than reset a clock.
    finished = {e['attempt_id'] for e in events if e['event'] in ('valid', 'invalid')}
    require(all(e['attempt_id'] in finished for e in starts), 'interrupted-attempt-review-required')
    elapsed_prior = sum(e.get('elapsed_seconds', 0) for e in events if e['event'] in ('valid', 'invalid'))
    started = time.monotonic()
    def emit(e):
        with journal.open('ab') as stream:
            stream.write(canonical(e)+b'\n'); stream.flush()
        events.append(e)
    roles = rubric['roles']; settings = manifest['settings']
    for v, text in sorted(texts.items()):
        identity = cache_identity(text, rubric, prompt, settings, roles, evidence_policy=evidence_policy, facets=facets)
        if v in completed:
            require(completed[v]['cache_identity'] == identity, 'stale-cache')
            continue
        attempts = sum(e['doc_id'] == v for e in starts)
        while attempts <= manifest['retries']:
            require(len(starts) < manifest['max_attempts'], 'annotation-attempt-budget')
            remaining = manifest['max_seconds'] - elapsed_prior - (time.monotonic()-started)
            require(remaining > 0, 'annotation-runtime-budget')
            prose, flags = view(text)
            request = prompt + '\nRUBRIC\n' + canonical(rubric).decode() + '\nUNTRUSTED PAGE DATA\n' + prose
            require(len(request.encode('utf-8')) <= settings['max_input_tokens'], 'annotation-context-overflow-no-truncation')
            attempt_id = digest([digest(manifest), v, attempts])
            start = {'event': 'start', 'doc_id': v, 'attempt_id': attempt_id, 'cache_identity': identity,
                     'timestamp': datetime.now(timezone.utc).isoformat(), 'availability': flags}
            emit(start); starts.append(start); attempts += 1
            call_start = time.monotonic()
            try:
                import asyncio
                reply = await asyncio.wait_for(transport(request, annotation_schema(roles, facets), settings), timeout=min(remaining, settings['timeout_seconds']))
                # Save raw response before schema validation. Provider layer checks
                # digest/response identity and records spend, including failures.
                (output/(attempt_id+'.json')).write_bytes(canonical(reply)+b'\n')
                require(reply['model'] == settings['model'] and reply['revision'] == settings['revision'], 'annotation-model-identity')
                result = validate_annotation(json.loads(reply['text']), text, roles, rubric['unknown_role'], evidence_policy=evidence_policy, facets=facets)
            except Exception as error:
                # Error text may contain provider/user data; retain type only.
                emit({'event': 'invalid', 'doc_id': v, 'attempt_id': attempt_id,
                      'error_type': type(error).__name__, 'elapsed_seconds': time.monotonic()-call_start})
                if any(token in str(error) for token in ('digest-mismatch', 'model-absent', 'annotation-model-identity', 'response-model', 'network-not-admitted', 'public-upstream')):
                    raise ValueError('annotation-provider-identity-stop') from None
                continue
            event = {'event': 'valid', 'doc_id': v, 'attempt_id': attempt_id, 'cache_identity': identity,
                     'elapsed_seconds': time.monotonic()-call_start, 'annotation': result,
                     'raw_sha256': raw_hash(text), 'rubric_sha256': digest(rubric), 'model': settings['model'],
                     'revision': settings['revision'], 'timestamp': datetime.now(timezone.utc).isoformat()}
            emit(event); completed[v] = event
            break
    return {'schema_version': VERSION_V2 if evidence_policy == MAPPED_EVIDENCE or facets else VERSION,
            'manifest_sha256': digest(manifest), 'attempts': len(starts),
            'annotations': list(completed.values()), 'unresolved': sorted(set(texts)-set(completed))}
