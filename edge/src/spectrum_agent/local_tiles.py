"""Whole-local-window linear-power tiles; short hints retained, no top-K pruning."""
import math
import numpy as np
from scipy import ndimage
from .coarse import tile_mask
from .common import finite_number


def scale(df,dt,time_bin_s,frequency_bin_hz):
    finite_number(time_bin_s,'local_time_bin_s',1e-6,.001)
    finite_number(frequency_bin_hz,'local_frequency_bin_hz',1e3,2e6)
    return max(1,int(math.ceil(time_bin_s/dt))),max(1,int(math.ceil(frequency_bin_hz/df)))


def detect(db,threshold_db,frequency_resolution_hz,time_step_s,time_bin_s=20e-6,frequency_bin_hz=200e3):
    rt,rf=scale(frequency_resolution_hz,time_step_s,time_bin_s,frequency_bin_hz)
    nt,nf=db.shape;ts=np.arange(0,nt,rt);fs=np.arange(0,nf,rf)
    # Mean LINEAR power, not averaged dB. Peak branch uses a separate background.
    power=np.power(10.,np.clip(db,-200,200)/10.)
    counts_t=np.minimum(rt,nt-ts);counts_f=np.minimum(rf,nf-fs)
    mean=np.add.reduceat(np.add.reduceat(power,ts,axis=0),fs,axis=1)/counts_t[:,None]/counts_f[None,:]
    peak=np.maximum.reduceat(np.maximum.reduceat(power,ts,axis=0),fs,axis=1)
    regions,grid=tile_mask(mean,peak,threshold_db)
    # A narrow ridge inside broad activity must remain inspectable separately.
    # This is a structural hypothesis; modulation inside one signal can also create ridges.
    contrast=grid['peak_db']-ndimage.median_filter(grid['peak_db'],size=(1,max(5,int(math.ceil(2e6/(rf*frequency_resolution_hz)))|1)),mode='nearest')
    narrow=ndimage.binary_propagation(contrast>=threshold_db,mask=contrast>=max(3,threshold_db-3))
    labels,_=ndimage.label(narrow,structure=np.ones((3,3)));extra=[]
    for i,box in enumerate(ndimage.find_objects(labels),1):
        if box is None:continue
        t0,t1=box[0].start,box[0].stop;f0,f1=box[1].start,box[1].stop
        if not any(r['f0']<=f0 and f1<=r['f1'] and (r['f1']-r['f0'])>=3*(f1-f0)
                   and min(t1,r['t1'])>max(t0,r['t0']) for r in regions):continue
        selected=labels[box]==i
        extra.append(dict(t0=t0,t1=t1,f0=f0,f1=f1,snr_db=float(contrast[box][selected].max()),
            peak_db=float(grid['peak_db'][box][selected].max()),occupancy=float(selected.mean()),layer='embedded_narrow_hint'))
    regions.extend(extra)
    return [dict(box=dict(x1=r['f0']*rf,x2=min(nf,r['f1']*rf)-1,
                         y1=r['t0']*rt,y2=min(nt,r['t1']*rt)-1),
                 snr_db=r['snr_db'],peak_db=r['peak_db'],occupancy=r['occupancy'],label=r.get('layer','local_power_tile_component')) for r in regions]
