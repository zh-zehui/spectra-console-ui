"""Replay identical windows through the pre-change wrapper and current wrapper."""
import importlib.util,json,tarfile,time,sys
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.dsp import load_iq
from spectrum_agent.wifi_modern import receive_iq
tasks=['task_window_wide_4065cef0c157','task_window_wide_4ab0e38da849','task_window_wide_9fb2183ea875']
baseline=ROOT/'runtime/baseline-wifi-modern.py'
with tarfile.open(ROOT/'docs/archive/before-window-wide-20261028.tar.gz') as archive:
    baseline.write_bytes(archive.extractfile('src/spectrum_agent/wifi_modern.py').read())
spec=importlib.util.spec_from_file_location('spectrum_agent.wifi_modern_before',baseline)
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
results=[]
for task in tasks:
    ctx=Context(task);start=time.perf_counter();checked=0;mac_before=mac_after=0
    cap=next(json.loads(p.read_text()) for p in (ctx.run/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='capture')
    meta=json.loads(Path(cap['metadata_path']).read_text());fs=meta['sample_rate_hz']
    for p in (ctx.run/'index').glob('*.json'):
        row=json.loads(p.read_text())
        if row.get('kind')!='subband':continue
        x,m=load_iq(ctx,row['artifact_id']);rate=m['sample_rate_hz']
        for core in range(m['requested_sample_start'],m['requested_sample_stop'],round(.01*fs)):
            end=min(m['requested_sample_stop'],core+round(.01*fs))
            lo=max(m.get('valid_output_start',0),int((core-m['parent_sample_start'])/fs*rate)-round(.002*rate))
            hi=min(len(x),int((end-m['parent_sample_start'])/fs*rate)+round(.002*rate))
            a=old.receive_iq(x[lo:hi]);b=receive_iq(x[lo:hi])
            def identities(frames):return sorted((f['frame_hex'],f.get('subband_sample_start',-1),f.get('subband_sample_stop',-1)) for f in frames)
            assert identities(a)==identities(b),(task,row['artifact_id'],core)
            mac_before+=len(a);mac_after+=len(b);checked+=1
    result=dict(task_id=task,compared_windows=checked,raw_mac_observations_before=mac_before,raw_mac_observations_after=mac_after,
        exact_bytes_and_timing_equal=True,elapsed_s=time.perf_counter()-start,
        note='Overlapping context windows include duplicate observations; not unique packet counts. Original pre-change Python wrapper compared with current wrapper; native20MHz payload support guards unchanged.')
    results.append(result);print(json.dumps(result),flush=True)
    dump(ROOT/'docs/testing/wide-mac-regression.json',results)
dump(ROOT/'docs/testing/wide-mac-regression.json',results)
