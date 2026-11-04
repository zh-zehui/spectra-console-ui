"""Keep acquisition LO, digital span, nominal frontend span and task scope separate."""
import json
from .common import DomainError,finite_number


def frontend_bandwidth(meta):
    identity=str(meta.get('device_info',{}).get('rx_id','')).upper()
    if 'UBX-160' in identity:
        # UBX160 fixed nominal frontend; a legacy writable UHD property is not
        # evidence of hardware filter tuning or a calibrated flat passband.
        return 160e6 if meta['center_hz']>=500e6 else 84e6,'hardware_model_nominal'
    value=meta.get('frontend_nominal_bandwidth_hz',meta.get('analog_bandwidth_hz'))
    return (float(value),'recorded_nominal') if value else (None,'unknown')


def describe(meta,constraints=None):
    fc=meta['center_hz'];fs=meta['sample_rate_hz'];bw,source=frontend_bandwidth(meta)
    dlo,dhi=fc-fs/2,fc+fs/2
    alo,ahi=(fc-bw/2,fc+bw/2) if bw else (None,None)
    previous=meta.get('frequency_scope',{})
    c=constraints or {};center=c.get('center_hz',meta.get('analysis_center_hz',previous.get('analysis_center_hz',fc)))
    span=c.get('span_hz',meta.get('analysis_span_hz',previous.get('analysis_span_hz',meta.get('requested_span_hz',fs*.8))))
    # External task normalization enforces RF limits. Internal geometry/baseband
    # fixtures may use small frequencies, and still need truthful range reports.
    finite_number(center,'analysis center_hz',-6e9,6e9);finite_number(span,'analysis span_hz',1e-9,160e6)
    low,high=center-span/2,center+span/2
    valid_low=max(dlo,alo) if bw else dlo;valid_high=min(dhi,ahi) if bw else dhi
    clamped=False
    if not {'center_hz','span_hz'}&c.keys() and (low<valid_low-1 or high>valid_high+1):
        span=min(span,valid_high-valid_low);center=max(valid_low+span/2,min(center,valid_high-span/2))
        low,high=center-span/2,center+span/2;clamped=True
    if low<valid_low-1 or high>valid_high+1:
        raise DomainError('ANALYSIS_OUT_OF_BAND',f'Analysis [{low}, {high}] exceeds saved recording usable nominal range [{valid_low}, {valid_high}] Hz')
    return dict(acquisition_center_hz=fc,sample_rate_hz=fs,digital_low_hz=dlo,digital_high_hz=dhi,
        frontend_nominal_bandwidth_hz=bw,frontend_low_hz=alo,frontend_high_hz=ahi,frontend_source=source,
        frontend_passband_calibrated=False,usable_low_hz=valid_low,usable_high_hz=valid_high,
        legacy_default_scope_clamped_to_saved_band=clamped,
        analysis_center_hz=center,analysis_span_hz=span,analysis_low_hz=low,analysis_high_hz=high,
        warning='Frontend bounds are nominal, not calibrated flatness or brick-wall cutoff; digital guard bands are not guaranteed usable RF bandwidth.')


def effective_metadata(ctx,meta):
    path=ctx.run/'request.json'
    request=json.loads(path.read_text()) if path.exists() else {}
    c=request.get('constraints',{})
    if meta.get('simulated') and request.get('source')=='synthetic' and not request.get('recording_capture_id') and c.get('span_hz',0)>meta['sample_rate_hz']:
        # Legacy small synthetic fixtures explicitly advertise their reduced
        # span in dsh_prompt; do not pretend they cover the requested wide RF.
        c={}
    return dict(meta,frequency_scope=describe(meta,c))


def bounds(meta):
    s=meta.get('frequency_scope')
    if s:return s['analysis_low_hz'],s['analysis_high_hz']
    width=meta.get('requested_span_hz',meta['sample_rate_hz']*.8)
    return meta['center_hz']-width/2,meta['center_hz']+width/2
