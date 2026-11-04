"""Same target scopes, different local detectors. Human reference labels stay pending."""
import json,time,uuid
from pathlib import Path
from spectrum_agent.common import Context,ROOT,dump
from spectrum_agent import scene
from spectrum_agent.catalog import associate

source=Context('task_b73949a5c41c428198c00908b18c8d35');ctx=Context('local_v12_'+uuid.uuid4().hex[:10])
previous=json.loads((source.run/'report.json').read_text());base=previous['initial_segmentation_id'];cap=previous['captures'][0]['artifact_id']
for p in (source.run/'index').glob('*.json'):
    rec=json.loads(p.read_text())
    if rec['kind'] not in ('capture','subband','wifi_verification','ble_verification','ieee154_verification','segmentation','scene_refinement','local_segmentation'):continue
    rec=dict(rec,task_id=ctx.task_id)
    for k in ('path','metadata_path'):
        if k not in rec:continue
        original=Path(rec[k]);target=ROOT/'artifacts'/ctx.task_id/original.name
        target.parent.mkdir(parents=True,exist_ok=True);target.hardlink_to(original);rec[k]=str(target)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
seg=scene.read(ctx,base);groups={g['group_id']:g for g in seg['signal_groups']};fs=previous['captures'][0]['metadata']['sample_rate_hz']
links=associate(ctx,[dict(seg,evidence_id=base)],previous['verification_attempts'])
ble=next(l for l in links if l['protocol']=='Bluetooth LE' and l['association_level']=='packet_time_frequency_overlap' and l['candidate_groups'])
ble_groups=[groups[m['group_id']] for m in ble['candidate_groups'] if groups[m['group_id']]['sample_start']<=ble['sample_start'] and groups[m['group_id']]['sample_stop']>=ble['sample_stop']]
bg=min(ble_groups,key=lambda g:g['bandwidth_hz'])
wifi=next(l for l in links if l['protocol']=='IEEE802.11' and l['association_level']=='packet_time_frequency_overlap' and l['sample_stop']-l['sample_start']<=7000 and l['candidate_groups'])
wg=max((groups[m['group_id']] for m in wifi['candidate_groups']),key=lambda g:g['bandwidth_hz'])
def case(name,gid,center,width,rate,offset=0,duration=.004):
    return dict(name=name,group_id=gid,center_hz=center,bandwidth_hz=width,output_rate_hz=rate,offset_s=offset,duration_s=duration)
cases=[case('wide_parent_focused_wifi','g79',2412e6,20e6,25e6),
       case('narrow_ble_packet',bg['group_id'],(ble['freq_low_hz']+ble['freq_high_hz'])/2,2e6,4e6,duration=.002),
       case('short_wifi_reference',wg['group_id'],(wifi['freq_low_hz']+wifi['freq_high_hz'])/2,20e6,25e6,
            offset=max(0,(wifi['sample_start']-wg['sample_start'])/fs-.00005),duration=.0002),
       case('weak_unknown','g126',(groups['g126']['freq_low_hz']+groups['g126']['freq_high_hz'])/2,20e6,25e6,duration=.001)]
review=scene.assess(ctx,base)['review_id'];results=[]
print(json.dumps(dict(task_id=ctx.task_id,cases=cases)),flush=True)
for c in cases:
    rows=[]
    for strategy,fft,threshold,tile in [('adaptive_hysteresis',2048,12,20e-6),('multiscale_energy',1024,10,20e-6),('local_power_tiles',512,10,10e-6)]:
        t=time.perf_counter()
        with ctx.span('local_candidate_benchmark'):
            out=scene.refine(ctx,base,c['group_id'],'Same target scope comparison, retain all raw/local evidence; not an accuracy claim.',[review],
                fft_size=fft,threshold_db=threshold,strategy=strategy,margin_s=.001,
                local_time_bin_s=tile,local_frequency_bin_hz=100e3,
                **{k:v for k,v in c.items() if k not in ('name','group_id')})
        r=scene.read(ctx,out['refinement_id']);rows.append(dict(strategy=strategy,fft_size=fft,threshold_db=threshold,
            elapsed_s=time.perf_counter()-t,refinement_id=out['refinement_id'],target_events=len(r['mapped_events']),
            context_events=r['context_event_count'],count_scope=r['count_scope'],target_time_samples=r['requested_parent_interval'],
            target_frequency_hz=r['fine_target_frequency_hz'],quality=r['quality_comparison'],local_scale=r['local_scale']))
        print(json.dumps(dict(case=c['name'],**rows[-1])),flush=True)
    results.append(dict(case=c,comparisons=rows,human_reference=dict(status='pending',burst_regions=[],notes='Known decoded packet estimates are partial references, not full burst boundary truth.')))
summary=dict(task_id=ctx.task_id,source_task=source.task_id,new_rf_capture=False,model_used=False,cases=results,
    human_reference_status='pending',accuracy_improvement='not_measured')
dump(ROOT/'docs/testing/local-benchmark-v12.json',summary)
dump(ctx.run/'review_dataset.json',summary)
print(json.dumps(dict(done=True,task_id=ctx.task_id)),flush=True)
