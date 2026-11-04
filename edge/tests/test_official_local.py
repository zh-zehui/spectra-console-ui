"""Runs the actual official harness against a local OpenAI-compatible HTTP fixture."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import unittest
import uuid
from spectrum_agent.common import ROOT,Context,dump


class Fixture(http.server.BaseHTTPRequestHandler):
    calls=[]
    scene_mode=False
    generic_mode=False
    reserve_mode=False
    ieee154_mode=False
    scene_context=None
    def log_message(self,*args): pass
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append(data)
        turn=len(type(self).calls)
        if type(self).ieee154_mode and turn<=5:
            ctx=type(self).scene_context
            records=[json.loads(p.read_text()) for p in (ctx.run/'index').glob('*.json')]
            def aid(kind): return next(r['artifact_id'] for r in records if r['kind']==kind)
            if turn==1: name,args='segment_spectrum',dict(capture_id=aid('capture'))
            elif turn==2: name,args='extract_subband',dict(capture_id=aid('capture'),center_hz=2405e6,bandwidth_hz=2e6,output_rate_hz=4e6)
            elif turn==3: name,args='verify_protocol',dict(artifact_id=aid('subband'),protocol='ieee802154_oqpsk',channel=11)
            elif turn==4: name,args='assess_signal_scene',dict(segmentation_id=aid('segmentation'))
            else: name,args='build_report',{}
            delta={'role':'assistant','tool_calls':[{'index':0,'id':'ieee154_call_'+str(turn),'type':'function',
                'function':{'name':name,'arguments':json.dumps(args)}}]}
            finish='tool_calls'
        elif type(self).generic_mode and turn<=9:
            ctx=type(self).scene_context
            records=[json.loads(p.read_text()) for p in (ctx.run/'index').glob('*.json')]
            def aid(kind): return next(r['artifact_id'] for r in records if r['kind']==kind)
            if turn==1: name,args='make_synthetic_capture',{}
            elif turn==2: name,args='segment_spectrum',dict(capture_id=aid('capture'))
            elif turn==3: name,args='analyze_iq_quality',dict(artifact_id=aid('capture'))
            elif turn==4: name,args='analyze_burst_timing',dict(artifact_id=aid('capture'))
            elif turn==5: name,args='analyze_sweep_structure',dict(artifact_id=aid('capture'))
            elif turn==6: name,args='summarize_spectral_activity',dict(segmentation_id=aid('segmentation'))
            elif turn==7: name,args='export_sigmf_recording',dict(artifact_id=aid('capture'),segmentation_id=aid('segmentation'))
            elif turn==8: name,args='assess_signal_scene',dict(segmentation_id=aid('segmentation'))
            else: name,args='build_report',{}
            delta={'role':'assistant','tool_calls':[{'index':0,'id':'generic_call_'+str(turn),'type':'function',
                'function':{'name':name,'arguments':json.dumps(args)}}]}
            finish='tool_calls'
        elif type(self).scene_mode and turn<=7:
            ctx=type(self).scene_context
            records=[json.loads(p.read_text()) for p in (ctx.run/'index').glob('*.json')]
            def aid(kind): return next(r['artifact_id'] for r in reversed(records) if r['kind']==kind)
            if turn==1: name,args='make_synthetic_capture',{}
            elif turn==2: name,args='segment_spectrum',dict(capture_id=aid('capture'))
            elif turn==3: name,args='assess_signal_scene',dict(segmentation_id=aid('segmentation'))
            elif turn==4:
                review=json.loads(Path(ctx.resolve(aid('scene_review'))['path']).read_text())
                name,args='refine_signal_region',dict(segmentation_id=aid('segmentation'),
                    group_id=review['review_targets'][0]['group_id'],reason='Review flags unresolved frequency structure; test adaptive local segmentation.',evidence_ids=[aid('scene_review')])
            elif turn==5:
                refinement=json.loads(Path(ctx.resolve(aid('scene_refinement'))['path']).read_text())
                name,args='profile_signal',dict(artifact_id=refinement['subband_id'])
            elif turn==6: name,args='assess_signal_scene',dict(segmentation_id=aid('segmentation'))
            else: name,args='build_report',{}
            delta={'role':'assistant','tool_calls':[{'index':0,'id':'scene_call_'+str(turn),'type':'function',
                'function':{'name':name,'arguments':json.dumps(args)}}]}
            finish='tool_calls'
        elif turn==1:
            delta={"role":"assistant","tool_calls":[{"index":0,"id":"call_prior","type":"function",
                "function":{"name":"query_band_priors","arguments":json.dumps({"center_hz":2400000000,"span_hz":100000000})}}]}
            finish="tool_calls"
        elif type(self).reserve_mode and turn==2:
            delta={'role':'assistant','tool_calls':[{'index':0,'id':'reserve_denied','type':'function',
                'function':{'name':'make_synthetic_capture','arguments':'{}'}}]}
            finish='tool_calls'
        elif turn==2 or (type(self).reserve_mode and turn==3):
            delta={"role":"assistant","tool_calls":[{"index":0,"id":"call_report","type":"function",
                "function":{"name":"build_report","arguments":"{}"}}]}
            finish="tool_calls"
        else:
            delta={"role":"assistant","content":"Local adapter fixture completed. No RF capture or protocol claim."}
            finish="stop"
        self.send_response(200);self.send_header("Content-Type","text/event-stream");self.end_headers()
        for value,reason in [(delta,None),({},finish)]:
            item=dict(id="local-test",object="chat.completion.chunk",created=int(time.time()),model="local-fixture",
                      choices=[dict(index=0,delta=value,finish_reason=reason)])
            self.wfile.write(("data: "+json.dumps(item)+"\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n");self.wfile.flush()


class LocalAdapterTest(unittest.TestCase):
    def test_actual_official_harness_local_route_and_tool_calls(self):
        self.run_fixture(False)

    def test_official_harness_scene_feedback_tools(self):
        self.run_fixture(True)

    def test_official_harness_generic_tools(self):
        self.run_fixture('generic')

    def test_official_harness_reserves_rounds_for_report(self):
        self.run_fixture('reserve')

    def test_official_harness_ieee154_decode_and_review(self):
        self.run_fixture('ieee154')

    def run_fixture(self,scene_mode):
        Fixture.calls=[]
        Fixture.scene_mode=scene_mode is True
        Fixture.generic_mode=scene_mode=='generic'
        Fixture.reserve_mode=scene_mode=='reserve'
        Fixture.ieee154_mode=scene_mode=='ieee154'
        server=http.server.ThreadingHTTPServer(("127.0.0.1",0),Fixture)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        ctx=Context("local_adapter_"+uuid.uuid4().hex[:10])
        Fixture.scene_context=ctx
        if scene_mode=='ieee154':
            from test_ieee154 import fixture
            samples,_=fixture()
            aid,path=ctx.artifact('fixture','cf32');samples.tofile(path)
            dump(path.with_suffix('.json'),dict(center_hz=2405e6,sample_rate_hz=4e6,requested_span_hz=3.2e6,
                sample_count=len(samples),duration_s=len(samples)/4e6,simulated=True,
                segments=[dict(file_start=0,count=len(samples),hardware_start_s=None)]))
            ctx.register(aid,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
        dump(ctx.run/"request.json",dict(budgets=dict(max_model_rounds=4 if scene_mode=='reserve' else 12),deadline_unix=time.time()+60))
        patch=ctx.run/"local-test.yml"
        patch.write_text(f"""
