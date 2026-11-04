"""Summarize an existing diagnostic matrix; never acquires RF."""
import argparse,json
from pathlib import Path
import numpy as np
from scipy.signal import welch
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from spectrum_agent.common import ROOT,Context,dump

p=argparse.ArgumentParser();p.add_argument('batch_id');args=p.parse_args()
source=ROOT/'docs/testing'/(args.batch_id+'.json');data=json.loads(source.read_text())
fig,ax=plt.subplots(figsize=(11,5));rows=[]
for row in data['rows']:
    cap=row.get('capture',{})
    if not cap.get('complete'):continue
    ctx=Context(row['task_id']);rec=ctx.resolve(cap['capture_id'])
    iq=np.memmap(rec['path'],dtype='<c8',mode='r')
    # Welch on five 100k-sample blocks distributed over the full recording.
    starts=np.linspace(0,max(0,len(iq)-100000),5,dtype=int)
    spectra=[];peaks=[];component_peaks=[]
    for start in starts:
        block=np.asarray(iq[start:start+100000])
        f,power=welch(block,fs=cap['sample_rate_hz'],nperseg=8192,return_onesided=False)
        spectra.append(power);peaks.append(float(np.max(np.abs(block))))
        component_peaks.append(float(max(abs(block.real).max(),abs(block.imag).max())))
    psd=np.fft.fftshift(np.mean(spectra,axis=0));freq=np.fft.fftshift(f)+cap['center_hz']
    selected=abs(freq-2373.9e6)<=10e6
    def band_power(low,high):
        return float(np.mean(psd[(freq>=low)&(freq<high)]))
    contrast=10*float(np.log10(band_power(2372e6,2373e6)/max(band_power(2380e6,2382e6),1e-30)))
    ax.plot(freq[selected]/1e6,10*np.log10(np.maximum(psd[selected],1e-30)),label=row['case'],alpha=.8)
    windows=row.get('windows',[]);cells=[c for w in windows for c in w.get('result',{}).get('synchronization_candidates',[])]
    rows.append(dict(case=row['case'],task_id=row['task_id'],capture_path=rec['path'],
        sample_rate_hz=cap['sample_rate_hz'],center_hz=cap['center_hz'],gain_db=cap['gain_db'],
        capture_elapsed_s=row.get('capture_elapsed_s'),complete=cap['complete'],overflow_count=cap['overflow_count'],
        native_windows=len(windows),failed_windows=sum('error' in w for w in windows),
        relative_subband_power_median_db=float(np.median([w['relative_power_dbfs'] for w in windows if 'relative_power_dbfs' in w])),
        distributed_psd_2372_2373_vs_2380_2382_db=contrast,
        sampled_peak_component=max(component_peaks),sampled_peak_magnitude=max(peaks),
        quality_coverage='five distributed 100k-sample raw-IQ blocks, not exhaustive clipping inspection',
        valid_broadcast_blocks=row.get('valid_broadcast_blocks'),sync_hypotheses=len(cells),
        candidate_pcis=[c['cell_id'] for c in cells],matching_pci_counts=[c['matching_cell_detections'] for c in cells],
        decoder_elapsed_s=sum(w.get('elapsed_s',0) for w in windows)))
ax.set(xlabel='RF frequency (MHz)',ylabel='Relative PSD (dB/Hz; uncalibrated)',
       title='B40 diagnostic: sequential captures, distributed Welch samples')
ax.grid(alpha=.2);ax.legend(ncol=2);fig.tight_layout()
fig.savefig(source.with_name(args.batch_id+'-psd.png'),dpi=160)
dump(source.with_name(args.batch_id+'-summary.json'),dict(batch_id=args.batch_id,
    started_utc=data['started_utc'],finished_utc=data.get('finished_utc'),rows=rows,
    warnings=data['warnings'],total_broadcast_blocks=sum(r['valid_broadcast_blocks'] or 0 for r in rows)))
print(json.dumps(rows,indent=2))
