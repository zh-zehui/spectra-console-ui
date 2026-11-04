"""Full-segment energy components; fixed temporal background, separate scales."""
import numpy as np
from scipy import ndimage


def detect(db, threshold_db=10, frequency_resolution_hz=100000, time_step_s=5.12e-6):
    # Estimate from the entire continuous recording, never restart at FFT batch
    # boundaries. Capping the per-bin estimate retains stationary narrow energy.
    # Median background and temporal denoising reduce exponential-power speckle;
    # otherwise a low-percentile noise floor can promote thousands of noise peaks.
    temporal_floor=np.percentile(db,50,axis=0)
    floor=np.minimum(temporal_floor,np.percentile(temporal_floor,30)+6)
    temporal_width=max(3,int(round(12e-6/time_step_s))|1)
    smoothed=ndimage.median_filter(db,size=(temporal_width,1),mode='nearest')
    residual=smoothed-floor[None,:]
    broad=ndimage.gaussian_filter(residual,sigma=(max(1,5e-6/time_step_s),max(1,300000/frequency_resolution_hz)),mode='nearest')
    low=max(3,threshold_db-6)
    seeds=broad>=threshold_db
    mask=ndimage.binary_propagation(seeds,mask=broad>=low)
    # Remove narrow scales from this layer; preserve them independently below.
    broad_width=max(5,int(round(1.5e6/frequency_resolution_hz)))
    mask=ndimage.binary_opening(mask,structure=np.ones((1,broad_width)))
    mask=ndimage.binary_closing(mask,structure=np.ones((max(3,int(round(8e-6/time_step_s))|1),broad_width)))
    # Narrow peaks above local same-time broad background remain independent,
    # including peaks embedded inside a broad burst.
    local=smoothed-ndimage.median_filter(smoothed,size=(1,max(9,int(round(2e6/frequency_resolution_hz))|1)),mode='nearest')
    narrow_support=(local>=max(3,threshold_db-6))&(residual>=low)
    narrow=ndimage.binary_propagation((local>=max(6,threshold_db-2))&(residual>=threshold_db),mask=narrow_support)
    result=[]
    for layer,energy in [('broad',mask),('narrow',narrow)]:
        labels,_=ndimage.label(energy,structure=np.ones((3,3)))
        for i,box in enumerate(ndimage.find_objects(labels),1):
            if box is None:continue
            height=box[0].stop-box[0].start;width=box[1].stop-box[1].start
            if height<max(3,int(np.ceil((12e-6 if layer=='broad' else 35e-6)/time_step_s))) or (layer=='narrow' and width>max(3,int(np.ceil(3.2e6/frequency_resolution_hz)))):continue
            selected=labels[box]==i;area=int(selected.sum());occupancy=area/(height*width)
            if area<(30 if layer=='broad' else 24) or occupancy<.4:continue
            values=residual[box][selected]
            result.append(dict(box=dict(x1=box[1].start,x2=box[1].stop-1,y1=box[0].start,y2=box[0].stop-1),
                snr_db=float(values.max()),peak_db=float(db[box][selected].max()),
                occupancy=float(occupancy),label=layer+'_energy_component'))
    return result
