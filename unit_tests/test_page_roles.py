import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from retrieval_arena import page_role_annotation as pa
from retrieval_arena import page_role_evaluation as pe

RUBRIC = {'roles': ['overview', 'procedure', 'unknown'], 'unknown_role': 'unknown', 'rules': 'Read purpose.'}
SETTINGS = {'provider': 'ollama', 'model': 'fixture', 'revision': 'a'*64,
            'max_input_tokens': 10000, 'max_output_tokens': 200, 'timeout_seconds': 30}


def batch_manifest(texts, retries=1):
    return {'rubric_sha256': pa.digest(RUBRIC), 'prompt_sha256': pa.raw_hash('prompt'),
            'schema_sha256': pa.digest(pa.annotation_schema(RUBRIC['roles'])),
            'page_hashes': {v: pa.raw_hash(s) for v, s in sorted(texts.items())}, 'settings': SETTINGS,
            'max_pages': len(texts), 'max_attempts': 10, 'max_seconds': 30, 'retries': retries}


def approval(manifest):
    return {'decision': 'yes', 'author': 'fixture only', 'record': 'synthetic', 'manifest_sha256': pa.digest(manifest)}


def annotation(role='procedure', quote='Run this.', incomplete=False):
    return {'role': role, 'quote': quote, 'rationale': 'Ordered action.', 'ambiguity': False, 'incomplete': incomplete}


def fixture_reply(value):
    return {'text': json.dumps(value), 'model': SETTINGS['model'], 'revision': SETTINGS['revision'], 'usage': {}, 'cost': None}


