"""Official dsh real-provider semantic check. Only an output sink is registered."""
import json,os,subprocess,time,uuid,sys
from pathlib import Path
from spectrum_agent.common import ROOT,dump
from spectrum_agent.service import credentials

base=ROOT/'docs/testing'/('three-prompt-intents-'+uuid.uuid4().hex[:8]);base.mkdir(parents=True)
plugin=base/'intent-check.mjs'
plugin.write_text('''import {writeFileSync} from 'node:fs';
import {defineTool} from '/home/sribd/spectrum_agent/runtime/dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js';
export const name='intent-audit';export const inject=['tools'];
export function apply(ctx){
 const number=description=>({type:'number',description});const text=description=>({type:'string',description});
 ctx.tools.register(defineTool({name:'record_intent',description:'Record understanding only. This tool cannot execute a task,read IQ,inspect hardware or acquire signals. Omit unknown numeric fields instead of inventing them.',
 parameters:{task_description:{type:'string',required:true},acquisition_requested:{type:'boolean',required:true},analysis_requested:{type:'boolean',required:true},
 visualization_requested:{type:'boolean',description:'Whether user requested a plot; separate from segmentation/protocol analysis'},plot_description:text('Requested plot,if any'),
 source_task_id:text('Exact referenced task ID,if any'),center_hz:number('Explicit center frequency Hz'),sample_rate_hz:number('Explicit sampling rate Hz'),
 capture_duration_s:number('Explicit capture duration seconds'),analysis_bandwidth_hz:number('Explicit analysis bandwidth Hz'),max_analysis_s:number('Explicit ANALYSIS deadline seconds,not capture duration'),
 notes:{type:'string',required:true,description:'Explain missing information and task reference; no operation is executed'}},
 output:{schema:{type:'object',additionalProperties:true},render:(_a,v)=>[{type:'text',text:JSON.stringify(v)}]},
 async execute(args){writeFileSync(process.env.INTENT_AUDIT_OUTPUT,JSON.stringify(args));return {recorded:true,executed:false,interpretation:args}}}));
 ctx.tools.guard(call=>{const n=call.name??call.toolName??call.tool?.name;if(n&&n!=='record_intent')return 'Interpretation-only: no execution tools are available';});
}
''',encoding='utf-8')
patch=base/'check.patch.yml';patch.write_text('''- id: spectrum-tools
  disabled: true
- id: system-prompt
  config:
    personaPrefix: >-
      Interpret the original user prompt only. Do not execute any task or access IQ or hardware.
      Call record_intent exactly once,then give a brief Chinese explanation.
      Distinguish capture time from analysis time limit. Omit unknown numeric fields.
      A task reference is an existing IQ source,not authorization to acquire again.
- insert:
    - id: intent-audit
      name: '''+str(plugin)+'\n',encoding='utf-8')
prompts=[
 '仅采集数据，中心频率5.8GHz、采样率200M，采集时长500ms',
 '采集数据，中心频率5.8GHz、采样率200M，采集时长500ms，并分析带宽160M内的信号。',
 '分析task_1795880356654fc9b90fb05c49520684采集的IQ数据，主要分析160MHz带宽内的信号。分析时长上限30分钟']
spectrum_only='--spectrum-only' in sys.argv
if spectrum_only:prompts=['仅采集数据，中心频率5.8GHz、采样率200M，采集时长500ms，不分析，直接画频谱']
env=dict(os.environ,PATH=str(ROOT/'runtime/node/bin')+':'+os.environ['PATH'],DSH_HOME=str(ROOT/'dsh-home'),DSH_TELEMETRY_DISABLED='1',NODE_EXTRA_CA_CERTS='/etc/ssl/certs/ca-certificates.crt')
env['DEEPSEEK_API_KEY']=credentials()['deepseek_api_key']
results=[]
for i,prompt in enumerate(prompts,1):
 output=base/f'intent-{i}.json';env['INTENT_AUDIT_OUTPUT']=str(output)
 start=time.perf_counter()
 with (base/f'dsh-{i}.jsonl').open('w') as out,(base/f'dsh-{i}.stderr').open('w') as err:
  proc=subprocess.run([str(ROOT/'runtime/dsh/node_modules/.bin/dsh'),'--profile','headless','--patch',str(ROOT/'configs/spectrum.patch.yml'),'--patch',str(patch),'--json','-'],
      input=prompt,text=True,stdout=out,stderr=err,cwd=ROOT,env=env,timeout=90)
 if not output.exists():raise RuntimeError('Intent output missing; inspect '+str(base/f'dsh-{i}.stderr'))
 value=json.loads(output.read_text());results.append(dict(prompt=prompt,interpretation=value,elapsed_s=time.perf_counter()-start,exit_code=proc.returncode))
 print(json.dumps(dict(case=i,**results[-1]),ensure_ascii=False),flush=True)
result=dict(official_harness=True,provider='deepseek',registered_tools=['record_intent'],new_rf_capture=False,iq_read=False,iq_analysis=False,checks=results)
dump(ROOT/'docs/testing'/('spectrum-only-intent.json' if spectrum_only else 'three-prompt-intents.json'),result)
if spectrum_only:
 value=results[0]['interpretation']
 assert value['acquisition_requested'] and value['visualization_requested']
 assert value['center_hz']==5800e6 and value['sample_rate_hz']==200e6 and value['capture_duration_s']==.5
 sys.exit(0)
assert results[0]['interpretation']['acquisition_requested'] and not results[0]['interpretation']['analysis_requested']
assert results[1]['interpretation']['acquisition_requested'] and results[1]['interpretation']['analysis_requested']
assert not results[2]['interpretation']['acquisition_requested'] and results[2]['interpretation']['analysis_requested']
assert results[2]['interpretation']['source_task_id']=='task_1795880356654fc9b90fb05c49520684'
assert results[2]['interpretation']['max_analysis_s']==1800
assert all(results[i]['interpretation']['analysis_bandwidth_hz']==160e6 for i in (1,2))
assert all(results[i]['interpretation']['center_hz']==5800e6 and results[i]['interpretation']['sample_rate_hz']==200e6 and results[i]['interpretation']['capture_duration_s']==.5 for i in (0,1))
