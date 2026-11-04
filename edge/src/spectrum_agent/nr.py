"""FR1 NR SSB/PBCH/MIB file receiver, independent of the LTE decoder."""
import hashlib
import json
import subprocess
import uuid
from .common import ROOT,DomainError
from .lte import COMMIT

RASTER_SOURCE='https://www.etsi.org/deliver/etsi_ts/138100_138199/138104/19.02.00_60/ts_138104v190200p.pdf'


def settings(scs=30,pattern=None,duplex_mode='auto'):
    if scs not in (15,30) or isinstance(scs,bool):raise DomainError('UNSUPPORTED_PROFILE','NR SSB supports15kHz Case A and30kHz Cases B/C only')
    pattern=pattern or ('A' if scs==15 else 'C')
    if (scs==15 and pattern!='A') or (scs==30 and pattern not in ('B','C')):
        raise DomainError('UNSUPPORTED_PROFILE','Use15kHz/A or30kHz/B,C')
    assumed='tdd' if pattern=='C' else 'fdd'
    if duplex_mode not in ('auto',assumed):raise DomainError('UNSUPPORTED_PROFILE','Validated NR profiles: A/B with FDD, C with TDD; this is an assumed search profile, not duplex inference')
    return dict(scs_khz=scs,pattern=pattern,duplex_mode=assumed,rate_hz=7680000 if scs==15 else 15360000,
                passband_hz=4400000 if scs==15 else 8800000)


def gscn_frequency(gscn):
    # TS38.104 global synchronization raster below3GHz: N*1.2MHz + M*50kHz.
    for m in (1,3,5):
        numerator=gscn-(m-3)//2
        if numerator%3==0:return (numerator//3)*1200000+m*50000
    raise DomainError('INVALID_ARGUMENT','Invalid sub3GHz GSCN')


def n40_raster(low_hz,high_hz):
    centers=[dict(gscn=g,center_hz=gscn_frequency(g)) for g in range(5762,5990)]
    return [c for c in centers if low_hz<=c['center_hz']-4400000 and c['center_hz']+4400000<=high_hz]


def verify(ctx,artifact_id,ssb_scs_khz=30,ssb_pattern=None,duplex_mode='auto'):
    from .dsp import load_iq
    profile=settings(ssb_scs_khz,ssb_pattern,duplex_mode)
    record=ctx.resolve(artifact_id)
    if record['kind']!='subband':raise DomainError('INVALID_ARTIFACT','Extract a focused NR SSB subband, not the whole wideband capture')
    iq,meta=load_iq(ctx,artifact_id);rate=profile['rate_hz']
    if abs(meta['sample_rate_hz']-rate)>1:
        raise DomainError('UNSUPPORTED_RATE',f"For NR{ssb_scs_khz}kHz extract bandwidth_hz={profile['passband_hz']} with output_rate_hz={rate}")
    minimum=4300000 if ssb_scs_khz==15 else 8600000
    if not minimum<=meta['requested_span_hz']<=profile['passband_hz']*1.3:
        raise DomainError('INVALID_ARGUMENT','NR SSB needs all240 subcarriers plus filter margin; use the documented passband')
    if not rate*.02<=len(iq)<=rate*.5:
        raise DomainError('RESOURCE_LIMIT','NR tool requires20..500ms continuous IQ; use the whole100ms recording when available')
    if len(meta['segments'])!=1 or meta['segments'][0]['count']!=len(iq):raise DomainError('CAPTURE_GAP','Do not cross recording gaps')
    binary=ROOT/'runtime/lte/bin/spectrum_nr_rx'
    if not binary.exists():raise DomainError('DEPENDENCY_MISSING','Build project-local NR SSB receiver')
    ctx.check()
    try:
        process=subprocess.run([str(binary),record['path'],str(ssb_scs_khz),profile['pattern'],str(meta['center_hz'])],
            cwd=ctx.run,capture_output=True,text=True,timeout=90)
    except subprocess.TimeoutExpired as exc:raise DomainError('DECODER_TIMEOUT','NR decoder exceeded90s; keep unknown') from exc
    (ctx.run/('nr-native-'+uuid.uuid4().hex[:12]+'.log')).write_text(process.stderr,encoding='utf-8')
    if process.returncode:raise DomainError('DECODER_ERROR','NR file receiver failed; inspect task-local log')
    try:native=json.loads(process.stdout)
    except json.JSONDecodeError as exc:raise DomainError('DECODER_ERROR','Invalid NR native result') from exc
    ctx.check();frames=[]
    for block in native['blocks']:
        if not block.get('pbch_crc_pass'):continue
        if not 0<=block['cell_id']<=1007 or not 0<=block['sfn']<=1023 or block['scs_common_khz'] not in (15,30):
            raise DomainError('DECODER_ERROR','Decoded NR MIB failed structural checks')
        frames.append(dict(block,center_hz=meta['center_hz'],integrity_unit='broadcast_block',
            integrity_check='NR PBCH polar decode and CRC24C, followed by MIB unpack',
            ssb_position_estimate=dict(sample_start=block['ssb_sample_start'],sample_stop=block['ssb_sample_stop'],calibrated=False),
            timing_scope='extraction_window_only',packet_boundary_available=False))
    evaluated=native['evaluated_prefix_samples']
    value=dict(artifact_id=artifact_id,protocol='5G NR',profile='NR FR1 SSB/PBCH/MIB',
        backend='srsRAN_4G NR PHY receive-only file adapter',source='https://github.com/srsran/srsRAN_4G',source_commit=COMMIT,
        adapter_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        duplex_mode=profile['duplex_mode'],ssb_scs_khz=ssb_scs_khz,ssb_pattern=profile['pattern'],center_hz=meta['center_hz'],
        simulated=meta.get('simulated',False),frames=frames,valid_frames=len(frames),validated_broadcast_blocks=len(frames),
        integrity_unit='broadcast_block',distinct_decoded_pci=sorted({f['cell_id'] for f in frames}),
        evidence_level='decoded' if frames else 'unknown',
        coverage=dict(provided_samples=len(iq),evaluated_samples=evaluated,sample_rate_hz=rate,
            intervals=[dict(sample_start=0,sample_stop=evaluated)] if evaluated else [],search_windows=native['search_windows'],
            hit_limit_reached=native['hit_limit_reached'],
            search_policy='Strongest synchronization hypothesis per5ms window with SSB overlap. Not exhaustive weak/co-channel cell detection.'),
        warnings=['Only CRC-passed, MIB-unpacked blocks count as NR confirmation; no blocks does not prove absence.',
            'SSB center is not necessarily carrier center. The profile duplex is assumed, not inferred from MIB.',
            'MIB does not provide full carrier bandwidth or PLMN/operator identity; SIB and user traffic are not decoded.',
            '20..500ms input may miss long-period or weak SSB. Supported FR1 subset only; no FR2 or60/120/240kHz SSB.',
            'SSB position estimates are not calibrated packet or full-carrier boundaries; display extraction windows only.'])
    aid=ctx.json_artifact('nr_verification',value)
    return dict(evidence_id=aid,**{k:v for k,v in value.items() if k!='frames'},frames=frames[:12],frame_summary_truncated=len(frames)>12)