class AnnotationTests(unittest.TestCase):
    def test_new_command_has_mount_and_resource_admission_in_both_launchers(self):
        root = Path(__file__).resolve().parents[1]
        powershell = (root/'scripts/run-verification.ps1').read_text()
        mount_line = next(line for line in powershell.splitlines() if line.startswith("if ($Command -in @('evaluate-profile'"))
        self.assertIn("-or $Command -eq 'page-roles'", mount_line)
        self.assertIn("if ($Command -eq 'page-roles')", powershell)
        shell = (root/'scripts/run-verification.sh').read_text()
        self.assertIn('[ "$command_name" != page-roles ] || operator_memory=8g', shell)
        self.assertIn('[ "$command_name" != page-roles ] || operator_cpus=4', shell)

    def test_view_blinding_and_full_length(self):
        text = '---\nrole: procedure\npath: secret\n---\n# Header\nSee [visible](hidden-path)\n```sh\nrun thing\n```\n{{< include x >}}'
        prose, flags = pa.view(text)
        self.assertNotIn('secret', prose); self.assertNotIn('hidden-path', prose)
        self.assertIn('# Header', prose); self.assertIn('run thing', prose)
        self.assertIn('curl https://example.test/<value>', pa.view('```sh\ncurl https://example.test/<value>\n```')[0])
        self.assertTrue(flags['unrendered_template'])
        self.assertEqual(len(pa.view('x'*200000)[0]), 200000)

    def test_cache_binds_content_rubric_prompt_schema_and_settings(self):
        args = ['page', RUBRIC, 'prompt', SETTINGS, RUBRIC['roles']]
        original = pa.cache_identity(*args)
        for i, replacement in enumerate(['other', {**RUBRIC, 'rules': 'changed'}, 'other', {**SETTINGS, 'revision': 'b'*64}, ['new']]):
            changed = args.copy(); changed[i] = replacement
            self.assertNotEqual(pa.cache_identity(*changed), original)

    def test_unicode_raw_evidence_offsets(self):
        text = '---\r\ntitle: hidden\r\n---\r\né Ω\r\nRun this.\r\n'
        value = pa.validate_annotation(annotation(), text, RUBRIC['roles'], 'unknown')
        span = value['evidence_span']
        self.assertEqual(text[span['start']:span['end']], 'Run this.')

    def test_refuses_hallucinated_and_metadata_evidence(self):
        for quote in ['invented', 'title: hidden']:
            with self.assertRaises(ValueError):
                pa.validate_annotation(annotation(quote=quote), '---\ntitle: hidden\n---\nRun this.', RUBRIC['roles'], 'unknown')

    def test_malformed_unsupported_missing_annotation(self):
        for value in [{}, annotation(role='madeup'), annotation(quote=''), {**annotation(), 'incomplete': 'no'}]:
            with self.assertRaises(ValueError):
                pa.validate_annotation(value, 'Run this.', RUBRIC['roles'], 'unknown')
        self.assertIsNone(pa.validate_annotation(annotation('unknown', '', True), '', RUBRIC['roles'], 'unknown')['evidence_span'])

    def test_groups_union_duplicate_source_and_discovery_exclusion(self):
        texts = {'a': 'shared five token words are here', 'b': 'shared five token words are here',
                 'c': 'different body', 'd': 'an entirely distinct fourth body'}
        pages = [{'doc_id': v, 'source_family': 'same' if v in 'bc' else v, 'topic_stratum': 'all'} for v in texts]
        groups = pa.leakage_groups(pages, texts, threshold=.8, shingle_words=3, minimum_tokens=4)['groups']
        self.assertEqual(groups['a'], groups['c'])
        sampled = pa.sample_pages(pages, groups, ['a'], 1, 123)
        self.assertEqual(sampled['rows'][0]['doc_id'], 'd')
        self.assertEqual(sampled['excluded_pages'], ['a', 'b', 'c'])

    def test_template_family_without_path_labeling(self):
        texts = {'a': '{{< include "same" >}} alpha', 'b': '{{< include "same" >}} beta'}
        pages = [{'doc_id': v, 'source_family': v} for v in texts]
        result = pa.leakage_groups(pages, texts, threshold=.8, shingle_words=3, minimum_tokens=4)
        self.assertEqual(len(set(result['groups'].values())), 1)

    def test_sampling_reproducible_proportional_probabilities(self):
        pages = [{'doc_id': str(i), 'source_family': str(i), 'topic_stratum': 'a' if i < 8 else 'b'} for i in range(12)]
        groups = {str(i): str(i) for i in range(12)}
        result = pa.sample_pages(pages, groups, [], 6, 5)
        self.assertEqual(result, pa.sample_pages(list(reversed(pages)), groups, [], 6, 5))
        self.assertEqual(result['allocation']['a']['sample'], 4)
        self.assertTrue(all(r['inclusion_probability'] == .5 for r in result['rows']))

    def packet(self):
        texts = {'secret_path': '# Human-readable\nRun this.'}
        packet, key, form = pa.export_blind(list(texts), texts, {'secret_path': 'group'}, pa.digest(RUBRIC), 'salt')
        return texts, packet, key, form

    def test_blind_export_import_blank_unresolved_and_conflict(self):
        texts, packet, key, form = self.packet()
        self.assertNotIn('secret_path', json.dumps(packet)); self.assertNotIn('group', json.dumps(packet))
        with self.assertRaises(ValueError):
            pa.import_human(form, packet, key, RUBRIC['roles'], texts)
        form['reviewer'] = 'fixture-human'
        blank = pa.import_human(form, packet, key, RUBRIC['roles'], texts)
        self.assertEqual(blank['resolved'], 0)
        form['rows'][0]['role'] = 'procedure'
        result = pa.import_human(form, packet, key, RUBRIC['roles'], texts)
        self.assertEqual(result['resolved'], 1)
        with self.assertRaises(ValueError):
            pa.import_human(form, packet, key, RUBRIC['roles'], texts, previous=blank)

    def test_stale_content_packet_rubric_and_duplicate_review(self):
        for change in ('text', 'packet', 'rubric', 'duplicate'):
            texts, packet, key, form = self.packet(); form['reviewer'] = 'fixture'
            if change == 'text': texts['secret_path'] += 'changed'
            if change == 'packet': packet['cards'][0]['text'] += 'changed'
            if change == 'rubric': form['rubric_sha256'] = 'bad'
            if change == 'duplicate': form['rows'] *= 2
            with self.assertRaises(ValueError):
                pa.import_human(form, packet, key, RUBRIC['roles'], texts)

    def test_batch_approval_first_valid_resume_and_retry_limit(self):
        calls = []
        async def transport(*args):
            calls.append(1)
            return fixture_reply(annotation() if len(calls) > 1 else annotation(quote='wrong'))
        texts = {'a': 'Run this.'}; manifest = batch_manifest(texts)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                asyncio.run(pa.run_batch(texts, RUBRIC, 'prompt', manifest, {}, tmp, transport))
            self.assertEqual(calls, [])
            result = asyncio.run(pa.run_batch(texts, RUBRIC, 'prompt', manifest, approval(manifest), tmp, transport))
            self.assertEqual(result['attempts'], 2); self.assertEqual(len(result['annotations']), 1)
            result2 = asyncio.run(pa.run_batch(texts, RUBRIC, 'prompt', manifest, approval(manifest), tmp, transport))
            self.assertEqual(result, result2); self.assertEqual(len(calls), 2)
        async def invalid(*args): return fixture_reply(annotation(quote='wrong'))
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(pa.run_batch(texts, RUBRIC, 'prompt', manifest, approval(manifest), tmp, invalid))
            self.assertEqual(result['attempts'], 2); self.assertEqual(result['unresolved'], ['a'])

    def test_batch_overflow_no_truncation_and_changed_manifest(self):
        async def never(*args): self.fail('transport called')
        texts = {'a': 'x'*10001}; manifest = batch_manifest(texts)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'overflow'):
                asyncio.run(pa.run_batch(texts, RUBRIC, 'prompt', manifest, approval(manifest), tmp, never))
            changed = {**manifest, 'retries': 0}
            with self.assertRaisesRegex(ValueError, 'resume-manifest'):
                asyncio.run(pa.run_batch(texts, RUBRIC, 'prompt', changed, approval(changed), tmp, never))

    def test_interrupted_attempt_consumes_budget_and_requires_review(self):
        manifest = batch_manifest({'a': 'Run this.'})
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'manifest.json').write_bytes(pa.canonical(manifest))
            Path(tmp, 'attempts.jsonl').write_bytes(pa.canonical({'event': 'start', 'attempt_id': 'old', 'doc_id': 'a'})+b'\n')
            with self.assertRaisesRegex(ValueError, 'interrupted'):
                asyncio.run(pa.run_batch({'a': 'Run this.'}, RUBRIC, 'prompt', manifest, approval(manifest), tmp, None))

    def test_provider_identity_failure_is_a_hard_stop(self):
        calls = []
        async def wrong_model(*args):
            calls.append(1)
            return {**fixture_reply(annotation()), 'revision': 'wrong'}
        manifest = batch_manifest({'a': 'Run this.'})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'identity-stop'):
                asyncio.run(pa.run_batch({'a': 'Run this.'}, RUBRIC, 'prompt', manifest, approval(manifest), tmp, wrong_model))
            self.assertEqual(len(calls), 1)

    def test_local_launcher_refuses_before_docker_without_author_yes(self):
        spec = importlib.util.spec_from_file_location('role_launcher', Path(__file__).resolve().parents[1]/'scripts/run-page-role-batch.py')
        launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp, 'input'); inp.mkdir()
            (inp/'manifest.json').write_bytes(pa.canonical(batch_manifest({'a': 'Run this.'})))
            (inp/'approval.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'not-authorized'):
                launcher.launch(inp, Path(tmp, 'output'), Path(tmp, 'missing-receipt.json'))


class QuoteProjectionTests(unittest.TestCase):
    def validate(self, quote, text):
        return pa.validate_annotation(annotation(quote=quote), text, RUBRIC['roles'], 'unknown',
                                      evidence_policy=pa.MAPPED_EVIDENCE)

    def test_mapped_view_reproduces_every_display_transform_and_literal_code(self):
        text = ('---\r\ntitle: hidden\r\n---\r\n# Heading\r\n'
                'Before <!-- hidden -->after {{< include "missing" >}}.\n'
                '![Alt label](image.png) [link label](private-path)\n'
                '[id]: https://hidden.test\n'
                'Get https://example.test/path now. <span>Visible</span>\n'
                '```xml\n<node>https://literal.test</node>\n```\n'
                '`<inline>` and ~~~text\nhttps://literal.test\n~~~')
        display, positions = pa.mapped_view(text)
        self.assertEqual(display, pa.view(text)[0])
        self.assertEqual(len(display), len(positions))
        for char, (start, end, kind) in zip(display, positions):
            self.assertGreaterEqual(start, len(text)-len(pa.body(text)))
            if kind == 'source': self.assertEqual(text[start:end], char)

    def test_crlf_unicode_and_multiline_quote_have_exact_raw_offsets(self):
        text = '---\r\ntitle: hidden\r\n---\r\né Ω\r\nRun this.\r\n\r\nCheck it.'
        quote = 'é Ω\nRun this.\n\nCheck it.'
        result = self.validate(quote, text); span = result['evidence_span']
        self.assertEqual(result['quote'], quote)
        self.assertEqual(span['source_quote'], 'é Ω\r\nRun this.\r\n\r\nCheck it.')
        self.assertEqual(text[span['start']:span['end']], span['source_quote'])
        self.assertTrue(span['whitespace_normalized'])
        self.assertEqual(span['source_segments'], [{'start': text.index('é'), 'end': len(text), 'kind': 'source'}])

    def test_wrapping_tabs_and_quoted_edge_whitespace_only_are_normalized(self):
        text = 'Run the\r\n\t small task.'
        result = self.validate('  Run the small task. \n', text)
        self.assertEqual(result['evidence_span']['source_quote'], text)
        self.assertEqual(result['quote'], '  Run the small task. \n')

    def test_link_label_projection_keeps_raw_markdown_and_destinations_explicit(self):
        text = 'Run [this task](hidden/path) and check it.'
        result = self.validate('Run this task and check it.', text); span = result['evidence_span']
        self.assertEqual(span['source_quote'], text)
        self.assertTrue(span['display_transformed'])
        self.assertNotIn('hidden/path', span['display_quote'])
        self.assertGreater(len(span['source_segments']), 1)

    def test_destination_marker_maps_to_its_real_url_with_two_sided_anchors(self):
        text = 'Docker provides a script at\r\nhttps://example.test/install\r\nto install software.'
        quote = 'Docker provides a script at [destination omitted] to install software.'
        span = self.validate(quote, text)['evidence_span']
        self.assertEqual(span['source_quote'], text)
        self.assertEqual(text[span['start']:span['end']], text)
        hidden = next(s for s in span['source_segments'] if s['kind'] == 'redacted_destination')
        self.assertEqual(text[hidden['start']:hidden['end']], 'https://example.test/install')

    def test_generated_or_partial_markers_and_hidden_destinations_are_not_evidence(self):
        text = 'Use https://secret.test/path for setup.'
        for quote in ['[destination omitted]', 'destination omitted', 'omitted] for setup.',
                      '[destination omitted] for setup.', 'Use [destination omitted]', 'secret.test/path']:
            with self.subTest(quote=quote), self.assertRaises(ValueError): self.validate(quote, text)
        with self.assertRaises(ValueError): self.validate('[destination omitted]', 'https://secret.test/path')

    def test_unavailable_template_image_or_markup_cannot_supply_evidence(self):
        for text, quote in [('Before {{< missing >}} after', 'Before [unrendered template] after'),
                            ('Before ![diagram](image) after', 'Before [image unavailable: diagram] after'),
                            ('Before <tag> after', 'Before [markup omitted] after')]:
            with self.subTest(text=text), self.assertRaises(ValueError): self.validate(quote, text)
        # Literal text is different from a generated display marker.
        self.assertEqual(self.validate('[destination omitted]', 'The literal string [destination omitted] is documented.')['evidence_span']['source_quote'], '[destination omitted]')

    def test_no_fuzzy_semantic_case_or_punctuation_matching_or_metadata_evidence(self):
        text = '---\ntitle: Metadata only\n---\nRun the task. Check success.'
        for quote in ['Metadata only', 'run the task.', 'Run the task!', 'Run task.', 'Check the task.']:
            with self.subTest(quote=quote), self.assertRaises(ValueError): self.validate(quote, text)

    def test_duplicate_quote_selects_first_visible_body_occurrence(self):
        text = '---\ntitle: Run this.\n---\nRun this.\r\nRun this.'
        span = self.validate('Run this.', text)['evidence_span']
        self.assertEqual(span['start'], text.index('Run this.', len(text)-len(pa.body(text))))
        self.assertEqual(span['occurrences'], 2)

    def test_v1_legacy_hash_and_verdict_are_unchanged_and_policies_do_not_share_cache(self):
        text = 'Run\r\nthis.'
        args = (text, RUBRIC, 'prompt', SETTINGS, RUBRIC['roles'])
        original = pa.digest({'raw_sha256': pa.raw_hash(text), 'view_sha256': pa.raw_hash(pa.view(text)[0]),
            'rubric_sha256': pa.digest(RUBRIC), 'prompt_sha256': pa.raw_hash('prompt'), 'settings': SETTINGS,
            'schema': pa.annotation_schema(RUBRIC['roles']), 'version': pa.VERSION})
        self.assertEqual(pa.cache_identity(*args), original)
        self.assertNotEqual(original, pa.cache_identity(*args, evidence_policy=pa.MAPPED_EVIDENCE))
        with self.assertRaises(ValueError): pa.validate_annotation(annotation(quote='Run\nthis.'), text, RUBRIC['roles'], 'unknown')
        self.validate('Run\nthis.', text)
        with self.assertRaises(ValueError): pa.cache_identity(*args, evidence_policy='unknown')

    def test_bounded_batch_uses_manifest_policy_and_preserves_returned_quote(self):
        quote = 'Run this.'; texts = {'a': 'Run\r\nthis.'}
        manifest = {**batch_manifest(texts), 'evidence_policy': pa.MAPPED_EVIDENCE}
        async def fixture(*args): return fixture_reply(annotation(quote=quote))
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(pa.run_batch(texts, RUBRIC, 'prompt', manifest, approval(manifest), tmp, fixture))
            self.assertEqual(result['schema_version'], pa.VERSION_V2)
            self.assertEqual(result['attempts'], 1)
            value = result['annotations'][0]['annotation']
            self.assertEqual(value['quote'], quote)
            self.assertEqual(value['evidence_span']['source_quote'], texts['a'])
            self.assertEqual(value['evidence_policy'], pa.MAPPED_EVIDENCE)
            changed = {**manifest, 'evidence_policy': pa.STRICT_EVIDENCE}
            with self.assertRaisesRegex(ValueError, 'resume-manifest-changed'):
                asyncio.run(pa.run_batch(texts, RUBRIC, 'prompt', changed, approval(changed), tmp, fixture))

    def test_unknown_empty_quote_stays_explicitly_incomplete(self):
        value = pa.validate_annotation(annotation('unknown', '', True), '', RUBRIC['roles'], 'unknown', evidence_policy=pa.MAPPED_EVIDENCE)
        self.assertIsNone(value['evidence_span'])
        self.assertEqual(value['evidence_policy'], pa.MAPPED_EVIDENCE)


class FacetAnnotationTests(unittest.TestCase):
    FACETS = {'content_scope': {'values': ['product', 'about_meta', 'mixed', 'unknown'], 'missing_value': 'unknown'}}

    def test_schema_requires_declared_facets_without_changing_legacy_schema(self):
        old = pa.annotation_schema(RUBRIC['roles'])
        self.assertEqual(set(old['required']), {'role', 'quote', 'rationale', 'ambiguity', 'incomplete'})
        self.assertEqual(old, pa.annotation_schema(RUBRIC['roles'], {}))
        new = pa.annotation_schema(RUBRIC['roles'], self.FACETS)
        self.assertIn('facets', new['required'])
        self.assertEqual(new['properties']['facets']['required'], ['content_scope'])
        self.assertEqual(new['properties']['facets']['properties']['content_scope']['enum'], self.FACETS['content_scope']['values'])
        self.assertEqual(new['properties']['role'], old['properties']['role'])

    def test_scope_and_purpose_are_independent_and_scope_is_not_a_role(self):
        for scope in ['product', 'about_meta']:
            value = {**annotation(), 'facets': {'content_scope': scope}}
            result = pa.validate_annotation(value, 'Run this.', RUBRIC['roles'], 'unknown', facets=self.FACETS)
            self.assertEqual(result['role'], 'procedure'); self.assertEqual(result['facets']['content_scope'], scope)
        with self.assertRaises(ValueError):
            pa.validate_annotation({**annotation('about_meta'), 'facets': {'content_scope': 'about_meta'}}, 'Run this.', RUBRIC['roles'], 'unknown', facets=self.FACETS)

    def test_invalid_facet_config_or_output_is_rejected(self):
        for spec in [{'bad-name': self.FACETS['content_scope']}, {'scope': {'values': ['x', 'x'], 'missing_value': 'x'}},
                     {'scope': {'values': ['x'], 'missing_value': 'unknown'}}, {'scope': {'values': 'x', 'missing_value': 'x'}}]:
            with self.subTest(spec=spec), self.assertRaises(ValueError): pa.annotation_schema(RUBRIC['roles'], spec)
        for facets in [{}, {'content_scope': 'invented'}, {'content_scope': 'product', 'extra': 'x'}, None]:
            with self.subTest(facets=facets), self.assertRaises(ValueError):
                pa.validate_annotation({**annotation(), 'facets': facets}, 'Run this.', RUBRIC['roles'], 'unknown', facets=self.FACETS)

    def test_empty_available_body_requires_declared_missing_facet_value(self):
        value = {**annotation('unknown', '', True), 'facets': {'content_scope': 'unknown'}}
        self.assertEqual(pa.validate_annotation(value, '', RUBRIC['roles'], 'unknown', facets=self.FACETS)['facets'], value['facets'])
        with self.assertRaises(ValueError):
            pa.validate_annotation({**value, 'facets': {'content_scope': 'product'}}, '', RUBRIC['roles'], 'unknown', facets=self.FACETS)

    def test_facet_schema_changes_cache_identity(self):
        args = ('Run this.', RUBRIC, 'prompt', SETTINGS, RUBRIC['roles'])
        self.assertNotEqual(pa.cache_identity(*args), pa.cache_identity(*args, facets=self.FACETS))
        changed = {'content_scope': {'values': ['product', 'unknown'], 'missing_value': 'unknown'}}
        self.assertNotEqual(pa.cache_identity(*args, facets=self.FACETS), pa.cache_identity(*args, facets=changed))

    def test_batch_dispatches_facet_schema_and_validates_provider_output(self):
        rubric = {**RUBRIC, 'facets': self.FACETS}
        manifest = {**batch_manifest({'a': 'Run this.'}), 'rubric_sha256': pa.digest(rubric),
                    'schema_sha256': pa.digest(pa.annotation_schema(rubric['roles'], self.FACETS)),
                    'evidence_policy': pa.MAPPED_EVIDENCE}
        seen = []
        async def fixture(prompt, schema, settings):
            seen.append(schema)
            return fixture_reply({**annotation(), 'facets': {'content_scope': 'about_meta'}})
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(pa.run_batch({'a': 'Run this.'}, rubric, 'prompt', manifest, approval(manifest), tmp, fixture))
            self.assertEqual(result['attempts'], 1); self.assertEqual(result['schema_version'], pa.VERSION_V2)
            self.assertEqual(seen, [pa.annotation_schema(rubric['roles'], self.FACETS)])
            self.assertEqual(result['annotations'][0]['annotation']['facets']['content_scope'], 'about_meta')


class EvaluationTests(unittest.TestCase):
    def fixture(self):
        ids = [f'p{i:02}' for i in range(48)]
        labels = {v: 'procedure' if i % 2 else 'overview' for i, v in enumerate(ids)}
        groups = {v: f'g{i//4}' for i, v in enumerate(ids)}
        texts = {v: ('Run execute steps task' if labels[v] == 'procedure' else 'Concept explains architecture system')+'\nunique '+v for v in ids}
        simple = {v: [float(i % 3), float('nan'), 1] for i, v in enumerate(ids)}
        kernel = {v: np.eye(48)[i] for i, v in enumerate(ids)}
        config = {'excluded_roles': ['mixed', 'unknown'], 'folds': 3, 'seed': 31,
            'minimum_pages': 10, 'minimum_groups': 3, 'max_features': 20000, 'min_df': 2,
            'C': 1., 'max_iter': 2000, 'draws': 20,
            'contrasts': [['text_simple_magnetic', 'text_simple_ordinary'], ['text_simple_magnetic', 'text_simple']]}
        return labels, groups, texts, simple, {'ordinary': kernel, 'magnetic': kernel}, config

    def test_hand_checked_confusion_and_kappa(self):
        result = pe.scores(['a', 'a', 'b', 'b'], ['a', 'b', 'a', 'b'], ['a', 'b'])
        self.assertEqual(result['confusion'], [[1, 1], [1, 1]])
        self.assertEqual(result['macro_f1'], .5); self.assertEqual(result['cohen_kappa'], 0.)

    def test_group_folds_isolate_all_groups_and_deterministic(self):
        labels, groups, *_ = self.fixture()
        result = pe.grouped_folds(labels, groups, folds=3, seed=31, minimum_pages=10, minimum_groups=3)
        self.assertEqual(result, pe.grouped_folds(labels, groups, folds=3, seed=31, minimum_pages=10, minimum_groups=3))
        for group in set(groups.values()):
            self.assertEqual(len({result['fold_by_id'][v] for v in labels if groups[v] == group}), 1)

    def test_absent_class_and_actual_fold_feasibility(self):
        args = list(self.fixture()); args[0] = {v: 'overview' for v in args[0]}
        self.assertEqual(pe.evaluate(*args)['status'], 'unsupported class support')
        with self.assertRaises(ValueError):
            pe.grouped_folds({'a': 'x', 'b': 'y'}, {'a': 'g', 'b': 'g'}, folds=3, seed=0, minimum_pages=1, minimum_groups=1)

    def test_training_only_vocabulary_and_scaling(self):
        a, b, meta = pe.tfidf(['alpha common', 'alpha other'], ['testonlynovel'], max_features=100, min_df=2)
        self.assertEqual(b.nnz, 0); self.assertGreater(meta['dimensions'], 0)
        train, test = pe.scale([[1, np.nan], [3, np.nan]], [[1000, 5]])
        np.testing.assert_array_equal(train[:, 0], [-1, 1]); self.assertEqual(test[0, 0], 998)

    def test_group_bootstrap_missing_class_draws_reported(self):
        result = pe.paired_bootstrap({'a': 'x', 'b': 'y'}, {'m': {'a': 'x', 'b': 'y'}, 'o': {'a': 'y', 'b': 'x'}},
            {'a': 'g', 'b': 'h'}, ['x', 'y'], [('m', 'o')], 100, 3)
        self.assertGreater(result['missing_class_draws_skipped'], 0)
        self.assertEqual(result['intervals']['m minus o'], [1., 1.])

    def test_ai_human_agreement_separate_unresolved(self):
        result = pe.agreement({'a': 'overview', 'b': None}, {'a': 'overview', 'b': 'procedure'},
            {'a': 'g', 'b': 'h'}, RUBRIC['roles'], 10, 1)
        self.assertEqual(result['paired'], 1); self.assertEqual(result['accuracy'], 1.)
        self.assertEqual(result['group_bootstrap']['undefined_kappa_draws'], 10)
        self.assertEqual(result['human_distribution']['unresolved'], 1)
        self.assertIsInstance(pa.canonical(result), bytes)

    def test_offline_grouped_classifier_end_to_end(self):
        result = pe.evaluate(*self.fixture())
        self.assertEqual(result['status'], 'evaluated')
        self.assertEqual(result['coverage']['eligible'], 48)
        self.assertEqual(result['macro_f1_differences']['text_simple_magnetic minus text_simple_ordinary'], 0)
        self.assertEqual(len(result['predictions']['text']), 48)
        self.assertTrue(all(r['success'] for r in result['fits']))

    def test_prepare_operator_end_to_end_and_stale_inventory(self):
        spec = importlib.util.spec_from_file_location('role_operator', Path(__file__).resolve().parents[1]/'containers/verification/page_roles.py')
        op = importlib.util.module_from_spec(spec); spec.loader.exec_module(op)
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp, 'input'); out = Path(tmp, 'output'); inp.mkdir(); out.mkdir(); (inp/'corpus').mkdir()
            def write(name, value): (inp/name).write_bytes(pa.canonical(value)+b'\n')
            (inp/'corpus/a.md').write_bytes(b'Run this.')
            write('pages.json', [{'doc_id': 'a', 'source_family': 'a', 'topic_stratum': 'topic', 'raw_sha256': pa.raw_hash('Run this.')}])
            write('rubric.json', RUBRIC); write('discovery.json', ['a'])
            write('config.json', {'schema_version': 'retrieval_arena.page_roles_config.v1', 'stage': 'prepare',
                'grouping': {'threshold': .8, 'shingle_words': 5, 'minimum_tokens': 30}, 'packet_salt': 'fixture',
                'max_seconds': 60, 'max_output_bytes': 1000000, 'proposed_count': 1, 'sample_seed': 1})
            inv = op.regular_inventory(inp)
            write('operator-request.json', {'schema_version': 'retrieval_arena.page_roles_request.v1', 'command': 'page-roles',
                'input_inventory': inv, 'input_inventory_sha256': pa.digest(inv)})
            self.assertEqual(op.run(inp, out)['remaining_population'], 0)
            self.assertTrue((out/'evaluation-form-template.json').exists())
            self.assertNotIn('a.md', (out/'discovery-review.md').read_text())
            other = Path(tmp, 'other'); other.mkdir(); (inp/'corpus/a.md').write_bytes(b'Changed.')
            with self.assertRaises(ValueError): op.run(inp, other)

    def test_sample_import_evaluate_operator_chain(self):
        from retrieval_arena.graph import page_representations as pr
        spec = importlib.util.spec_from_file_location('role_operator_chain', Path(__file__).resolve().parents[1]/'containers/verification/page_roles.py')
        op = importlib.util.module_from_spec(spec); spec.loader.exec_module(op)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); inp = base/'input'; inp.mkdir(); (inp/'corpus').mkdir()
            ids = ['a', 'b', 'c', 'd', 'e', 'f']
            texts = {v: ('Run action task' if i % 2 else 'Explain concept system')+' '+v*50 for i, v in enumerate(ids)}
            pages = [{'doc_id': v, 'source_family': v, 'topic_stratum': 'all', 'raw_sha256': pa.raw_hash(texts[v])} for v in ids]
            def write(name, value): (inp/name).write_bytes(pa.canonical(value)+b'\n')
            def request():
                inventory = [r for r in op.regular_inventory(inp) if r['path'] != 'operator-request.json']
                write('operator-request.json', {'schema_version': 'retrieval_arena.page_roles_request.v1', 'command': 'page-roles',
                    'input_inventory': inventory, 'input_inventory_sha256': pa.digest(inventory)})
            for v in ids: (inp/'corpus'/f'{v}.md').write_bytes(texts[v].encode())
            write('pages.json', pages); write('rubric.json', RUBRIC); write('discovery.json', [])
            config = {'schema_version': 'retrieval_arena.page_roles_config.v1', 'stage': 'sample',
                'grouping': {'threshold': .99, 'shingle_words': 5, 'minimum_tokens': 30}, 'packet_salt': 'fixture',
                'max_seconds': 60, 'max_output_bytes': 10000000, 'count': 6, 'sample_seed': 1}
            write('config.json', config)
            write('sampling-approval.json', {'decision': 'yes', 'author': 'fixture only', 'record': 'synthetic', 'count': 6,
                'rubric_sha256': pa.digest(RUBRIC), 'population_sha256': pa.digest(pages), 'group_policy_sha256': pa.digest(config['grouping'])})
            request(); out = base/'sample'; out.mkdir(); self.assertEqual(op.run(inp, out)['sampled'], 6)
            form = json.loads((out/'initial-human-form.json').read_bytes()); key = json.loads((out/'private-key.json').read_bytes())
            form['reviewer'] = 'synthetic fixture'
            for row in form['rows']:
                row['role'] = 'procedure' if ids.index(key['cards'][row['card_id']]['doc_id']) % 2 else 'overview'
            # New versioned import input, with the model stream absent.
            (inp/'sampling-approval.json').unlink()
            for name in ('blind-packet.json', 'private-key.json'):
                (inp/name).write_bytes((out/name).read_bytes())
            write('initial-human-form.json', form); write('config.json', {**config, 'stage': 'import'})
            request(); imported_out = base/'import'; imported_out.mkdir()
            self.assertEqual(op.run(inp, imported_out)['resolved'], 6)
            imported = json.loads((imported_out/'initial-human-labels.json').read_bytes())
            write('initial-human-labels.json', imported)
            reference = {'kind': 'initial_human', 'rubric_sha256': pa.digest(RUBRIC), 'provenance_record': 'synthetic fixture',
                'initial_human_sha256': pa.digest(imported), 'rows': imported['rows']}
            write('reference.json', reference); write('reference-approval.json', {'decision': 'yes', 'author': 'fixture only',
                'record': 'synthetic', 'reference_sha256': pa.digest(reference), 'rubric_sha256': pa.digest(RUBRIC)})
            write('groups.json', json.loads((out/'leakage-groups.json').read_bytes())['groups'])
            write('sample.json', json.loads((out/'sample.json').read_bytes()))
            write('simple.json', {'rows': [{'doc_id': v, 'value': 0.0} for v in ids]})
            fields = self.fixture()[-1]; fields = {**fields, 'minimum_pages': 1, 'minimum_groups': 3, 'draws': 10, 'min_df': 1}
            write('evaluation.json', fields)
            kernels = {}
            for name, q in [('ordinary', 0.), ('magnetic', .25)]:
                kernel, receipt = pr.heat_kernel(ids, [(ids[i], ids[i+1]) for i in range(5)], q=q)
                pr.save_bundle(inp/name, ids, kernel, receipt, pa.digest([(v, pa.raw_hash(texts[v])) for v in ids]))
                kernels[name] = {'path': name, 'graph_sha256': receipt['graph_sha256'], 'q': q, 't': 1.}
            write('config.json', {**config, 'stage': 'evaluate', 'simple_fields': ['value'], 'kernels': kernels})
            request(); evaluation = base/'evaluate'; evaluation.mkdir()
            self.assertEqual(op.run(inp, evaluation)['status'], 'evaluated')
            self.assertTrue((evaluation/'frozen-evaluation-plan.json').exists())