- id: llm-pi-ai
  config:
    providers:
      local-fixture:
        api: openai-completions
        apiKeyEnv: LOCAL_FIXTURE_API_KEY
        baseURL: http://127.0.0.1:{server.server_port}/v1
        models:
          - id: local-fixture
            name: Local test fixture
            contextWindow: 32768
        retryPolicy:
          mode: normal
          maxRetries: 0
- id: agent-default-model
  config:
    provider: local-fixture
    model: local-fixture
""")
        env=dict(os.environ,PATH=str(ROOT/"runtime/node/bin")+":"+os.environ["PATH"],
            DSH_HOME=str(ROOT/"dsh-home"),DSH_TELEMETRY_DISABLED="1",SPECTRUM_ROOT=str(ROOT),
            SPECTRUM_TASK_ID=ctx.task_id,LOCAL_FIXTURE_API_KEY="fixture-not-a-secret")
        try:
            p=subprocess.run([str(ROOT/"runtime/dsh/node_modules/.bin/dsh"),"--profile","headless",
                "--patch",str(ROOT/"configs/spectrum.patch.yml"),"--patch",str(patch),"--json",
                "Query band priors, then build report. No capture."],
                cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
            (ctx.run/"dsh.jsonl").write_text(p.stdout)
            (ctx.run/"dsh.stderr").write_text(p.stderr)
            self.assertEqual(p.returncode,0,p.stderr[-3000:])
            self.assertNotIn("did not activate",p.stderr)
            self.assertGreaterEqual(len(Fixture.calls),3,p.stderr)
            self.assertTrue((ctx.run/"report.json").exists())
            if scene_mode is True:
                report=json.loads((ctx.run/'report.json').read_text())
                self.assertEqual(len(report['scene_refinements']),1)
                self.assertEqual(len(report['scene_reviews']),2)
                self.assertTrue(any(len(r['refinements'])==1 for r in report['scene_reviews']))
            if scene_mode=='generic':
                report=json.loads((ctx.run/'report.json').read_text())
                self.assertEqual(len(report['generic_signal_analysis']),4)
                self.assertTrue(any(r['generic_evidence'] for r in report['scene_reviews']))
                self.assertTrue(any(json.loads(p.read_text()).get('kind')=='sigmf_export' for p in (ctx.run/'index').glob('*.json')))
            if scene_mode=='reserve':
                self.assertFalse(any(json.loads(p.read_text()).get('kind')=='capture' for p in (ctx.run/'index').glob('*.json')))
                self.assertIn('MODEL_ROUND_RESERVE',p.stdout)
            if scene_mode=='ieee154':
                report=json.loads((ctx.run/'report.json').read_text())
                self.assertTrue(any(v['protocol']=='IEEE802.15.4' and v['valid_frames']>0 for v in report['verified_protocols']))
                self.assertEqual(report['verified_protocols'][0]['upper_layer_protocol'],'unknown')
                self.assertTrue(report['scene_reviews'][0]['verification_attempts'])
            self.assertTrue(any(m.get("role")=="tool" for m in Fixture.calls[-1]["messages"]))
            allowed={t["function"]["name"] for t in Fixture.calls[0]["tools"]}
            self.assertIn("query_band_priors",allowed)
            self.assertIn("assess_signal_scene",allowed)
            self.assertIn("refine_signal_region",allowed)
            self.assertIn('analyze_iq_quality',allowed)
            self.assertIn('analyze_burst_timing',allowed)
            self.assertIn('analyze_sweep_structure',allowed)
            self.assertIn('summarize_spectral_activity',allowed)
            self.assertIn('export_sigmf_recording',allowed)
            self.assertNotIn("bash",allowed)
            self.assertNotIn("read_file",allowed)
        finally:
            server.shutdown();server.server_close()
