"""Consumer-supplied authored sources through the production ID interface.

Fixtures are explicitly marked. This module never presents fake completions as
external model results and never supplies a production semantic judgment.
"""
import copy
import json
from pathlib import Path

from . import embedded_links as e
from . import embedded_interface as interface
from . import embedded_study as study
from .embedded_demonstrator import paragraphs
from .embedded_link_audit import read,write


def prepare(inp,out,cfg):
    fixture=read(inp/cfg['fixture_manifest']);manifest=read(inp/'study-manifest.json')
    contents={p.stem:p.read_bytes() for p in sorted((inp/cfg['corpus']).glob('*.md'))}
    if len(contents)!=6:raise ValueError('authored-six-source-fixture')
    pages=[{'doc_id':n,'path':n+'.md','raw_sha256':e.rawhash(raw)} for n,raw in contents.items()]
    page_map={p['path']:p for p in pages}
    occurrences=[r for p in pages for r in e.occurrences(contents[p['doc_id']],p,page_map)]
    if e.project(occurrences,set(contents))!=[tuple(x) for x in fixture['baseline_edges']]:
        raise ValueError('authored-content-graph')
    candidates={c['candidate_key']:c for c in fixture['candidates']}
    scenario=next(s for s in fixture['scenarios'] if s['id']==cfg['scenario'])
    used=[candidates[k] for k in scenario['candidate_order']];packets=[]
    region={'region_id':'authored:'+scenario['id'],'assignment':'development','nodes':sorted(contents),
        'halo_nodes':[],'component_membership':{n:'authored-workflow' for n in contents},
        'source_families':['authored-workflow'],'corpus_id':'consumer-authored-fixture','snapshot_id':e.digest(contents_hashes(contents))}
    for source in dict.fromkeys(c['source'] for c in used):
        sp,_=paragraphs(contents[source],source=True)
        relevant=[c for c in used if c['source']==source]
        sp=[p for p in sp if any(c['anchor'] in p['text'] for c in relevant)]
        targets=[]
        for c in relevant:
            target=c['target'];tp,_=paragraphs(contents[target]);tp=[p for p in tp if c['target_quote'] in p['text']]
            targets.append({'target_id':target,'path':target+'.md','title':'Authored page '+target,
                'source_sha256':e.rawhash(contents[target]),'paragraphs':tp})
        p={'source_id':source,'source_path':source+'.md','source_sha256':e.rawhash(contents[source]),
            'source_paragraphs':sp,'targets':targets,'region_id':region['region_id']}
        p['packet_sha256']=e.digest(p);packets.append(p)
    out.mkdir(exist_ok=True)
    for n,raw in contents.items():
        dest=out/'corpus'/(n+'.md');dest.parent.mkdir(exist_ok=True);dest.write_bytes(raw)
    manifest=copy.deepcopy(manifest);manifest['maximum_group_arm_edits']=1
    manifest['authored_fixture']=True
    manifest['authored_tie_order']=[{'source_id':c['source'],'target_id':c['target']} for c in used]
    manifest['allowed_source_ids']=[p['source_id'] for p in packets]
    write(out/'pages.json',pages);write(out/'regions.json',[region]);write(out/'packets.json',packets)
    write(out/'original.json',{'nodes':sorted(contents),'edges':e.project(occurrences,set(contents),'original')})
    write(out/'frozen-occurrences.json',occurrences);write(out/'study-manifest.json',manifest)
    # Same child-packet projector used for the real population.
    from .embedded_refinement import prepare_interface
    projection=out/'projection';projection.mkdir();prepare_interface(out,projection,{})
    write(out/'packets.json',read(projection/'packets.json'))
    (out/'token-input').mkdir()
    write(out/'token-input/jobs.json',read(projection/'jobs.json'))
    manifest['allowed_initial_token_request_hashes']=[j['job_id'] for j in read(projection/'jobs.json')]
    write(out/'study-manifest.json',manifest)
    study.seal(out)
    return {'scenario':scenario['id'],'mock_or_technical_fixture_only':True,'packets':len(packets),'sources':6}


