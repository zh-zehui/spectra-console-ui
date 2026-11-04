"""Offline acceptance on three immutable real captures, no acquisition tools."""
import json,time,uuid,sys
from pathlib import Path
from collections import Counter
import numpy as np
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize
from spectrum_agent.task_intent import resolve
from spectrum_agent.window_analysis import analyze
from spectrum_agent.dsp import extract,load_iq
from spectrum_agent.wifi_modern import receive_iq
from spectrum_agent.worker import report
from spectrum_agent.ofdm_structure import analyze as structure

sources=['task_4d7af074d5bd406e9d9dd781f242cd17','task_962288f3229a48468017cdd4dca54aab','task_e7bb08a92f9440da91c043ab85f5b1b6']
if len(sys.argv)>1:sources=[sources[int(sys.argv[1])]]
results=[]
for source in sources:
    ctx=Context('task_window_wide_'+uuid.uuid4().hex[:12]);start=time.perf_counter()
    prompt=source+'，使用已有IQ，按10ms分窗分析整个记录的5.8GHz信号，保留报头和MAC校验的区别；不重新采集。'
    dump(ctx.run/'request.json',normalize(dict(prompt=prompt,source='usrp',budgets=dict(coverage_mode='complete',deadline_s=3600))))
    resolve(ctx,evidence_quotes=[source],reason='Offline acceptance on saved recording;10ms main windows and independent SIG/MAC evidence.',
        source_task_id=source,analysis_requested=True,acquisition_requested=False,analysis_window_s=.01)
    req=json.loads((ctx.run/'request.json').read_text());cap=req['recording_capture_id'];iq,m=load_iq(ctx,cap);fs=m['sample_rate_hz']
    while True:
        with ctx.span('analyze_spectrum_windows'):progress=analyze(ctx,cap,max_windows=8)
        print(json.dumps(dict(source=source,stage='window_detection',**progress)),flush=True)
        if progress['complete']:break
    # Decode all recorded time using100ms extraction batches and10ms main windows.
    # This diagnostic fixes the comparison grid,not the model's future tool choice.
    for a in range(0,len(iq),round(.1*fs)):
        b=min(len(iq),a+round(.1*fs))
        for center in (5745e6,5765e6,5785e6,5805e6,5825e6,5845e6,5865e6):
            with ctx.span('extract_subband'):
                sub=extract(ctx,cap,center,18e6,a,b,20e6,.002)
            x,sm=load_iq(ctx,sub['artifact_id']);rate=sm['sample_rate_hz'];frames=[];headers=[];counts=Counter()
            for core in range(a,b,round(.01*fs)):
                core_end=min(b,core+round(.01*fs))
                lo=max(sm.get('valid_output_start',0),int((core-sm['parent_sample_start'])/fs*rate)-round(.002*rate))
                hi=min(len(x),int((core_end-sm['parent_sample_start'])/fs*rate)+round(.002*rate))
                with ctx.span('verify_wifi_window'):
                    found,stages=receive_iq(np.asarray(x[lo:hi]),True)
                counts.update(stages['counts'])
                for key,values in (('frames',found),('headers',stages['headers'])):
                    for event in values:
                        if 'subband_sample_start' not in event:continue
                        e=dict(event);e['subband_sample_start']+=lo;e['subband_sample_stop']+=lo;e['center_hz']=center
                        position=sm['parent_sample_start']+(e['subband_sample_start']/rate-sm.get('filter_group_delay_s',0))*fs
                        if not core<=position<core_end:continue
                        (frames if key=='frames' else headers).append(e)
            value=dict(artifact_id=sub['artifact_id'],protocol='IEEE802.11',verification_profile='wifi_gr20_siso',
                profile='20MHz data plus CRC-checked HT/VHT wide header observation',valid_frames=len(frames),frames=frames,
                verification_stages=dict(counts=dict(counts),headers=headers),simulated=False,
                evidence_level='decoded' if frames else 'phy_header_pass' if headers else 'unknown',
                warnings=['VHT80 SIG recognition only; full80MHz data decoding unavailable. Header observation centers are not full channel centers.'])
            ctx.json_artifact('wifi_verification',value)
            # Structural analysis at an evidence-selected observation with headers,once per100ms batch.
            if center==5805e6:
                with ctx.span('analyze_ofdm_structure'):structure(ctx,sub['artifact_id'],sample_stop=min(len(x),200000))
            print(json.dumps(dict(source=source,batch_start_ms=a/fs*1000,center_mhz=center/1e6,frames=len(frames),headers=len(headers))),flush=True)
    with ctx.span('build_report'):r=report(ctx,stop_reason='tool_capability_limit',stop_detail='Complete10ms energy-window survey and fixed20MHz Wi-Fi comparison grid; wide HT/VHT header recognition is separate from unsupported wideband MAC decoding. Other protocols and unresolved regions are not exhaustively verified.')
    r=json.loads((ctx.run/'report.json').read_text())
    elapsed=time.perf_counter()-start
    dump(ctx.run/'outcome.json',dict(task_id=ctx.task_id,status='partial',elapsed_ms=elapsed*1000,completed_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        note='Deterministic offline regression,not a model-authored exhaustive all-protocol analysis'))
    counts=Counter((h['phy_format'],h['channel_width_hz']/1e6) for h in r['wifi_header_evidence'])
    result=dict(task_id=ctx.task_id,source_task_id=source,new_rf_capture=False,duration_s=m['duration_s'],window_s=.01,
        total_windows=progress['total_windows'],coarse_regions=len(r['signal_groups']),valid_mac_frames=len(r['frame_associations']),
        header_observations=len(r['wifi_header_evidence']),header_formats={str(k):v for k,v in counts.items()},elapsed_s=elapsed,
        note='Header counts include independent20MHz observations of the same wideband PPDU; not unique packets or devices.')
    dump(ROOT/'docs/testing'/('window-wide-'+source+'.json'),result);results.append(result)
    print('RESULT '+json.dumps(result),flush=True)
dump(ROOT/'docs/testing/window-wide-acceptance.json',results)
