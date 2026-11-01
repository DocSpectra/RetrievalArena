import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from retrieval_arena import embedded_links as e
from retrieval_arena import embedded_token_admission as t
from retrieval_arena.embedded_link_audit import write,portable_inventory,read


def job():
    passage={'semantic_text':'Read the manual.','raw_excerpt':'Read the manual.','raw_span':[0,16]}
    data={'source_paragraphs':[passage],'targets':[{'target_id':'target','paragraphs':[passage,passage]}]}
    body={'model':'gpt-6-luna','store':False,'input':'Instructions'+t.MARKER+e.canonical(data).decode(),
          'text':{'format':{'type':'json_schema','schema':{'type':'object'}}}}
    return {'job_id':e.digest(body),'body':body}


class TokenAdmission(unittest.TestCase):
    def test_unapproved_stops_before_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'in';out=Path(tmp)/'out';inp.mkdir();out.mkdir();write(inp/'jobs.json',[job()])
            inv=portable_inventory(inp);write(inp/'operator-request.json',{'input_inventory':inv,'input_inventory_sha256':e.digest(inv)})
            with patch.object(t,'counter_client',side_effect=AssertionError('client must not open')) as factory:
                with self.assertRaisesRegex(ValueError,'authorization-required'): asyncio.run(t.run(inp,out,{'approved':False}))
                factory.assert_not_called()

    def test_exact_authorization_inventory_and_schema_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp);write(inp/'jobs.json',[job()]);inv=portable_inventory(inp)
            write(inp/'operator-request.json',{'input_inventory':inv,'input_inventory_sha256':e.digest(inv)})
            auth={'approved':True,'scope':'provider_token_count_only','input_inventory_sha256':e.digest(inv),
                  'provider':'openai','model':'gpt-6-luna'}
            self.assertEqual(t.admit(inp,auth),[job()])
            auth['input_inventory_sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'input-binding'):t.admit(inp,auth)

    def test_count_schema_retry_and_whole_passage_omission(self):
        calls=[]
        async def count(**kwargs):
            calls.append(kwargs)
            if len(calls)==1: raise TimeoutError()
            return {'input_tokens':16001 if len(calls)==2 else 100}
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);results=asyncio.run(t.count_jobs([job()],out,count))
            self.assertEqual(results[0]['status'],'admitted');self.assertEqual(len(calls),3)
            self.assertEqual(calls[0]['text'],job()['body']['text'])
            data=read(next((out/'admitted').glob('*.json')))
            disclosed=__import__('json').loads(data['input'].split(t.MARKER,1)[1])
            self.assertEqual(len(disclosed['targets'][0]['paragraphs']),1)
            self.assertEqual(read(out/'counts/0001.json')['attempt']['status'],'technical_failure')

    def test_resource_ceiling_has_no_calls(self):
        async def count(**kwargs):raise AssertionError('no time remains')
        with tempfile.TemporaryDirectory() as tmp:
            results=asyncio.run(t.count_jobs([job()],Path(tmp),count,wall_seconds=0))
            self.assertEqual(results[0]['status'],'resource_limit')

    def test_over_cap_single_target_is_omitted(self):
        async def count(**kwargs):return {'input_tokens':20000}
        with tempfile.TemporaryDirectory() as tmp:
            results=asyncio.run(t.count_jobs([job()],Path(tmp),count))
            self.assertEqual(results[0]['status'],'over_cap_omitted')


if __name__=='__main__':unittest.main()
