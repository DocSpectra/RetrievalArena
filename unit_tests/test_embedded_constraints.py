"""V2 behavioral properties and exact assignments; fakes never semantic labels."""
import asyncio
import copy
from itertools import permutations
from pathlib import Path
import tempfile
import unittest

from retrieval_arena import embedded_constraints as v
from retrieval_arena import embedded_packet_context as pc
from retrieval_arena import embedded_competitive as c
from retrieval_arena import embedded_interface as interface
from retrieval_arena import embedded_ledger_v2 as ledger
from retrieval_arena import embedded_links as e
from retrieval_arena import embedded_refinement as f
from retrieval_arena import embedded_study as study
from retrieval_arena.embedded_link_selection import feasible_sets
from retrieval_arena.embedded_selection_v2 import select_embedded_v2
from retrieval_arena.embedded_link_audit import read, write
from retrieval_arena.embedded_interface_fixture import MockTransport, mock_count
from unit_tests.test_embedded_competitive import bundle


def selector_pool():
    return [dict(proposal_id=pid, source_id=s, target_id=t, semantic_score=6,
                 semantic_eligible=True, conflicts=[])
            for pid,s,t in [('a','0','2'),('b','2','0'),('c','0','5'),('d','3','5')]]


def corrected_bundle(root):
    """Real combined runner with reciprocal alternatives and bounded evidence."""
    inp, manifest = bundle(root)
    for lane in ('preflight','real'):
        dest=inp/lane; policy=read(dest/'study-manifest.json')
        policy['constraint_version']=v.VERSION
        for role in policy['roles']:
            role['initial_items_max']=2 if role['role']=='generation' else 3
            role['attempts_max']=2*role['initial_items_max']
        write(dest/'study-manifest.json',policy)
        rows=read(dest/'pages.json'); pages={p['path']:p for p in rows}
        raw={p['doc_id']:(dest/'corpus'/p['path']).read_bytes() for p in rows}
        raw['2']+=b'\nReview the release checklist.\n'
        (dest/'corpus/2.md').write_bytes(raw['2'])
        for p in rows:p['raw_sha256']=e.rawhash(raw[p['doc_id']])
        write(dest/'pages.json',rows)
        occurrences=[r for p in rows for r in e.occurrences(raw[p['doc_id']],p,pages)]
        write(dest/'frozen-occurrences.json',occurrences)
        write(dest/'original.json',{'nodes':sorted(raw),'edges':e.project(occurrences,set(raw),'original')})
        old=read(dest/'packets.json')[0]
        # Exact, short source/target material and readonly raw context.
        for t in old['targets']:
            t['source_sha256']=e.rawhash(raw[t['target_id']])
        p2=next(p for p in pc.paragraph_rows(raw['2'],editable=True,maximum=3000)[0]
                if p['text']=='Review the release checklist.')
        t0=next(p for p in pc.paragraph_rows(raw['0'],editable=False,maximum=2000)[0]
                if p['text']=='Review validation rules.')
        reverse={'source_id':'2','source_path':'2.md','source_sha256':e.rawhash(raw['2']),
            'source_paragraphs':[p2], 'targets':[{'target_id':'0','path':'0.md','title':'Authored overview',
                'source_sha256':e.rawhash(raw['0']),'paragraphs':[t0]}], 'region_id':old['region_id']}
        packets=[]
        for packet in (old,reverse):
            packet.pop('packet_sha256',None)
            packet.update(context_version=pc.VERSION,
                source_context=[pc.raw_row(raw[packet['source_id']],*packet['source_paragraphs'][0]['raw_span'])])
            packet['packet_sha256']=e.digest(packet)
            projected,_=pc.project(packet,raw);packets.append(projected)
        write(dest/'packets.json',packets)
        projection=dest/'corrected-projection';projection.mkdir();f.prepare_interface(dest,projection,{})
        write(dest/'packets.json',read(projection/'packets.json'))
        write(dest/'token-input/jobs.json',read(projection/'jobs.json'))
        policy['allowed_initial_token_request_hashes']=[j['job_id'] for j in read(projection/'jobs.json')]
        policy['allowed_source_ids']=['0','2'];write(dest/'study-manifest.json',policy)
    manifest['constraint_version']=v.VERSION
    manifest['count_attempts']={'preflight':4,'real':4}
    write(inp/'study-manifest.json',manifest);study.seal(inp)
    return inp,manifest


