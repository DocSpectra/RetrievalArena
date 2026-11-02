"""Offline bound historical-score replay, edited artifacts and structural export."""
import copy
import math
from dataclasses import replace

import networkx as nx

from . import embedded_curated as c
from . import embedded_links as e
from . import embedded_minimum_set as m
from .embedded_link_audit import read, write
from .graph import block_phase as bp


def graph_for(region, frozen, occurrences):
    nodes=set(region['nodes'])
    return {'nodes':region['nodes'],'edges':e.project([r for r in occurrences if r['source_id'] in nodes],nodes),
        'audit_binding':{'freeze_sha256':e.digest(frozen),'region':region,'mask_sha256':e.digest(occurrences)}}


def load_bound(inp):
    pages,_,contents,regions=c.f.load_sources(inp);frozen=read(inp/'curated-freeze.json')
    for name,h in frozen['input_bindings'].items():
        if e.rawhash((inp/name).read_bytes())!=h:raise ValueError('minimum-set-frozen-input')
    if c.analysis_pool(frozen['records'],regions)!=frozen['A']:raise ValueError('minimum-set-pool-drift')
    receipt=read(inp/'freeze-receipt.json')
    if receipt['curated_freeze_sha256']!=e.rawhash((inp/'curated-freeze.json').read_bytes()):raise ValueError('minimum-set-freeze-receipt')
    binding=read(inp/'study-binding.json');history=inp/'historical'
    actual={p.relative_to(history).as_posix():e.rawhash(p.read_bytes()) for p in history.rglob('*') if p.is_file()}
    if actual!=binding['historical_files']:raise ValueError('minimum-set-historical-inventory')
    manifest=read(history/'manifest.json')
    for item in manifest['outputs']:
        p=history/item['path']
        if e.rawhash(p.read_bytes())!=item['sha256'] or p.stat().st_size!=item['size_bytes']:raise ValueError('minimum-set-historical-manifest')
    if e.digest(manifest['outputs'])!=manifest['outputs_sha256']:raise ValueError('minimum-set-manifest-hash')
    source=read(history/'summary.json')['image']
    if source['RA_SOURCE_COMMIT']!=binding['historical_receipt']['source_commit'] or source['RA_IMAGE_REFERENCE']!=binding['historical_receipt']['image']['reference']:
        raise ValueError('minimum-set-historical-image')
    groups=read(history/'groups.json')
    if len(groups)!=len(regions) or len(groups)!=len(frozen['A']['groups']):raise ValueError('minimum-set-group-coverage')
    occurrences=read(inp/'frozen-occurrences.json');proofs=[]
    policy=dict(m.v2.DEFAULT_POLICY,maximum=1,constraint_version=c.constraints.VERSION)
    for region,plan,group in zip(regions,frozen['A']['groups'],groups):
        if group['region_id']!=region['region_id'] or group['top_tier']!=plan['top_tier']:raise ValueError('minimum-set-group-identity')
        graph=graph_for(region,frozen,occurrences)
        audit=read(history/'numerical'/region['region_id'].replace(':','-')/'audit.json')
        if audit['candidate_costs']!=group['candidate_costs']:raise ValueError('minimum-set-audit-costs')
        proof=m.replay_scores(plan['pool'],graph,policy,group['arms']['M'],audit)
        for arm in ('S','Z'):
            replay=m.v2.select_embedded_v2(plan['pool'],graph,policy,{},arm=arm)
            if replay!=group['arms'][arm]:raise ValueError('minimum-set-control-replay')
        proofs.append(dict(proof,region_id=region['region_id']))
    return pages,contents,regions,frozen,groups,proofs


