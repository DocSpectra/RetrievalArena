import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from scipy.linalg import expm
from retrieval_arena.graph import magnetic_spectrum as ms
from retrieval_arena.graph import page_representations as pr

CASES={
    'chain':(['a','b','c'],[('a','b'),('b','c')]),
    'cycle':(['a','b','c'],[('a','b'),('b','c'),('c','a')]),
    'reciprocal':(['a','b'],[('a','b'),('b','a')]),
    'disconnected_isolate':(['a','b','c','d','e'],[('a','b'),('c','d')]),
    'repeated':(['a','b','c','d'],[('a','b'),('c','d')]),
    'localized':(['a','b','c','d','e','f'],[('a','b'),('b','c'),('c','a'),('c','d'),('d','e'),('d','f')])}

class PageRepresentationTests(unittest.TestCase):
    def test_all_fixtures_spectral_heat_expm_hermitian_and_positive_definite(self):
        for name,(nodes,edges) in CASES.items():
            for q in [0,.1,.25]:
                with self.subTest(name=name,q=q):
                    h,r=pr.heat_kernel(nodes,edges,q)
                    np.testing.assert_allclose(h,expm(-ms.normalized_magnetic_matrix(nodes,edges,q)),atol=1e-12)
                    np.testing.assert_allclose(h,h.conj().T,atol=1e-12)
                    self.assertGreater(np.linalg.eigvalsh(h).min(),0)
                    if q==0: self.assertGreaterEqual(h.real.min(),-1e-12)
                    self.assertEqual(r['status'],'qualified')

    def test_reversal_conjugates_but_magnitude_affinity_cannot_detect_it(self):
        nodes,edges=CASES['localized']
        h,_=pr.heat_kernel(nodes,edges); rev,_=pr.heat_kernel(nodes,[(b,a) for a,b in edges])
        np.testing.assert_allclose(rev,h.conj(),atol=1e-12)
        np.testing.assert_allclose(pr.pair_affinity(h,[0,2],list(range(6))),pr.pair_affinity(rev,[0,2],list(range(6))),atol=1e-12)
        self.assertGreater(np.abs(h.imag).max(),.01)

    def test_node_permutation_rows_columns_and_refitted_ridge_predictions(self):
        nodes,edges=CASES['localized']; h,_=pr.heat_kernel(nodes,edges)
        perm=np.array([3,5,1,0,4,2]); hp,_=pr.heat_kernel([nodes[i] for i in perm],edges)
        np.testing.assert_allclose(hp,h[np.ix_(perm,perm)],atol=1e-12)
        # Fixed labeled node split, matched column scaling and L2 fit.
        labels=np.array([0,1,0,1]); train=np.array([0,1,2,3]); test=np.array([4,5])
        def fit(k,tr,te):
            x=pr.row_features(k); mu=x[tr].mean(0); scale=x[tr].std(0); scale[scale<1e-12]=1
            x=(x-mu)/scale
            beta=np.linalg.solve(x[tr].T@x[tr]+np.eye(x.shape[1]),x[tr].T@labels)
            return x[te]@beta
        inverse=np.argsort(perm)
        np.testing.assert_allclose(fit(h,train,test),fit(hp,inverse[train],inverse[test]),atol=1e-10)

    def test_complete_repeated_eigenspace_rotation(self):
        nodes,edges=CASES['repeated']; l=ms.normalized_magnetic_matrix(nodes,edges)
        values,vectors=np.linalg.eigh(l); rotated=vectors.copy()
        for indices in [[0,1],[2,3]]:
            rot=np.array([[1,1j],[1j,1]])/np.sqrt(2)
            rotated[:,indices]=vectors[:,indices]@rot
        np.testing.assert_allclose(pr.heat_from_eigensystem(values,vectors,1),pr.heat_from_eigensystem(values,rotated,1),atol=1e-12)

    def test_isolate_and_zero_disconnected_coupling(self):
        nodes,edges=CASES['disconnected_isolate']; h,_=pr.heat_kernel(nodes,edges)
        self.assertEqual(h[4,4],1); self.assertEqual(h[0,3],0); self.assertEqual(h[4,0],0)
        rows,_=pr.simple_features(nodes,edges)
        self.assertTrue(rows[4]['isolate']); self.assertIsNone(rows[4]['trophic_level'])
        self.assertAlmostEqual(rows[1]['trophic_level']-rows[0]['trophic_level'],1)

    def test_q0_matched_operator_and_reciprocal_independence(self):
        nodes,edges=CASES['reciprocal']
        a,_=pr.heat_kernel(nodes,edges,0); b,_=pr.heat_kernel(nodes,edges,.25)
        np.testing.assert_array_equal(a,b)

    def test_masked_operator_rebuild_and_loader_identity_integrity(self):
        nodes,edges=CASES['chain']; h,r=pr.masked_heat_kernel(nodes,edges,[('b','c')])
        self.assertEqual(h[1,2],0)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'bundle'; pr.save_bundle(root,nodes,h,r,'content-a')
            m,k,x=pr.load_bundle(root,content_identity='content-a')
            np.testing.assert_array_equal(x,pr.row_features(k))
            with self.assertRaisesRegex(ValueError,'content-mismatch'): pr.load_bundle(root,content_identity='b')
            with self.assertRaisesRegex(ValueError,'graph_sha256-mismatch'): pr.load_bundle(root,graph_sha256='wrong')
            with self.assertRaisesRegex(ValueError,'q-mismatch'): pr.load_bundle(root,q=0)
            with (root/'features.npy').open('ab') as f: f.write(b'bad')
            with self.assertRaisesRegex(ValueError,'file-hash'): pr.load_bundle(root)

    def test_cleaning_training_only_boilerplate_and_blind_cards(self):
        text='---\ntitle: reference\n---\n# Reference\n[body](secret/path) <!-- hidden -->\n```\nrole: target\n```'
        clean=pr.clean_text(text)
        for word in ['reference','Reference','secret/path','hidden','target']: self.assertNotIn(word,clean)
        self.assertIn('body',clean)
        self.assertEqual(pr.training_boilerplate(['same\nA','same\nB']),{'same'})
        pages=[{'doc_id':v,'source_family':g} for v,g in [('a','x'),('b','x'),('c','y')]]
        cards=pr.review_cards(pages,{r['doc_id']:text for r in pages},3)
        self.assertEqual(len(cards),3); self.assertTrue(all(c['role'] is None for c in cards))

    def test_deterministic_maps_and_invalid_input(self):
        nodes,edges=CASES['localized']; h,_=pr.heat_kernel(nodes,edges); h2,_=pr.heat_kernel(nodes,edges)
        np.testing.assert_array_equal(h,h2)
        self.assertEqual(pr.descriptive_map(nodes,h,nodes[:4]),pr.descriptive_map(nodes,h2,nodes[:4]))
        with self.assertRaises(ValueError): pr.heat_kernel(nodes,edges,t=0)
        with self.assertRaises(ValueError): pr.masked_heat_kernel(nodes,edges,[('a','f')])

    def test_small_operator_end_to_end_exact_inventory_and_fresh_outputs(self):
        from containers.verification.page_representations import run
        from retrieval_arena.candidate_retrieval import canonical,regular_inventory
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'in'; out=Path(tmp)/'out'; inp.mkdir(); out.mkdir(); (inp/'corpus').mkdir()
            nodes,edges=CASES['disconnected_isolate']; pages=[]
            def write(p,x): p.write_bytes(canonical(x)+b'\n')
            for n in nodes:
                p=inp/'corpus'/f'{n}.md'; p.write_text('# page\nbody '+n)
                pages.append({'doc_id':n,'source_family':'topic','raw_sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
            write(inp/'pages.json',pages)
            write(inp/'graph.json',{'schema_version':ms.BUNDLE_SCHEMA,'graph_id':'fixture','nodes':nodes,'edges':edges,'canonical_graph_sha256':ms.graph_sha256(nodes,edges)})
            write(inp/'config.json',{'schema_version':'retrieval_arena.page_representations_config.v1','graphs':{'main':'graph.json'},
                'parameters':[[.25,1],[0,1]],'kernel_tolerance':1e-10,'map_anchors':4,'card_count':3,'max_runtime_seconds':120,'max_output_bytes':10000000})
            inv=regular_inventory(inp)
            write(inp/'operator-request.json',{'schema_version':'retrieval_arena.page_representations_request.v1','command':'export-page-representations','input_inventory':inv,'input_inventory_sha256':ms.digest(inv)})
            summary=run(inp,out)
            self.assertEqual(len(summary['bundles']),2)
            self.assertTrue((out/'page_maps.png').is_file())
            with self.assertRaisesRegex(ValueError,'not-fresh'): run(inp,out)
            out2=Path(tmp)/'out2'; out2.mkdir(); (inp/'unexpected.txt').write_text('bad')
            with self.assertRaisesRegex(ValueError,'inventory'): run(inp,out2)

if __name__=='__main__': unittest.main()