class ConstraintTests(unittest.TestCase):
    def test_original_canary_shortage_never_selects_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp,manifest=corrected_bundle(root)
            policy=read(inp/'real/study-manifest.json')
            policy.update(canary_source_ids=['missing-original','0'],allowed_source_ids=['missing-original','0','2'])
            write(inp/'real/study-manifest.json',policy);study.seal(inp)
            out=root/'output'
            asyncio.run(c.CompetitiveStudy(inp,out,manifest,
                factory=lambda settings,network,path:MockTransport(settings,path),counter=mock_count).run())
            canary=read(out/'canary.json')
            self.assertEqual(canary['original_source_ids'],['missing-original','0'])
            self.assertEqual(canary['unavailable_source_ids'],['missing-original'])
            self.assertEqual({j['source_id'] for j in read(out/'real-0-generation-input/jobs.json')},{'0'})
            self.assertEqual({j['source_id'] for j in read(out/'real-1-generation-input/jobs.json')},{'2'})
            self.assertEqual(canary['replacement_packets'],0)

    def test_invalid_explicit_canary_fails_before_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp,manifest=corrected_bundle(root)
            policy=read(inp/'real/study-manifest.json')
            for ids in ([],['0','0'],['0','unknown'],'02'):
                with self.subTest(ids=ids):
                    with self.assertRaisesRegex(ValueError,'frozen-canary'):
                        c.canary_sources(inp/'real',dict(policy,canary_source_ids=ids))

    def test_reciprocal_singletons_and_k_greater_than_one(self):
        pool=selector_pool()
        sets=feasible_sets(pool,4,constraint_version=v.VERSION)
        self.assertIn(frozenset(['a']),sets);self.assertIn(frozenset(['b']),sets)
        self.assertNotIn(frozenset(['a','b']),sets)
        self.assertEqual(max(map(len,sets)),3) # b+c+d uses three different sources.
        with self.assertRaisesRegex(ValueError,'pool-reciprocal'):feasible_sets(pool,1)

    def test_adding_unselected_reverse_preserves_individual_validity(self):
        pool=selector_pool()
        self.assertEqual(v.validate_selected(pool[:1],['a'],[])['added_pairs'],
                         v.validate_selected(pool[:2],['a'],[])['added_pairs'])

    def test_joint_reciprocal_rejected_without_conflict_metadata(self):
        with self.assertRaisesRegex(ValueError,'reciprocal'):
            v.validate_selected(selector_pool(),['a','b'],[])

    def test_baseline_reverse_and_forward_still_fail(self):
        for edge in [('2','0'),('0','2')]:
            with self.assertRaisesRegex(ValueError,'original'):
                v.validate_selected(selector_pool(),['a'],[edge])

    def test_source_cap_duplicates_and_order_invariance(self):
        pool=selector_pool()
        sets=feasible_sets(pool,4,constraint_version=v.VERSION)
        for ordered in permutations(pool):
            self.assertEqual(set(sets),set(feasible_sets(list(ordered),4,constraint_version=v.VERSION)))
        with self.assertRaisesRegex(ValueError,'same-source'):
            v.validate_selected(pool,['a','c'],[])
        duplicate=dict(pool[0],proposal_id='e')
        with self.assertRaisesRegex(ValueError,'duplicate-page-pair'):
            v.validate_selected(pool+[duplicate],['a','e'],[])

    def test_all_arms_preserve_exact_completion(self):
        graph={'nodes':list('012345'),'edges':[['0','1'],['1','2'],['2','3'],['3','4'],['4','5']]}
        for arm in ('S','Z','M'):
            seen=[]
            def score(prefix):
                v.validate_selected(selector_pool(),list(prefix),graph['edges']);seen.append(prefix)
                return {'status':'finite_estimate','schedule_costs':[0.,0.]}
            result=select_embedded_v2(selector_pool(),graph,{'maximum':3,'constraint_version':v.VERSION},arm=arm,scorer=score)
            self.assertEqual(result['achieved_budget'],3)
            v.validate_selected(selector_pool(),result['selected'],graph['edges'])

    def test_unknown_version_fails_closed(self):
        with self.assertRaisesRegex(ValueError,'unknown'):v.admit_version('future')

    def test_full_fake_reciprocal_pipeline_patches_and_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp,manifest=corrected_bundle(root)
            out=root/'output'
            def factory(settings,network,path):return MockTransport(settings,path)
            asyncio.run(c.CompetitiveStudy(inp,out,manifest,factory=factory,counter=mock_count).run())
            records=read(out/'real-selection-output/records.json')
            self.assertEqual(sum(r.get('semantic_eligible',False) for r in records),3)
            proposals=[r['proposal'] for r in records]
            pages,_,raw,regions=f.load_sources(inp/'real')
            baseline=read(inp/'real/original.json')['edges']
            for p in proposals:
                # Both orientations independently survive the full-pool editor.
                single,facts=ledger.apply_edit(raw[p['source_id']],raw[p['target_id']],p,pages,
                    baseline,regions[0],f.LIMITS,[p],constraint_version=v.VERSION)
                full,full_facts=ledger.apply_edit(raw[p['source_id']],raw[p['target_id']],p,pages,
                    baseline,regions[0],f.LIMITS,proposals,constraint_version=v.VERSION)
                self.assertEqual((single,facts),(full,full_facts))
            duplicate=copy.deepcopy(records[0]);duplicate['proposal']['proposal_id']='duplicate-occurrence'
            frozen=c.freeze(records+[duplicate],regions,constraint_version=v.VERSION)
            self.assertEqual(len(frozen['records']),4);self.assertEqual(len(frozen['groups'][0]['pool']),3)
            self.assertTrue(any(x['reason']=='proposed-reciprocal-partner' for x in read(out/'real-pool-output/common-pool.json')['conflicts']))
            for arm in ('S','Z','M'):
                dest=out/'real-selection-output'/arm
                self.assertEqual(len(read(dest/'selected-set-proof.json')['added_pairs']),1)
                log=[__import__('json').loads(line) for line in (dest/'intent-events.jsonl').read_text().splitlines()]
                ledger.replay(log,read(dest/'trusted-tip.json')['tip_sha256'])
            # Replay the same pool through exact materialization without new calls.
            replay=root/'replay';replay.mkdir()
            c.select_and_apply(out/'real-selection-input',replay,{},diagnostic=False)
            self.assertEqual([a['added_pairs'] for a in read(replay/'applications.json')],
                             [a['added_pairs'] for a in read(out/'real-selection-output/applications.json')])
            for arm in ('S','Z','M'):
                for p in (replay/arm/'corpus').glob('*.md'):
                    self.assertEqual(p.read_bytes(),(out/'real-selection-output'/arm/'corpus'/p.name).read_bytes())

    def test_existing_sentence_link_is_retained_but_nested_anchor_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp,manifest=corrected_bundle(root)
            # Obtain real typed proposals with explicitly fake provenance.
            out=root/'output'
            asyncio.run(c.CompetitiveStudy(inp,out,manifest,
                factory=lambda settings,network,path:MockTransport(settings,path),counter=mock_count).run())
            p=copy.deepcopy(read(out/'real-selection-output/records.json')[0]['proposal'])
            pages,_,raw,regions=f.load_sources(inp/'real');baseline=read(inp/'real/original.json')['edges']
            original=b'# Overview\n\nRead [the introduction](1.md), then review validation rules.\n'
            anchor=b'validation rules';a=original.index(anchor)
            p.update(source_sha256=e.rawhash(original),source_span=[a,a+len(anchor)],anchor=anchor.decode(),
                     sentence='Read [the introduction](1.md), then review validation rules.')
            with self.assertRaisesRegex(ValueError,'sentence-link-budget'):
                ledger.apply_edit(original,raw[p['target_id']],p,pages,baseline,regions[0],f.LIMITS)
            edited,facts=ledger.apply_edit(original,raw[p['target_id']],p,pages,baseline,regions[0],f.LIMITS,constraint_version=v.VERSION)
            self.assertIn(b'[the introduction](1.md)',edited)
            self.assertEqual(e.semantic_text(original),e.semantic_text(edited))
            a=original.index(b'the introduction');p.update(source_span=[a,a+len(b'the introduction')],anchor='the introduction')
            with self.assertRaisesRegex(ValueError,'nested-or-overlapping'):
                ledger.apply_edit(original,raw[p['target_id']],p,pages,baseline,regions[0],f.LIMITS,constraint_version=v.VERSION)