def select(inp,out,cfg):
    pages,contents,regions,frozen,oldgroups,proofs=load_bound(inp)
    occurrences=read(inp/'frozen-occurrences.json');groups=[]
    policy=dict(m.v2.DEFAULT_POLICY,maximum=1,constraint_version=c.constraints.VERSION)
    for region,plan,old in zip(regions,frozen['A']['groups'],oldgroups):
        graph=graph_for(region,frozen,occurrences);group=copy.deepcopy(old)
        group['historical_M_v2']=group['arms']['M']
        costs=group['candidate_costs']
        new=m.select_minimum_set(plan['pool'],graph,policy,{},scorer=lambda prefix:costs[prefix[0]])
        group['arms']['M']=new
        step=new['trace'][0] if new['trace'] else None
        status=step['status'] if step else 'empty_pool'
        changed=new['selected']!=group['arms']['S']['selected']
        group['outcome']=('empty' if not plan['pool'] else 'insufficient_top_tier_competition' if not group['competitive'] else
            'qualified_changed_subset' if changed and status in ('resolved_finite_order','separated_minimum_set') else
            'resolved_agreement' if status=='resolved_finite_order' else
            'minimum_set_agreement' if status=='separated_minimum_set' else 'competitive_unresolved')
        group['comparison']={'S':group['arms']['S']['selected'],'Z':group['arms']['Z']['selected'],
            'M_v2':old['arms']['M']['selected'],'M_set':new['selected'],
            'intersection':sorted(set(group['arms']['S']['selected'])&set(new['selected'])),
            'symmetric_difference':sorted(set(group['arms']['S']['selected'])^set(new['selected'])),
            'removed':sorted(set(group['arms']['S']['selected'])-set(new['selected'])),
            'added':sorted(set(new['selected'])-set(group['arms']['S']['selected']))}
        groups.append(group)
    write(out/'primary-outcomes-sealed.json',{'selector':m.VERSION,'groups':groups,'score_replay':proofs,'B':'unrun-not-authorized'})
    # Only primary audits are copied; diagnostics are not consulted for selection.
    for region in regions:
        name=region['region_id'].replace(':','-')
        (out/'numerical'/name).mkdir(parents=True)
        write(out/'numerical'/name/'audit.json',read(inp/'historical/numerical'/name/'audit.json'))
    apps=c.materialize(inp,out,frozen,groups,pages,contents,regions)
    write(out/'groups.json',groups)
    structure=structural_export(regions,frozen,occurrences,groups,inp/'historical')
    write(out/'changed-block-structures.json',structure)
    return {'selector':m.VERSION,'groups':len(groups),'candidates':sum(g['pool_count'] for g in groups),
        'changed_groups':sum(g['outcome']=='qualified_changed_subset' for g in groups),'score_replay':proofs,
        'applications':apps,'B':'unrun-not-authorized','provider_calls':0}


def structural_export(regions,frozen,occurrences,groups,history):
    records=[];representatives=[];classes=[]
    for region,plan,group in zip(regions,frozen['A']['groups'],groups):
        graph=graph_for(region,frozen,occurrences)
        audit=read(history/'numerical'/region['region_id'].replace(':','-')/'audit.json')
        for p in plan['pool']:
            pair=[p['source_id'],p['target_id']];score=group['candidate_costs'][p['proposal_id']]
            if 'schedules' not in score:
                records.append({'proposal_id':p['proposal_id'],'status':score['status']});continue
            schedules=[]
            for schedule in score['schedules']:
                sides={side:[dict(audit['solves'][key],solve_key=key) for key in schedule[side+'_solve_keys']] for side in ('baseline','edited')}
                terms={side:[{'nodes':fit['support_nodes'],'arcs':fit['support_edges'],
                    'terms':[{'s':nodes[int(i)],'t':nodes[int(j)],'weight':float(w),'theta':float(t)}
                        for i,j,w,t in zip(*ts)]}
                    for fit in sides[side] for nodes,ts in [bp.lo.terms(fit['support_nodes'],fit['support_edges'],.25)]]
                    for side in sides}
                schedules.append({'seed':schedule['seed'],'cost':schedule['cost'],'fits':sides,'edge_tables':terms})
            before=[edge for fit in schedules[0]['fits']['baseline'] for edge in fit['support_edges']]
            after=[edge for fit in schedules[0]['fits']['edited'] for edge in fit['support_edges']]
            # Before/after membership and distinguished candidate are part of the isomorphism.
            combined=nx.DiGraph();combined.add_nodes_from({n for edge in after+before for n in edge})
            for n in combined:combined.nodes[n]['role']='source' if n==pair[0] else 'target' if n==pair[1] else 'other'
            for s,t in after:combined.add_edge(s,t,weight=1,before=[s,t] in before,added=[s,t]==pair)
            found=None;mapping=None
            for k,representative in enumerate(representatives):
                matcher=nx.algorithms.isomorphism.DiGraphMatcher(combined,representative,
                    node_match=nx.algorithms.isomorphism.categorical_node_match('role','other'),
                    edge_match=nx.algorithms.isomorphism.categorical_edge_match(['weight','before','added'],[1,False,False]))
                if matcher.is_isomorphic():found=k;mapping=next(matcher.isomorphisms_iter());break
            if found is None:
                found=len(representatives);representatives.append(combined)
                classes.append({'representative':p['proposal_id'],'members':[]})
                mapping={n:n for n in combined}
            classes[found]['members'].append(p['proposal_id'])
            undirected=combined.to_undirected()
            simple_cycle=bool(len(undirected)>2 and nx.is_connected(undirected) and all(d==2 for _,d in undirected.degree()) and len(after)==len(undirected))
            row={'proposal_id':p['proposal_id'],'region_id':region['region_id'],'added_arc':pair,
                'status':score['status'],'class':found,'mapping_to_representative':mapping,
                'before_arcs':before,'after_arcs':after,'directed_arc_weight':1,
                'simple_cycle':simple_cycle,'schedules':schedules}
            if simple_cycle:
                row['cycle']=cycle_record(nx.cycle_basis(undirected)[0],after)
            records.append(row)
    return canonical_structure({'q':.25,'classes':classes,'candidates':records,'structural_only':True})


