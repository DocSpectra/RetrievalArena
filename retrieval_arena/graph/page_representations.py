"""Basis-invariant full magnetic heat rows, simple features and descriptive maps.

This transductive representation does not infer document roles or route lengths.
See docs/evaluations/page_representations.md for the versioned contract.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import numpy as np
from . import magnetic_spectrum as ms

VERSION = 'magnetic_page_representations.v1'

def heat_from_eigensystem(values, vectors, t):
    if not np.isfinite(t) or t <= 0:
        raise ValueError('heat-scale')
    return (vectors * np.exp(-t * values)) @ vectors.conj().T

def heat_kernel(nodes, edges, q=0.25, t=1.0, tolerance=1e-10):
    """Full complex128 heat operator, with qualification before downstream use."""
    if not nodes or len(nodes) != len(set(nodes)):
        raise ValueError('heat-node-universe')
    admitted=ms.admit_graph(nodes, edges)
    if admitted['self_loops_removed'] or admitted['duplicate_edges_removed']:
        raise ValueError('heat-noncanonical-edges')
    if not np.isfinite(q) or not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('heat-config')
    lap = ms.normalized_magnetic_matrix(nodes, edges, q)
    values, vectors = np.linalg.eigh(lap)
    kernel = heat_from_eigensystem(values, vectors, t)
    checks = {
        'operator_hermitian':float(np.max(np.abs(lap-lap.conj().T))),
        'relative_residual':float(np.linalg.norm(lap@vectors-vectors*values)/max(1,np.linalg.norm(lap))),
        'orthogonality':float(np.max(np.abs(vectors.conj().T@vectors-np.eye(len(nodes))))),
        'bound_error':float(max(0,-values.min(),values.max()-2)),
        'kernel_hermitian':float(np.max(np.abs(kernel-kernel.conj().T))),
        'kernel_eigenvalue_min':float(np.exp(-t*values).min()),
        'kernel_eigenvalue_max':float(np.exp(-t*values).max())}
    comps = ms.weak_components(nodes, edges)
    labels = {v:i for i,c in enumerate(comps) for v in c}
    cross = np.array([[labels[s]!=labels[d] for d in nodes] for s in nodes])
    checks['cross_component_max_before_zero'] = float(np.max(np.abs(kernel[cross]))) if cross.any() else 0.0
    passed = all(checks[k] <= (1e-12 if k=='operator_hermitian' else tolerance)
                 for k in ['operator_hermitian','relative_residual','orthogonality','bound_error',
                           'kernel_hermitian','cross_component_max_before_zero'])
    passed &= checks['kernel_eigenvalue_min'] > 0 and checks['kernel_eigenvalue_max'] <= 1+tolerance
    if not passed: raise ValueError('heat-qualification:'+json.dumps(checks))
    # Roundoff must not create cross-component evidence. Isolates keep H_ii=1.
    kernel[cross] = 0
    receipt = {'version':VERSION,'q':q,'t':t,'tolerance':tolerance,'status':'qualified',
               'graph_sha256':ms.graph_sha256(nodes,edges),'node_order_sha256':ms.digest(nodes),
               'checks':checks,'dtype':'complex128','full_eigenbasis':True,
               'operator':ms.NORMALIZATION,'phase':ms.PHASE_POLICY,
               'row_feature_dimensions':2*len(nodes)}
    return kernel, receipt

def row_features(kernel):
    return np.concatenate([kernel.real,kernel.imag],axis=1)

def pair_affinity(kernel, seed_indices, candidate_indices):
    """Mean |H[s,c]| over seeds; reversal-invariant, not a directional score."""
    if not seed_indices: raise ValueError('empty-seeds')
    return np.abs(kernel[np.ix_(seed_indices,candidate_indices)]).mean(axis=0)

def masked_heat_kernel(nodes, edges, held_out_edges, **kwargs):
    """Rebuild the entire operator after edge removal; never slice old rows."""
    held = set(map(tuple,held_out_edges)); edges = set(map(tuple,edges))
    if not held <= edges: raise ValueError('mask-unknown-edge')
    return heat_kernel(nodes,sorted(edges-held),**kwargs)

def simple_features(nodes, edges):
    incoming=Counter(t for _,t in edges); outgoing=Counter(s for s,_ in edges)
    es=set(map(tuple,edges)); reciprocal=Counter(s for s,t in es if (t,s) in es)
    comps=ms.weak_components(nodes,edges)
    membership={v:(i,len(c)) for i,c in enumerate(comps) for v in c}
    levels,_,_,residual=ms.trophic_levels(nodes,edges)
    if residual > 1e-10: raise ValueError('trophic-residual')
    rows=[]
    for v in nodes:
        ci,size=membership[v]; missing=v not in levels
        rows.append({'doc_id':v,'in_degree':incoming[v],'out_degree':outgoing[v],
                     'reciprocal_degree':reciprocal[v],'component_id':ci,
                     'component_size':size,'in_largest_component':ci==0,
                     'isolate':missing,'trophic_level':levels.get(v),
                     'trophic_missing':missing})
    return rows, {'trophic_residual':residual,'anchor':'minimum level zero within each weak component with edges',
                  'component_id_use':'grouping only, never categorical predictor'}

def descriptive_map(nodes, kernel, anchors):
    """Nearest fixed-anchor groups in complex row Euclidean distance.

    Coordinate table retains all anchor real/imag entries. The display uses
    distance contrasts to four fixed anchors; overlap is not role equivalence.
    """
    indices=[nodes.index(v) for v in anchors]
    norm=np.sum(np.abs(kernel)**2,axis=1)
    distances=np.maximum(0,norm[:,None]+norm[indices][None,:]-2*(kernel@kernel[indices].conj().T).real)
    groups=distances.argmin(axis=1)
    coords=np.zeros((len(nodes),2))
    for k in range(min(2,len(indices)//2)):
        coords[:,k]=np.sqrt(distances[:,2*k])-np.sqrt(distances[:,2*k+1])
    mass=np.abs(kernel)**2
    self_share=np.diag(mass)/mass.sum(axis=1)
    participation=mass.sum(axis=1)**2/(mass**2).sum(axis=1)
    rows=[]
    for i,v in enumerate(nodes):
        off=kernel[i,indices].copy()
        for j,a in enumerate(indices):
            if a==i: off[j]=0
        rows.append({'doc_id':v,'x':float(coords[i,0]),'y':float(coords[i,1]),
            'group':int(groups[i]),'anchor_real':kernel[i,indices].real.tolist(),
            'anchor_imag':kernel[i,indices].imag.tolist(),'self_mass_share':float(self_share[i]),
            'effective_row_support':float(participation[i]),
            'low_anchor_coupling':bool(np.max(np.abs(off))<=1e-10)})
    return rows

def frontmatter(text):
    match=re.match(r'\A---\s*\n.*?\n---(?:\s*\n|$)',text,re.S)
    return text[match.end():] if match else text

def clean_text(text, boilerplate=()):
    text=frontmatter(text)
    text=re.sub(r'<!--.*?-->|{{[<%].*?[%>]}}|```.*?```|~~~.*?~~~',' ',text,flags=re.S)
    text=re.sub(r'!\[[^\]]*\]\([^)]*\)',' ',text)
    text=re.sub(r'\[([^\]]*)\]\([^)]*\)',r'\1',text)
    text=re.sub(r'^\s*#{1,6} .*$', ' ',text,flags=re.M)
    text=re.sub(r'https?://\S+',' ',text)
    return '\n'.join(line for line in text.splitlines() if line.strip() not in boilerplate)

def training_boilerplate(texts):
    counts=Counter(line.strip() for text in texts for line in set(clean_text(text).splitlines()) if line.strip())
    return {line for line,n in counts.items() if n>=max(2,int(np.ceil(.1*len(texts))))}

def review_cards(pages, texts, count=40):
    """Round-robin topic strata, SHA256 ID order, no roles/features in cards."""
    strata=defaultdict(list)
    for row in pages: strata[row['source_family']].append(row['doc_id'])
    for ids in strata.values(): ids.sort(key=lambda s:hashlib.sha256(s.encode()).hexdigest())
    chosen=[]
    while len(chosen)<min(count,len(pages)):
        # Hash ordering avoids exhausting a lexical prefix when strata outnumber cards.
        for group in sorted(strata,key=lambda s:hashlib.sha256(s.encode()).hexdigest()):
            if strata[group] and len(chosen)<count: chosen.append((group,strata[group].pop(0)))
    return [{'doc_id':v,'topic_stratum':g,'excerpt':frontmatter(texts[v])[:3000],
             'role':None,'ambiguity_note':'','missing_coverage_note':''} for g,v in chosen]

def save_bundle(root, nodes, kernel, receipt, content_identity):
    root=Path(root); root.mkdir(parents=True,exist_ok=False)
    np.save(root/'kernel.npy',kernel,allow_pickle=False)
    np.save(root/'features.npy',row_features(kernel),allow_pickle=False)
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir())}
    manifest={**receipt,'schema_version':VERSION,'nodes':nodes,'content_identity':content_identity,
              'files':files,'affinity':'mean abs(H[seed,candidate]); global-reversal invariant',
              'direction_data':'complex entries retained; direction not equivalent to route reachability'}
    (root/'manifest.json').write_text(json.dumps(manifest,sort_keys=True)+'\n',encoding='utf-8')
    return manifest

def load_bundle(root, *, content_identity=None, graph_sha256=None, q=None, t=None):
    root=Path(root); manifest=json.loads((root/'manifest.json').read_bytes())
    if manifest.get('schema_version')!=VERSION or manifest.get('status')!='qualified': raise ValueError('bundle-not-qualified')
    if content_identity is not None and manifest['content_identity']!=content_identity: raise ValueError('bundle-content-mismatch')
    for key,expected in [('graph_sha256',graph_sha256),('q',q),('t',t)]:
        if expected is not None and manifest[key]!=expected: raise ValueError('bundle-'+key+'-mismatch')
    if manifest['node_order_sha256']!=ms.digest(manifest['nodes']): raise ValueError('bundle-node-order')
    for name in ['kernel.npy','features.npy']:
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=manifest['files'][name]: raise ValueError('bundle-file-hash')
    kernel=np.load(root/'kernel.npy',allow_pickle=False); features=np.load(root/'features.npy',allow_pickle=False)
    n=len(manifest['nodes'])
    if kernel.shape!=(n,n) or features.shape!=(n,2*n) or not np.isfinite(kernel).all() or not np.array_equal(features,row_features(kernel)):
        raise ValueError('bundle-array-contract')
    return manifest,kernel,features
