import copy
import json
from pathlib import Path
import tempfile
import unittest

from retrieval_arena import embedded_links as e

class EmbeddedLinks(unittest.TestCase):
    def setUp(self):
        self.raw=b'First use the engine. Then read the manual.\n\n[existing](c.md)\n'
        self.target=b'# Manual\n\nThe manual explains engines.\n'
        self.pages={p:{'doc_id':p[0],'path':p,'raw_sha256':e.rawhash(raw)} for p,raw in
                    [('a.md',self.raw),('b.md',self.target),('c.md',b'C') ]}
        self.region={'region_id':'region:test','nodes':['a','b','c'],
                     'component_membership':{'a':'c','b':'c','c':'c'}}
        self.limits={'min_words':1,'max_words':12,'max_links_per_sentence':1}
        self.p={'schema_version':e.VERSION+'.proposal','proposal_id':'proposal:test',
            'run_id':'run:test','arm_id':'fixture','region_id':'region:test',
            'corpus_id':'fixture','snapshot_id':'snapshot','source_id':'a','target_id':'b',
            'source_sha256':e.rawhash(self.raw),'target_sha256':e.rawhash(self.target),
            'href':'b.md','target_fragment':'','coordinate_convention':e.COORDINATES,
            'source_span':[14,20],'anchor':'engine','sentence':'First use the engine.',
            'target_evidence':[{'span':[0,8],'quote':'# Manual'}],
            'relation':'source_requires_target','stated_intent':'Read manual first.',
            'expected_reading_use':'Prerequisite context.','basis':'supported_inference',
            'confidence':None,'confidence_status':'uncalibrated','provenance':{'provider':'offline','model':'mock',
                'prompt_version':'fixture','schema_version':'v1','attempt_id':'1','timestamp':'fixture',
                'response_sha256':e.digest({}),'source_access':['a','b'],'retry_of':None,'status':'mock-model'}}

    def edit(self,raw=None,**changes):
        p=copy.deepcopy(self.p); p.update(changes)
        return e.apply_edit(self.raw if raw is None else raw,self.target,p,self.pages,[],self.region,self.limits)

    def test_occurrence_reference_and_inline(self):
        raw=b'[one](b.md) and [two][ref].\n\n[ref]: c.md\n[unused]: b.md\n'
        rows=e.occurrences(raw,self.pages['a.md'],self.pages)
        self.assertEqual([r['target_id'] for r in rows],['b','c'])
        self.assertEqual(rows[1]['syntax'],'markdown_reference')
        for r in rows: self.assertEqual(raw[slice(*r['span'])].decode(),r['source_syntax'])

    def test_mixed_navigation_content_and_release_notes(self):
        raw=b'# Index\n\n- [manual](b.md)\n\nThe [manual](b.md) explains engines.\n'
        rows=e.occurrences(raw,self.pages['a.md'],self.pages)
        self.assertEqual([r['class'] for r in rows],['navigation','substantive'])
        self.assertEqual(e.project(rows,{'a','b','c'}),[('a','b')])

    def test_ambiguity_sensitivity(self):
        rows=e.occurrences(b'- Related [manual](b.md)\n',self.pages['a.md'],self.pages)
        self.assertEqual(rows[0]['class'],'ambiguous')
        self.assertEqual(e.project(rows,{'a','b'}),[])
        self.assertEqual(e.project(rows,{'a','b'},'sensitivity'),[('a','b')])

    def test_html_and_code_are_not_silently_prose(self):
        raw=b'```\n[x](b.md)\n```\n\n<a href="b.md">Manual</a>\n'
        rows=e.occurrences(raw,self.pages['a.md'],self.pages)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['class'],'ambiguous')

    def test_edit_and_graph_delta_and_fixed_ranking(self):
        edited,facts=self.edit()
        self.assertIn(b'[engine](b.md)',edited)
        self.assertEqual(facts['added_edges'],[['a','b']])
        self.assertEqual(facts['rendered_before_sha256'],facts['rendered_after_sha256'])
        self.assertEqual(e.semantic_text(self.raw),e.semantic_text(edited))
        nav=e.navigation_view(edited,self.pages['a.md'],self.pages)
        self.assertEqual(nav[0]['anchor'],'engine')
        self.assertEqual(nav[0]['destination'],'b')
        self.assertIn('First use',nav[0]['sentence_context'])
        self.assertEqual(e.replay_patch(self.raw,facts),edited)
        self.assertEqual(e.replay_patch(edited,facts),edited)

    def test_repeated_anchor_uses_exact_span(self):
        raw=b'Engine then engine.\n'
        new,_=self.edit(raw,source_sha256=e.rawhash(raw),source_span=[12,18],sentence='Engine then engine.')
        self.assertEqual(new,b'Engine then [engine](b.md).\n')

    def test_unicode_and_crlf_coordinates(self):
        raw='CafÃ© engine works.\r\n'.encode(); a=raw.index(b'engine')
        new,_=self.edit(raw,source_sha256=e.rawhash(raw),source_span=[a,a+6],sentence='CafÃ© engine works.')
        self.assertTrue(new.endswith(b'\r\n'))

    def test_stale_source_and_target(self):
        for changes in [{'source_sha256':'0'*64},{'target_sha256':'0'*64}]:
            with self.assertRaisesRegex(ValueError,'stale'): self.edit(**changes)

    def test_unsafe_and_unresolved_targets(self):
        for href in ['https://x.test','javascript:x','../../b.md','missing.md','b.md?q=1','b.md#missing']:
            with self.assertRaises(ValueError): self.edit(href=href)

    def test_fragment_is_verified_but_no_page_pair_duplicate(self):
        self.edit(href='b.md#manual',target_fragment='manual')
        with self.assertRaisesRegex(ValueError,'existing-directed'):
            e.apply_edit(self.raw,self.target,self.p,self.pages,[('a','b')],self.region,self.limits)
        with self.assertRaisesRegex(ValueError,'reciprocal'):
            e.apply_edit(self.raw,self.target,self.p,self.pages,[('b','a')],self.region,self.limits)

    def test_heading_list_code_escape_and_nested_rejected(self):
        for raw in [b'# engine\n',b'- engine text.\n',b'`engine` works.\n',b'An [engine](c.md) works.\n',
                    b'An \\engine works.\n',b'{{< engine >}} works.\n']:
            at=raw.index(b'engine')
            with self.assertRaises(ValueError):
                self.edit(raw,source_sha256=e.rawhash(raw),source_span=[at,at+6],sentence=raw.decode().strip())

    def test_sentence_limit_and_overlap(self):
        raw=b'The engine uses a [manual](c.md).\n'; at=raw.index(b'engine')
        with self.assertRaisesRegex(ValueError,'sentence-link-budget'):
            self.edit(raw,source_sha256=e.rawhash(raw),source_span=[at,at+6],sentence=raw.decode().strip())
        with self.assertRaisesRegex(ValueError,'overlap'):
            e.apply_edit(self.raw,self.target,self.p,self.pages,[],self.region,self.limits,[(15,21)])
        with self.assertRaisesRegex(ValueError,'anchor-crosses-sentence'):
            self.edit(source_span=[14,31],anchor='engine. Then read',sentence='First use the engine. Then read the manual.')

    def test_region_and_component_rejection(self):
        for region in [{'region_id':'x','nodes':['a','b']},
                       {'region_id':'region:test','nodes':['a','b'],'component_membership':{'a':'x','b':'y'}}]:
            with self.assertRaises(ValueError): e.apply_edit(self.raw,self.target,self.p,self.pages,[],region,self.limits)

    def test_schema_closed_and_relation_uncertainty(self):
        e.validate_schema(self.p,e.PROPOSAL_SCHEMA)
        for change in [{'relation':'forward'},{'success':True},{'confidence':float('nan')}]:
            p=self.p|change
            with self.assertRaises(ValueError): e.validate_schema(p,e.PROPOSAL_SCHEMA)

    def test_ledger_tamper_duplicates_transitions_and_authority(self):
        log=[e.event([],self.p,'proposed',self.p,'model')]
        log.append(e.event(log,self.p,'schema_validated',{'proposal_sha256':e.digest(self.p)}))
        tip=e.digest(log[-1]); self.assertEqual(e.replay(log,tip)['unique_events'],2)
        self.assertEqual(e.replay(log+[log[-1]],tip)['unique_events'],2)
        bad=copy.deepcopy(log); bad[0]['payload']['stated_intent']='tampered'
        with self.assertRaises(ValueError): e.replay(bad)
        with self.assertRaisesRegex(ValueError,'transition'):
            e.event(log,self.p,'eligible',{'structural':True,'semantic':True})
        with self.assertRaises(ValueError): e.event(log,self.p,'structurally_validated',
            {'eligible':True,'reason':'x','validation_sha256':e.digest({})},'model')
        with self.assertRaises(ValueError): e.replay(log[:1],tip)

    def test_blinding_allowlist(self):
        view=e.blind_review_input(self.p,self.raw,self.target)
        self.assertFalse({'arm_id','stated_intent','relation','scores','verdict'} & view.keys())

    def test_existing_search_indexes_destinations_and_page_exposes_markup(self):
        import runpy
        runner=runpy.run_path(str(Path(__file__).resolve().parents[1]/'tests/rag_repo_search_agent/runner.py'))
        edited,_=self.edit()
        self.assertEqual(runner['search_corpus']({'a':self.raw.decode()},'b.md',hit_files=2,lines_per_file=2),[])
        hits=runner['search_corpus']({'a':edited.decode()},'b.md',hit_files=2,lines_per_file=2)
        self.assertEqual(hits[0]['doc_id'],'a')
        view=runner['page_view']('a',edited.decode(),limit=1000)
        self.assertIn('First use the [engine](b.md).',view['text'])

    def test_fragment_ambiguity_and_reference_anchor_escaping(self):
        rows=e.occurrences(b'The [manual](b.md#missing) explains engines.\n',self.pages['a.md'],self.pages)
        e.qualify_fragments(rows,{'b':self.target})
        self.assertEqual(rows[0]['class'],'ambiguous')
        self.assertFalse(e.project(rows,{'a','b'}))
        rows=e.occurrences(b'The [engine \\[x\\]][ref] works.\n\n[ref]: b.md\n',self.pages['a.md'],self.pages)
        self.assertEqual(rows[0]['syntax'],'markdown_reference')
        self.assertEqual(rows[0]['target_id'],'b')

    def test_append_api_and_supersession(self):
        first=e.event([],self.p,'proposed',self.p,'model')
        p=copy.deepcopy(self.p); p['stated_intent']='Corrected declaration.'
        second=e.event([first],p,'proposed',p,'model',first['event_id'])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'ledger.jsonl'
            tip=e.append_event(path,first)['tip_sha256']
            e.append_event(path,second,tip)
            size=path.stat().st_size
            e.append_event(path,second,e.digest(second))
            self.assertEqual(path.stat().st_size,size)
            self.assertEqual(e.replay([first,second])['unique_events'],2)

    def test_emitted_schemas_match_source(self):
        root=Path(__file__).resolve().parents[1]/'schemas'
        for name,schema in [('proposal',e.PROPOSAL_SCHEMA),('event',e.EVENT_SCHEMA)]:
            saved=json.loads((root/f'embedded-link-{name}.v1.json').read_bytes())
            self.assertEqual({k:v for k,v in saved.items() if k!='$schema'},schema)

    def test_portable_inventory_has_case_sensitive_posix_path_order(self):
        from retrieval_arena.embedded_link_audit import portable_inventory
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ['z.md','A.md','b.md']:
                (root/name).write_bytes(b'fixture')
            self.assertEqual([r['path'] for r in portable_inventory(root)],['A.md','b.md','z.md'])

    def test_semantic_text_preserves_line_boundaries_and_exact_response_bytes(self):
        self.assertEqual(e.semantic_text(b'First engine\nthen manual.\n'),'First engine\nthen manual.')
        raw=b'{"fixture":true}'
        self.assertTrue(e.verify_response_bytes(raw,e.rawhash(raw)))
        with self.assertRaisesRegex(ValueError,'raw-response-hash'):
            e.verify_response_bytes(raw+b'\n',e.rawhash(raw))

    def test_patch_tamper_and_stale_replay(self):
        _,facts=self.edit(); bad=copy.deepcopy(facts); bad['patch']['replacement']='changed'
        with self.assertRaises(ValueError): e.replay_patch(self.raw,bad)
        with self.assertRaises(ValueError): e.replay_patch(self.raw+b'x',facts)

    def test_regions_duplicates_grouped_no_outcome_or_halo(self):
        rows={'topic/a.md':{'doc_id':'a'},'other/b.md':{'doc_id':'b'},'other/c.md':{'doc_id':'c'}}
        contents={'a':b'Same.','b':b'Same.','c':b'Other.'}
        cfg={'source_family_depth':1,'region_seed':3401,'max_cores':8}
        r=e.freeze_regions(rows,contents,[('a','c'),('b','c')],cfg)
        self.assertEqual(len(r['regions']),1)
        self.assertEqual(r['all_source_nodes_accounted'],3)
        self.assertFalse(r['evaluation_outcomes_inspected'])
        self.assertEqual(r['shared_halo_nodes'],0)

    def test_identity_namespaces(self):
        self.assertNotEqual(e.stable('edge','a','b'),e.stable('occurrence','a','b'))

if __name__=='__main__': unittest.main()
