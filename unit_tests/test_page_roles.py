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


if __name__ == '__main__':
    unittest.main()