def contents_hashes(contents):return {k:e.rawhash(v) for k,v in contents.items()}


class MockTransport:
    """Explicit fake SDK adapter through the real accounting/executor callback."""
    def __init__(self,settings,out,*,mode='eligible'):
        from .model_providers.ledger import CostLedger
        self.settings=settings;self.mode=mode
        self.ledger=CostLedger(out/'cost-ledger.jsonl',settings['pricing'],settings['spend_cap_usd'],
            provider=settings['provider'],model=settings['model'])
    @staticmethod
    def request(prompt,schema,settings):
        # The offline fixture does not require optional SDK imports, while the
        # production transport and native SDK schema projection have own tests.
        from .model_providers.openai import OpenAITransport
        return OpenAITransport.request(prompt,schema,dict(settings,generation={'reasoning_effort':'low','strict':True}))
    async def __call__(self,prompt,schema,settings,*,audit=None):
        from .embedded_refinement import MARKER
        data=json.loads(prompt.split(MARKER,1)[1]);generation=bool({'proposals','dispositions'} & schema['properties'].keys())
        if generation:
            items=[]
            for target in data['targets']:
                anchor='validation rules' if target['target_id']=='2' else 'release checklist'
                sentence=next(s for p in data['source_paragraphs'] for s in p['sentences'] if anchor in s['text'])
                p=target['paragraphs'][0]
                items.append({'source_id':data['source_id'],'target_id':target['target_id'],
                    'source_sentence_id':sentence['sentence_id'],'anchor':anchor,
                    'target_evidence':[{'passage_id':p['passage_id'],'quote':p['text']}],
                    'relation':'target_elaborates_source','stated_intent':'Mock fixture declaration.',
                    'expected_reading_use':'Mock technical evidence only.','basis':'supported_inference','confidence':None})
            payload={'proposals':items if self.mode!='empty' else []}
            if 'dispositions' in schema['properties']:
                payload={'dispositions':[{'target_id':p['target_id'],
                    'disposition':'no_supported_link' if self.mode=='empty' else 'supported_link',
                    'reason':'MOCK technical disposition, no external model.',
                    'proposal':None if self.mode=='empty' else p} for p in items]}
        else:
            source=data['source_paragraphs'][0];target=data['targets'][0]['paragraphs'][0]
            negative=self.mode=='negative'
            payload={'relation':'insufficient_evidence' if negative else 'target_elaborates_source',
                'relationship_support':0 if negative else 2,'target_support':2,'placement':2,
                'uncertainty':negative,'contradiction':False,'assessment':'MOCK: consumer-authored assertion, not a model result.',
                'source_evidence':[{'passage_id':source['passage_id'],'quote':source['text']}],
                'target_evidence':[{'passage_id':target['passage_id'],'quote':target['text']}]}
        body=self.request(prompt,schema,settings);reservation=self.ledger.reserve(settings['max_input_tokens']+1024,settings['max_output_tokens'],e.digest(body))
        if audit:audit({'event':'request','body':body,'reservation':reservation})
        text=e.canonical(payload).decode()
        raw=({'output':[{'type':'message','content':[{'type':'output_text','text':text}]}]} if settings['provider']=='openai' else
             {'content':[{'type':'text','text':text}]})
        raw['mock']=True
        if audit:audit({'event':'response','body':raw,'request_id':'MOCK-NOT-A-PROVIDER'})
        usage={'input_tokens':100,'output_tokens':100};cost=self.ledger.report(reservation,usage,'MOCK-NOT-A-PROVIDER')
        return {'text':text,'model':settings['model'],'revision':settings['revision'],'usage':usage,'cost':cost,'mock':True}


async def mock_count(*args):return {'input_tokens':100,'mock':True}
