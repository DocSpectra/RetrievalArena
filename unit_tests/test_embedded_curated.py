import copy
from pathlib import Path
import tempfile
import unittest

from retrieval_arena import embedded_curated as c
from retrieval_arena import embedded_links as e
from retrieval_arena import embedded_ledger_v2 as ledger
from retrieval_arena.embedded_link_audit import read, write
from unit_tests import test_embedded_links as old_tests


class Curated(unittest.TestCase):
    def fixture(self, root):
        root.mkdir();(root/'corpus').mkdir()
        raw=[b'Read validation rules or release checklist.\n\nContinue to [one](1.md).\n',
             b'Continue to [two](2.md).\n',b'Validation rules explain testing.\n\nContinue to [three](3.md).\n',
             b'Continue to [four](4.md).\n',b'Continue to [five](5.md).\n',b'Release checklist explains release.\n\n- Check release.\n\n```\nrelease --check\n```\n']
        pages=[{'path':f'{i}.md','doc_id':str(i),'raw_sha256':e.rawhash(b)} for i,b in enumerate(raw)]
        for p,b in zip(pages,raw):(root/'corpus'/p['path']).write_bytes(b)
        region={'region_id':'fixture','nodes':list('012345'),'component_membership':{n:'component' for n in '012345'},
                'assignment':'development','halo_nodes':[],'corpus_id':'fixture','snapshot_id':'fixture'}
        occurrences=[r for p,b in zip(pages,raw) for r in e.occurrences(b,p,{p['path']:p for p in pages})]
        write(root/'pages.json',pages);write(root/'regions.json',[region]);write(root/'frozen-occurrences.json',occurrences)
        write(root/'original.json',{'edges':e.project(occurrences,set('012345'),'original')})
        pairs=[{'audit_id':key,'source':pages[0],'target':pages[int(t)],'region_id':'fixture','prior_exposure':'authored fixture'}
               for key,t in [('a','2'),('b','5')]]
        drafts=[{'audit_id':key,'anchor':anchor,'target_quote':quote,'relation':'target_elaborates_source','scores':[2,2,2],
                 'checklist':['Complete procedure'],'counterargument':'Authored plumbing only','reason':'Authored assertion'}
                for key,anchor,quote in [('a','validation rules','Validation rules explain testing.'),('b','release checklist','Release checklist explains release.')]]
        write(root/'audit-inventory.json',{'pairs':pairs});write(root/'curation-decisions.json',drafts)
        return pairs,drafts

    def prepare(self, root):
        inp=root/'input';self.fixture(inp);out=root/'prepare';out.mkdir()
        c.prepare(inp,out,{'timestamp':'fixture','run_id':'fixture'})
        frozen=read(out/'curated-freeze.json');write(inp/'curated-freeze.json',frozen)
        write(inp/'freeze-receipt.json',{'curated_freeze_sha256':e.rawhash((inp/'curated-freeze.json').read_bytes()),'context_commit':'1'*40})
        return inp,frozen

    def test_full_evidence_binding_and_blinding(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp,frozen=self.prepare(Path(tmp));packet=next(p for p in frozen['packets'].values() if p['target_id']=='5')
            self.assertIn('release --check',packet['pages'][1]['text'])
            for page in packet['pages']:
                self.assertEqual(page['sha256'],e.rawhash(page['text'].encode()))
                self.assertEqual(page['raw_span'],[0,len(page['text'].encode())])
            self.assertFalse({'curator','scores','reason','region_id','cost','arm_id'} & packet.keys())
            self.assertEqual(c.review_packet(frozen['records'][0]['proposal'],(inp/'corpus/0.md').read_bytes(),(inp/'corpus/2.md').read_bytes()),frozen['packets'][frozen['records'][0]['packet_sha256']])

    def test_A_and_B_have_distinct_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp,frozen=self.prepare(Path(tmp));regions=read(inp/'regions.json');rows=frozen['records']
            self.assertEqual(len(frozen['A']['groups'][0]['pool']),2)
            self.assertEqual(frozen['B']['status'],'unrun-not-authorized')
            good=dict(relationship_support=2,target_support=2,placement=2,uncertainty=False,contradiction=False,relation='target_elaborates_source')
            bad=dict(good,uncertainty=True)
            reviews={r['proposal']['proposal_id']:{'first':good,'confirmation':good if i==0 else bad} for i,r in enumerate(rows)}
            self.assertEqual(len(c.analysis_pool(rows,regions,'B',reviews)['groups'][0]['pool']),1)
            self.assertEqual(len(c.analysis_pool(rows,regions)['groups'][0]['pool']),2)

    def test_curator_ledger_authority_score_and_closed_provenance(self):
        old=old_tests.EmbeddedLinks();old.setUp();p=copy.deepcopy(old.p);p['schema_version']=ledger.VERSION+'.proposal'
        p['provenance']={'kind':'curator','manifest_sha256':e.digest({}),'candidate_key':'x','timestamp':'fixture'}
        log=[ledger.event([],p,'proposed',p,'curator')]
        log.append(ledger.event(log,p,'schema_validated',{'proposal_sha256':e.digest(p)}))
        log.append(ledger.event(log,p,'structurally_validated',{'eligible':True,'reason':'valid','validation_sha256':e.digest({})}))
        review=dict(kind='curator',manifest_sha256=e.digest({}),semantic_score=5,relationship_support=2,target_support=2,placement=1,
                    uncertainty=False,contradiction=False,relation=p['relation'],decision='eligible')
        log.append(ledger.event(log,p,'reviewed',review,'curator'))
        self.assertEqual(ledger.replay(log,e.digest(log[-1]))['unique_events'],4)
        with self.assertRaises(ValueError):ledger.event(log[:3],p,'reviewed',dict(review,semantic_score=6),'curator')
        with self.assertRaises(ValueError):ledger.event([],p,'proposed',p,'model')
        with self.assertRaises(ValueError):ledger.validate_proposal(dict(p,provenance=dict(p['provenance'],provider='invented')))
        with self.assertRaises(ValueError):ledger.replay(log[:-1],e.digest(log[-1]))

    def test_reciprocal_alternatives_and_set_conflict(self):
        old=old_tests.EmbeddedLinks();old.setUp();p=copy.deepcopy(old.p);p['schema_version']=ledger.VERSION+'.proposal'
        p['provenance']={'kind':'curator','manifest_sha256':e.digest({}),'candidate_key':'x','timestamp':'fixture'}
        q=dict(p,proposal_id='q',source_id='b',target_id='a')
        self.assertTrue(c.constraints.pool_conflicts([p,q]))
        c.constraints.validate_selected([p,q],[p['proposal_id']],[])
        with self.assertRaisesRegex(ValueError,'reciprocal'):c.constraints.validate_selected([p,q],[p['proposal_id'],'q'],[])

    def test_stale_sources_and_incomplete_audit_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp,frozen=self.prepare(root);(inp/'corpus/0.md').write_bytes(b'changed')
            out=root/'bad';out.mkdir()
            with self.assertRaisesRegex(ValueError,'immutable-source'):c.prepare(inp,out,{'timestamp':'x','run_id':'x'})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp=root/'input';self.fixture(inp);write(inp/'curation-decisions.json',[]);out=root/'out';out.mkdir()
            with self.assertRaisesRegex(ValueError,'complete-audit'):c.prepare(inp,out,{'timestamp':'x','run_id':'x'})

    def test_complete_distinguishing_and_tie_paths_with_replay(self):
        class Scorer:
            def __init__(self,graph,pool,policy):self.pool=pool;self.cache={};self.attempts=[]
            def __call__(self,prefix):
                self.attempts.append(list(prefix));p=next(p for p in self.pool if p['proposal_id']==prefix[0])
                value=0. if p['target_id']=='5' else 1.
                return {'status':'finite_estimate','schedule_costs':[value,value]}
        for tie in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);inp,frozen=self.prepare(root);out=root/'selected';out.mkdir()
                class Tie(Scorer):
                    def __call__(self,prefix):return {'status':'finite_estimate','schedule_costs':[0.,0.]}
                result=c.select(inp,out,{'diagnostic':False},Tie if tie else c.BoundedBlockScorer)
                self.assertEqual(result['candidates'],2);self.assertEqual(result['provider_calls'],0)
                group=read(out/'groups.json')[0]
                self.assertEqual(group['arms']['S']['selected'],group['arms']['Z']['selected'])
                self.assertEqual(group['arms']['M']['trace'][0]['status'],'all_candidate_tie' if tie else 'resolved_finite_order')
                for arm,tip in read(out/'ledger-anchors.json').items():
                    import json
                    log=[json.loads(x) for x in (out/'A'/arm/'intent-events.jsonl').read_bytes().splitlines()]
                    ledger.replay(log,tip)
                self.assertEqual(len(list((out/'A/original/corpus').glob('*.md'))),6)
                import shutil
                shutil.copytree(out,inp/'result');write(inp/'ledger-anchors.json',read(out/'ledger-anchors.json'))
                checked=root/'checked';checked.mkdir()
                self.assertEqual(c.verify(inp,checked,{})['files_verified'],24)
                (inp/'result/A/S/corpus/0.md').write_bytes(b'tamper')
                with self.assertRaisesRegex(ValueError,'independent-copy'):c.verify(inp,checked,{})

    def test_selection_freeze_tamper_fails_before_scoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp,_=self.prepare(root);(inp/'curation-decisions.json').write_bytes(b'[]');out=root/'selected';out.mkdir()
            with self.assertRaisesRegex(ValueError,'frozen-input'):c.select(inp,out,{})


if __name__=='__main__':unittest.main()
