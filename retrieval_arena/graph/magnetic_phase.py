"""Versioned edge-relative magnetic phase descriptor; standalone, not an operator.

The contract is docs/evaluations/magnetic_phase.md. Historical node-angle
averaging is intentionally not implemented here. NumPy is an optional runtime
dependency imported only when this diagnostic is used.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import numpy as np

VERSION = 'magnetic_edge_relative_flow.v2'

@dataclass(frozen=True)
class PhaseConfig:
    q: float = .25
    modes: int = 10
    gap_relative: float = 1e-8
    amplitude_relative: float = 1e-8
    phase_absolute: float = 1e-7
    residual_relative: float = 1e-10
    orthogonality_absolute: float = 1e-10
    minimum_coverage: float = .95

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def canonical_edges(nodes, edges):
    if len(set(nodes)) != len(nodes):
        raise ValueError('duplicate-node')
    known = set(nodes)
    # Binary simple directed graph, matching historical construction.
    return sorted({(s,t) for s,t in edges if s != t and s in known and t in known})

def magnetic_matrix(nodes, edges, q=.25):
    if not np.isfinite(q):
        raise ValueError('nonfinite-q')
    idx = {v:i for i,v in enumerate(nodes)}
    a = np.zeros((len(nodes),len(nodes)), dtype=np.float64)
    for s,t in canonical_edges(nodes,edges):
        a[idx[s],idx[t]] = 1
    w = .5*(a+a.T)
    return np.diag(w.sum(axis=1)).astype(np.complex128)-w*np.exp(2j*np.pi*q*(a-a.T))

def relative_phase_counts(nodes, edges, vectors, config):
    """Pure phase stage, invariant to independent column-wise global rotations.

    Input columns must represent isolated, normalized eigenmodes. This function
    alone makes no spectral qualification claim.
    """
    idx = {v:i for i,v in enumerate(nodes)}
    es = canonical_edges(nodes,edges)
    si = np.array([idx[s] for s,t in es], dtype=int)
    ti = np.array([idx[t] for s,t in es], dtype=int)
    records=[]; directions=[]
    for j in range(vectors.shape[1]):
        v = vectors[:,j]
        threshold=config.amplitude_relative*float(np.max(abs(v)))
        eligible=(abs(v[si])>threshold)&(abs(v[ti])>threshold)
        delta=np.angle(v[ti]*v[si].conj())
        neutral=(abs(delta)<=config.phase_absolute)|((np.pi-abs(delta))<=config.phase_absolute)
        sign=np.where(eligible&~neutral,np.sign(delta),0).astype(int)
        count=int(eligible.sum()); f=int((sign>0).sum()); b=int((sign<0).sum())
        records.append({'forward':f,'backward':b,'neutral':count-f-b,'excluded':len(es)-count,
                        'eligible':count,'coverage':count/len(es) if es else 0.,
                        'imbalance':abs(f-b)/count if count else None,
                        'amplitude_threshold':threshold,'minimum_node_amplitude':float(np.min(abs(v))),
                        'zero_boundary_edges':int((eligible&(abs(delta)<=config.phase_absolute)).sum()),
                        'pi_boundary_edges':int((eligible&((np.pi-abs(delta))<=config.phase_absolute)).sum())})
        directions.append(sign.tolist())
    return records, directions

def measure(nodes, edges, config=PhaseConfig(), *, eigenpairs=None):
    """Return a scalar only for isolated selected modes and sufficient coverage.

    eigenpairs is an audit hook: supplied full eigensystems are still validated
    against the constructed matrix. It is not an alternate solver admission.
    """
    cfg=asdict(config)
    if config.modes < 1 or not isinstance(config.modes,int):
        raise ValueError('invalid-modes')
    for key in ('gap_relative','amplitude_relative','phase_absolute','residual_relative','orthogonality_absolute'):
        if not np.isfinite(cfg[key]) or cfg[key]<=0:
            raise ValueError('invalid-tolerance:'+key)
    if not 0<=config.minimum_coverage<=1 or config.phase_absolute>=np.pi/2:
        raise ValueError('invalid-coverage-or-phase-tolerance')
    es=canonical_edges(nodes,edges)
    receipt={'metric_version':VERSION,'config':cfg,'config_sha256':digest(cfg),
             'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'graph_sha256':digest({'nodes':sorted(nodes),'edges':es}),
             'dtype':'complex128','solver':'numpy.linalg.eigh_full','numpy_version':np.__version__,
             'node_count':len(nodes),'edge_count':len(es),'status':'unavailable','value':None,
             'optimization_admitted':False,'scientific_utility':'unvalidated','validity_reasons':[]}
    if len(nodes)<2 or not es:
        receipt['validity_reasons']=['empty_or_edgeless_graph']; return receipt
    l=magnetic_matrix(nodes,es,config.q)
    e,v=np.linalg.eigh(l) if eigenpairs is None else eigenpairs
    e=np.asarray(e); v=np.asarray(v)
    if e.shape!=(len(nodes),) or v.shape!=(len(nodes),len(nodes)) or not np.all(np.isfinite(e)) or not np.all(np.isfinite(v)) or np.iscomplexobj(e):
        receipt['validity_reasons']=['invalid_eigensystem']; return receipt
    scale=max(1.,float(np.linalg.norm(l,ord=np.inf)))
    residual=float(np.linalg.norm(l@v-v*e,ord='fro')/max(1.,float(np.linalg.norm(l,ord='fro'))))
    orth=float(np.linalg.norm(v.conj().T@v-np.eye(len(nodes)),ord='fro'))
    hermitian=float(np.linalg.norm(l-l.conj().T,ord='fro')/max(1.,float(np.linalg.norm(l,ord='fro'))))
    upper=min(config.modes+1,len(nodes)); gaps=np.diff(e)
    # Every selected mode must be isolated from both neighbors, including
    # index 0 and the first omitted mode. Never split an unresolved cluster.
    relevant=gaps[:min(upper,len(gaps))]
    gap_tolerance=config.gap_relative*scale
    receipt.update({'selected_indices':list(range(1,upper)), 'selected_eigenvalues':e[1:upper].tolist(),
                    'adjacent_gaps':relevant.tolist(),'gap_tolerance':gap_tolerance,
                    'matrix_scale_inf':scale,'residual_relative':residual,'orthogonality_fro':orth,
                    'hermitian_relative':hermitian})
    reasons=receipt['validity_reasons']
    if np.any(gaps<0) or residual>config.residual_relative or orth>config.orthogonality_absolute or hermitian>config.residual_relative or e[0]<-config.residual_relative*scale:
        reasons.append('invalid_matrix_or_eigenpairs')
    if np.any(relevant<=gap_tolerance):
        reasons.append('selected_mode_or_boundary_unresolved')
    if reasons: return receipt
    counts,_=relative_phase_counts(nodes,es,v[:,1:upper],config)
    receipt['per_mode']=counts
    receipt['minimum_edge_coverage']=min(c['coverage'] for c in counts)
    if any(c['eligible']==0 or c['coverage']<config.minimum_coverage for c in counts):
        reasons.append('insufficient_amplitude_coverage'); return receipt
    receipt.update(status='computed',value=float(np.mean([c['imbalance'] for c in counts])))
    return receipt

PROJECTOR_VERSION = 'magnetic_subspace_edge_polarization.v1'

def subspace_edge_metrics(nodes, edges, vectors, neutral_relative=1e-10):
    """Pure edge aggregation for an already qualified orthonormal subspace."""
    es=canonical_edges(nodes,edges); u=np.asarray(vectors)
    diag=np.sum(abs(u)**2,axis=1)
    idx={n:i for i,n in enumerate(nodes)}
    si=np.array([idx[s] for s,t in es],dtype=int); ti=np.array([idx[t] for s,t in es],dtype=int)
    corr=np.sum(u[ti]*u[si].conj(),axis=1)
    weights=np.sqrt(diag[si]*diag[ti])
    denominator=float(weights.sum()); numerator=float(corr.imag.sum())
    return {'value':abs(numerator)/denominator if denominator>0 else None,
            'weighted_edge_denominator':denominator,'signed_edge_numerator':numerator,
            'weighting':'sqrt(P_ss*P_tt)','edge_coverage':1.,'excluded_edges':0,
            'zero_weight_edges':int((weights==0).sum()),
            'neutral_edges':int((abs(corr.imag)<=neutral_relative*weights).sum()),
            'minimum_node_projector_diagonal':float(diag.min()) if diag.size else None}

def measure_subspace(nodes, edges, config=PhaseConfig(), *, eigenpairs=None):
    """Amplitude-weighted imaginary edge correlation of a complete subspace.

    Uses the lowest K modes INCLUDING index zero, extending the upper cutoff
    across an entire unresolved cluster. Its meaning differs from phase counts.
    """
    # Reuse validation/provenance and full-eigensystem numerical checks, but
    # isolated-mode availability is not relevant to a spectral projector.
    es=canonical_edges(nodes,edges)
    if len(nodes)<2 or not es:
        result=measure(nodes,es,config)
        result['metric_version']=PROJECTOR_VERSION
        return result
    l=magnetic_matrix(nodes,es,config.q)
    e,v=np.linalg.eigh(l) if eigenpairs is None else eigenpairs
    result=measure(nodes,es,config,eigenpairs=(e,v))
    result.update(metric_version=PROJECTOR_VERSION,status='unavailable',value=None)
    result['validity_reasons']=[r for r in result['validity_reasons'] if r not in
                               ('selected_mode_or_boundary_unresolved','insufficient_amplitude_coverage')]
    result.pop('per_mode',None); result.pop('minimum_edge_coverage',None)
    if result['validity_reasons']: return result
    tol=result['gap_tolerance']; gaps=np.diff(e); upper=min(config.modes,len(nodes))
    while upper<len(nodes) and gaps[upper-1]<=tol: upper+=1
    result.update(selected_indices=list(range(upper)),selected_eigenvalues=e[:upper].tolist(),
                  selection_policy='lowest_K_including_0_extend_complete_upper_cluster',
                  actual_modes=upper,cutoff_expansion=upper-min(config.modes,len(nodes)),
                  upper_boundary_gap=float(gaps[upper-1]) if upper<len(nodes) else None,
                  full_spectrum_selected=upper==len(nodes))
    # Reject a numerically ambiguous cluster inclusion decision. The margin
    # uses the observed residual with a roundoff floor, not task outcomes.
    inclusion_margin=max(config.residual_relative,100*np.finfo(float).eps)*result['matrix_scale_inf']
    relevant=gaps[min(config.modes,len(nodes))-1:upper] if upper<len(nodes) else gaps[min(config.modes,len(nodes))-1:]
    result['cluster_decision_margin']=inclusion_margin
    if any(abs(g-tol)<=inclusion_margin for g in relevant):
        result['validity_reasons'].append('ambiguous_cluster_boundary'); return result
    aggregation=subspace_edge_metrics(nodes,es,np.asarray(v)[:,:upper],config.residual_relative)
    denominator=aggregation['weighted_edge_denominator']
    value=aggregation.pop('value'); result.update(aggregation)
    # No per-node phase or weak-amplitude division. All edges are retained;
    # a zero-weight edge contributes zero numerator and denominator.
    if denominator<=config.residual_relative:
        result['validity_reasons'].append('insufficient_total_spectral_edge_weight'); return result
    if not np.isfinite(value) or value>1+config.residual_relative:
        result['validity_reasons'].append('invalid_projector_bound'); return result
    result.update(status='computed',value=min(1.,value))
    return result