class PacketContextTests(unittest.TestCase):
    def test_operator_projection_preserves_page_identity_and_records_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp,manifest=corrected_bundle(root)
            out=root/'projection';out.mkdir();summary=pc.prepare(inp/'real',out,{})
            old=read(inp/'real/packets.json');new=read(out/'packets.json')
            self.assertEqual([p['source_id'] for p in old],[p['source_id'] for p in new])
            self.assertEqual([[t['target_id'] for t in p['targets']] for p in old],
                             [[t['target_id'] for t in p['targets']] for p in new])
            self.assertEqual(summary['provider_calls'],0)
            self.assertTrue(all(r['serialized_request_bytes']>0 and r['provider_input_tokens'] is None
                                for r in read(out/'request-inventory.json')))

    def test_short_meaningful_prose_and_fragments(self):
        for text in ('Run tests.','Stop now!','Use Docker.'):self.assertTrue(pc.substantive(text))
        for text in ('0.3.0','2021-05-03','Docker Engine','This command will:','You must also have:'):
            self.assertFalse(pc.substantive(text))

    def test_no_suitable_source(self):
        raw=b'# API\n\n0.3.0\n';p={'source_id':'a','source_sha256':e.rawhash(raw),'targets':[]}
        p['packet_sha256']=e.digest(p)
        child,audit=pc.project(p,{'a':raw})
        self.assertIsNone(child);self.assertEqual(audit['status'],'no-suitable-source')

    def test_context_keeps_complete_lists_code_and_no_edit_ids(self):
        raw=b'# Guide\n\nRun tests.\n\nThis command will:\n\n- Test an image.\n- Save results.\n\n```sh\ntest image\n```\n'
        rows,_=pc.paragraph_rows(raw,editable=True,maximum=3000)
        self.assertEqual([r['text'] for r in rows],['Run tests.'])
        contexts,omissions=pc.contexts(raw,rows)
        self.assertFalse(omissions)
        self.assertIn('- Save results.',contexts[0]['text']);self.assertIn('```sh',contexts[0]['text'])
        p={'source_id':'a','source_path':'a.md','source_sha256':e.rawhash(raw),'source_paragraphs':rows,'source_context':contexts,
            'targets':[],'context_version':pc.VERSION}
        data=interface.disclosure(p,{'a':raw});public=interface.public_disclosure(data)
        self.assertNotIn('sentences',public['source_context'][0]);self.assertFalse(public['source_context'][0]['editable'])
        self.assertNotIn('raw_byte_map',public['source_context'][0])

    def test_oversize_code_is_atomic_and_logged(self):
        raw=b'# Guide\n\nRun tests.\n\n```txt\n'+b'x'*5000+b'\n```\n'
        rows,_=pc.paragraph_rows(raw,editable=True,maximum=3000)
        contexts,omissions=pc.contexts(raw,rows)
        self.assertNotIn('```',contexts[0]['text']);self.assertTrue(omissions)

    def test_readonly_source_cannot_be_an_anchor_but_can_be_review_evidence(self):
        raw=b'# Guide\n\nRun tests.\n\n- Inspect results.\n'
        rows,_=pc.paragraph_rows(raw,editable=True,maximum=3000);context,_=pc.contexts(raw,rows)
        packet={'source_id':'a','source_path':'a.md','source_sha256':e.rawhash(raw),'source_paragraphs':rows,
                'source_context':context,'context_version':pc.VERSION,'targets':[]}
        data=interface.disclosure(packet,{'a':raw})
        item={'source_id':'a','target_id':'b','source_sentence_id':data['source_context'][0]['passage_id'],
              'anchor':'Inspect results','target_evidence':[]}
        # Add a legitimate target; a readonly passage ID is still not a sentence.
        data['targets']=[{'target_id':'b','paragraphs':[]}]
        with self.assertRaisesRegex(ValueError,'sentence-id'):interface.resolve_proposal(item,data,{'a':raw})
        p=data['source_context'][0]
        evidence={'source':[p],'target':[dict(p,role='target')]}
        payload={'source_evidence':[{'passage_id':p['passage_id'],'quote':'- Inspect results.'}],
                 'target_evidence':[{'passage_id':p['passage_id'],'quote':'Run tests.'}]}
        interface.resolve_review(payload,evidence)

    def test_wrong_context_role_and_invented_quote_fail(self):
        raw=b'Run tests.\n';p=interface.passage(pc.raw_row(raw,0,len(raw)),'a',raw,'source')
        payload={'source_evidence':[{'passage_id':p['passage_id'],'quote':'Invented quote.'}],'target_evidence':[]}
        with self.assertRaisesRegex(ValueError,'quote-absent'):interface.resolve_review(payload,{'source':[p],'target':[]})


if __name__=='__main__':unittest.main()
