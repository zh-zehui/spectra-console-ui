"""Recorded-IQ review sheet. Human burst-boundary labels remain explicitly pending."""
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from spectrum_agent.common import ROOT,Context
from spectrum_agent.scene import read
from spectrum_agent.catalog import associate
from spectrum_agent.common import dump

summary=json.loads((ROOT/'docs/testing/local-benchmark-v12.json').read_text());ctx=Context(summary['task_id'])
verified=[]
for p in (ctx.run/'index').glob('*.json'):
    rec=json.loads(p.read_text())
    if rec['kind'] in ('wifi_verification','ble_verification','ieee154_verification'):
        verified.append(dict(read(ctx,rec['artifact_id']),evidence_id=rec['artifact_id']))
links=associate(ctx,[],verified);review=[]
fig,axes=plt.subplots(len(summary['cases']),3,figsize=(18,12),constrained_layout=True)
for row,c in enumerate(summary['cases']):
    for col,candidate in enumerate(c['comparisons']):
        ax=axes[row,col];r=read(ctx,candidate['refinement_id']);rec=ctx.resolve(r['subband_id']);m=json.loads(Path(rec['metadata_path']).read_text())
        parent=json.loads(Path(ctx.resolve(r['capture_id'])['metadata_path']).read_text());fs=parent['sample_rate_hz'];rate=m['sample_rate_hz']
        a,b=r['requested_parent_interval'];lo,hi=r['fine_target_frequency_hz']
        references=[l for l in links if l['capture_id']==r['capture_id'] and l['association_level']=='packet_time_frequency_overlap'
            and a<=l['sample_start'] and l['sample_stop']<=b and m['center_hz']-m['requested_span_hz']/2<=l['freq_low_hz'] and l['freq_high_hz']<=m['center_hz']+m['requested_span_hz']/2]
        if col==0:review.append(dict(case=c['case'],capture_id=r['capture_id'],target_samples=[a,b],target_frequency_hz=[lo,hi],
            known_packet_estimates=references,human_burst_labels=[],human_label_status='pending',
            note='Decoded packet estimates and decoder passbands are partial references, not whole burst boundaries or measured occupied bandwidth.'))
        iq=np.memmap(rec['path'],dtype=np.complex64,mode='r');nfft=512;hop=256
        offsets=np.arange(m.get('valid_output_start',0),len(iq)-nfft+1,hop)
        times=m['parent_sample_start']/fs+(offsets+nfft/2)/rate-m.get('filter_group_delay_s',0)
        keep=(times>=a/fs)&(times<=b/fs);offsets=offsets[keep];times=times[keep]
        if not len(offsets):continue
        freq=m['center_hz']+np.fft.fftshift(np.fft.fftfreq(nfft,1/rate));valid=(freq>=lo)&(freq<=hi)
        window=np.hanning(nfft);data=np.array([iq[int(o):int(o)+nfft]*window for o in offsets])
        power=abs(np.fft.fftshift(np.fft.fft(data,axis=1),axes=1))**2/window.dot(window)
        db=10*np.log10(np.maximum(power[:,valid],1e-20));vmin,vmax=np.percentile(db,[10,99.5])
        ax.imshow(db,origin='lower',aspect='auto',extent=[freq[valid][0]/1e6,freq[valid][-1]/1e6,times[0]*1000,times[-1]*1000],cmap='viridis',vmin=vmin,vmax=vmax)
        for e in r['mapped_events']:
            ax.add_patch(plt.Rectangle((e['freq_low_hz']/1e6,e['sample_start']/fs*1000),
                (e['freq_high_hz']-e['freq_low_hz'])/1e6,(e['sample_stop']-e['sample_start'])/fs*1000,
                fill=False,edgecolor='#ffa726',linewidth=.5,alpha=.75))
        for l in references:
            ax.add_patch(plt.Rectangle((l['freq_low_hz']/1e6,l['sample_start']/fs*1000),
                (l['freq_high_hz']-l['freq_low_hz'])/1e6,(l['sample_stop']-l['sample_start'])/fs*1000,
                fill=False,edgecolor='cyan',linestyle='--',linewidth=1))
        ax.set_xlim(lo/1e6,hi/1e6);ax.set_ylim(a/fs*1000,b/fs*1000)
        ax.set_title(f"{c['case']['name']} | {candidate['strategy']}\n{len(r['mapped_events'])} target candidates; refs {candidate['quality']['known_packet_reference_count']}, unmatched {candidate['quality']['known_packet_unmatched_count']}",fontsize=9)
        ax.set_xlabel('MHz');ax.set_ylabel('Recorded IQ time (ms)')
fig.suptitle('Recorded IQ | orange: candidates | cyan: packet estimates / decoder passbands | HUMAN BURST LABELS: PENDING',fontsize=13)
fig.savefig(ROOT/'docs/testing/local-review-v12.png',dpi=140);plt.close(fig)
print('Saved docs/testing/local-review-v12.png')
dump(ROOT/'docs/testing/local-human-review-v12.json',dict(source_task=summary['source_task'],benchmark_task=ctx.task_id,cases=review,human_label_status='pending'))