def cycle_record(cycle,arcs):
    if len(set(cycle))!=len(cycle) or set(cycle)!={n for edge in arcs for n in edge}:
        raise ValueError('minimum-set-cycle-coverage')
    signed=0
    for s,t in zip(cycle,cycle[1:]+cycle[:1]):
        if [s,t] in arcs:signed+=1
        elif [t,s] in arcs:signed-=1
        else:raise ValueError('minimum-set-cycle-edge')
    flux=2*math.pi*.25*signed;wrapped=(flux+math.pi)%(2*math.pi)-math.pi
    return {'nodes':cycle,'length':len(cycle),'signed_arcs':signed,'flux':flux,
        'wrapped_flux':wrapped,'equal_residual_stationary_energy':len(cycle)*(1-math.cos(wrapped/len(cycle))),
        'certified_global_optimum':False}


def canonical_structure(record):
    """Validate raw cycle receipts, then canonicalize traversal gauge for replay.

    Cycle-basis traversal can reverse with process hash order. This changes flux
    sign, but neither the directed graph nor the equal-residual energy. Preserve
    original exports; validate their stated traversal before normalizing it.
    """
    result=copy.deepcopy(record)
    for row in result['candidates']:
        if 'cycle' not in row:continue
        old=row['cycle'];cycle=old['nodes'];arcs=row['after_arcs']
        if old!=cycle_record(cycle,arcs):raise ValueError('minimum-set-cycle-receipt')
        rotate=lambda seq:seq[seq.index(min(seq)):]+seq[:seq.index(min(seq))]
        canonical=min(rotate(cycle),rotate(list(reversed(cycle))))
        row['cycle']=cycle_record(canonical,arcs)
    return result


def verify(inp,out,cfg):
    pages,contents,regions,frozen,oldgroups,proofs=load_bound(inp)
    result=inp/'result';groups=read(result/'groups.json')
    seal=read(result/'primary-outcomes-sealed.json')
    if seal['groups']!=groups or seal['score_replay']!=proofs:raise ValueError('minimum-set-primary-seal')
    occurrences=read(inp/'frozen-occurrences.json');policy=dict(m.v2.DEFAULT_POLICY,maximum=1,constraint_version=c.constraints.VERSION)
    for region,plan,old,group in zip(regions,frozen['A']['groups'],oldgroups,groups):
        graph=graph_for(region,frozen,occurrences)
        replay=m.select_minimum_set(plan['pool'],graph,policy,{},scorer=lambda prefix:old['candidate_costs'][prefix[0]])
        if replay!=group['arms']['M'] or group['historical_M_v2']!=old['arms']['M']:raise ValueError('minimum-set-selection-replay')
    if structural_export(regions,frozen,occurrences,groups,inp/'historical')!=canonical_structure(read(result/'changed-block-structures.json')):
        raise ValueError('minimum-set-structure-replay')
    proof=c.verify(inp,out,cfg,selection_fn=m.select_minimum_set)
    proof.update(score_replay=proofs,selector=m.VERSION,structural_replay_equal=True)
    write(out/'verification.json',proof)
    return proof