class ModelReferenceTests(unittest.TestCase):
    def batch(self, root, texts=None, invalid=False):
        texts = texts or {'a': 'Run this.'}
        manifest = batch_manifest(texts, retries=0)
        async def transport(*args):
            return fixture_reply(annotation(quote='missing' if invalid else 'Run this.'))
        result = asyncio.run(pa.run_batch(texts, RUBRIC, 'prompt', manifest, approval(manifest), root, transport))
        for name, value in [('approval.json', approval(manifest)), ('result.json', result)]:
            (root/name).write_bytes(pa.canonical(value))
        (root/'prompt.txt').write_text('prompt')
        return texts, manifest, result

    def test_model_reference_is_explicit_and_unresolved_stays_missing(self):
        from retrieval_arena.page_role_reference import load_model_reference
        for invalid in [False, True]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); texts, _, _=self.batch(root, invalid=invalid)
                labels, meta=load_model_reference(root, texts, RUBRIC, {'a':'g'}, [])
                self.assertEqual(labels['a'], None if invalid else 'procedure')
                self.assertEqual(meta['kind'], 'model_assigned')
                self.assertNotIn('human_labels', meta)

    def test_model_reference_rejects_tampering_and_discovery_leakage(self):
        from retrieval_arena.page_role_reference import load_model_reference
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); texts, _, result=self.batch(root)
            with self.assertRaisesRegex(ValueError, 'discovery-evaluation'):
                load_model_reference(root, texts, RUBRIC, {'a':'g'}, ['a'])
            with self.assertRaisesRegex(ValueError, 'model-reference-content'):
                load_model_reference(root, {'a':'changed'}, RUBRIC, {'a':'g'}, [])
            result['annotations'][0]['annotation']['role']='overview'
            (root/'result.json').write_bytes(pa.canonical(result))
            with self.assertRaisesRegex(ValueError, 'model-reference-result'):
                load_model_reference(root, texts, RUBRIC, {'a':'g'}, [])

    def test_model_reference_rejects_raw_reply_changes_and_interruption(self):
        from retrieval_arena.page_role_reference import load_model_reference
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); texts, _, result=self.batch(root)
            aid=result['annotations'][0]['attempt_id']
            raw=(root/(aid+'.json')).read_bytes()
            (root/(aid+'.json')).write_bytes(pa.canonical(fixture_reply(annotation(role='overview'))))
            with self.assertRaisesRegex(ValueError, 'model-reference-annotation'):
                load_model_reference(root, texts, RUBRIC, {'a':'g'}, [])
            (root/(aid+'.json')).write_bytes(raw)
            events=(root/'attempts.jsonl').read_bytes().splitlines()
            (root/'attempts.jsonl').write_bytes(events[0]+b'\n')
            with self.assertRaisesRegex(ValueError, 'model-reference-interrupted'):
                load_model_reference(root, texts, RUBRIC, {'a':'g'}, [])

    def test_model_operator_end_to_end_preserves_reference_kind(self):
        from retrieval_arena.graph import page_representations as pr
        spec=importlib.util.spec_from_file_location('role_operator', Path(__file__).resolve().parents[1]/'containers/verification/page_roles.py')
        op=importlib.util.module_from_spec(spec); spec.loader.exec_module(op)
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); inp=base/'input'; out=base/'output'
            (inp/'corpus').mkdir(parents=True); out.mkdir()
            ids=list('abcdef'); texts={v:'Run this. '+('concept system' if i%2 else 'action task')+' '+v*50 for i,v in enumerate(ids)}
            for v in ids: (inp/'corpus'/f'{v}.md').write_text(texts[v])
            def write(name, value): (inp/name).write_bytes(pa.canonical(value))
            manifest=batch_manifest(texts, retries=0); manifest['max_attempts']=6
            async def transport(prompt, *args):
                return fixture_reply(annotation(role='overview' if 'concept system' in prompt else 'procedure'))
            ref=inp/'model-reference'; result=asyncio.run(pa.run_batch(texts,RUBRIC,'prompt',manifest,approval(manifest),ref,transport))
            for name,value in [('approval.json',approval(manifest)),('result.json',result)]: (ref/name).write_bytes(pa.canonical(value))
            (ref/'prompt.txt').write_text('prompt')
            write('pages.json',[{'doc_id':v,'raw_sha256':pa.raw_hash(texts[v])} for v in ids])
            write('rubric.json',RUBRIC); write('discovery.json',[]); write('groups.json',{v:v for v in ids})
            write('simple.json',{'rows':[{'doc_id':v,'value':0.} for v in ids]})
            config={**EvaluationTests().fixture()[-1],'minimum_pages':1,'minimum_groups':3,'draws':10,'min_df':1}
            write('evaluation.json',config)
            kernels={}
            for name,q in [('ordinary',0.),('magnetic',.25)]:
                kernel,receipt=pr.heat_kernel(ids,[(ids[i],ids[i+1]) for i in range(5)],q=q)
                pr.save_bundle(inp/name,ids,kernel,receipt,pa.digest([(v,pa.raw_hash(texts[v])) for v in ids]))
                kernels[name]={'path':name,'graph_sha256':receipt['graph_sha256'],'q':q,'t':1.}
            write('config.json',{'schema_version':'retrieval_arena.page_roles_config.v1','stage':'evaluate-model',
                'simple_fields':['value'],'kernels':kernels,'max_seconds':60,'max_output_bytes':10000000})
            inventory=op.regular_inventory(inp)
            write('operator-request.json',{'schema_version':'retrieval_arena.page_roles_request.v1','command':'page-roles',
                'input_inventory':inventory,'input_inventory_sha256':pa.digest(inventory)})
            self.assertEqual(op.run(inp,out)['status'],'evaluated')
            measured=json.loads((out/'evaluation-results.json').read_bytes())
            self.assertEqual(measured['reference']['kind'],'model_assigned')
            self.assertEqual(len(measured['predictions']['text']),6)

if __name__ == '__main__':
    unittest.main()
