"""Recorded-IQ LTE PSS/SSS search and CRC-checked PBCH/MIB via pinned srsRAN."""
import json
import hashlib
import subprocess
import uuid
from .common import ROOT,DomainError

COMMIT='bef8680d5f9714f3e040e6f9cbc88d7888439b6d'


def verify(ctx,artifact_id,duplex_mode='auto'):
    from .dsp import load_iq
    rec=ctx.resolve(artifact_id)
    if rec['kind']!='subband':raise DomainError('INVALID_ARTIFACT','Extract the central1.4MHz at a candidate LTE carrier center with output_rate_hz=1920000')
    if duplex_mode not in ('auto','tdd','fdd'):raise DomainError('INVALID_ARGUMENT','duplex_mode must be auto, tdd or fdd')
    iq,meta=load_iq(ctx,artifact_id)
    if abs(meta['sample_rate_hz']-1920000)>1:
        raise DomainError('UNSUPPORTED_RATE','LTE PBCH tool requires1.92MS/s; extract1.4MHz passband with output_rate_hz=1920000')
    if not 1.2e6<=meta['requested_span_hz']<=2e6:
        raise DomainError('INVALID_ARGUMENT','Use a central1.4MHz subband; this tool does not decode the full LTE carrier')
    if not 76800<=len(iq)<=1920000:
        raise DomainError('RESOURCE_LIMIT','LTE synchronization/PBCH requires40ms..1s continuous IQ; prefer the whole100ms recording, not a short segmentation box')
    if len(meta['segments'])!=1 or meta['segments'][0]['count']!=len(iq):
        raise DomainError('CAPTURE_GAP','LTE decode must not cross discontinuities')
    binary=ROOT/'runtime/lte/bin/spectrum_lte_rx'
    if not binary.exists():raise DomainError('DEPENDENCY_MISSING','Build the project-local srsRAN LTE file receiver first')
    ctx.check()
    try:
        process=subprocess.run([str(binary),rec['path'],duplex_mode],cwd=ctx.run,capture_output=True,text=True,timeout=90)
    except subprocess.TimeoutExpired as exc:
        raise DomainError('DECODER_TIMEOUT','LTE file receiver exceeded90s; retain unknown activity') from exc
    key='lte-native-'+uuid.uuid4().hex[:12]
    (ctx.run/(key+'.log')).write_text(process.stderr,encoding='utf-8')
    if process.returncode!=0:raise DomainError('DECODER_ERROR','LTE file receiver failed; inspect task-local native log')
    try:native=json.loads(process.stdout)
    except json.JSONDecodeError as exc:raise DomainError('DECODER_ERROR','Invalid LTE native result') from exc
    ctx.check();cells=native.get('cells',[])
    for cell in cells:
        count=cell.get('matching_cell_detections')
        cell['synchronization_consistency']=('not_measured' if count is None else 'repeated_same_pci_candidate' if count>=2 else 'single_detection_unconfirmed')
    trials=native.get('synchronization_trials',[])
    consumed=max([t.get('samples_consumed',0) for t in trials]+[c.get('mib_samples_consumed',0) for c in cells]+[0])
    frames=[]
    for cell in cells:
        if not cell.get('pbch_crc_pass'):continue
        if not 0<=cell['cell_id']<=503 or cell['nof_prb'] not in (6,15,25,50,75,100) or cell['antenna_ports'] not in (1,2,4):
            raise DomainError('DECODER_ERROR','Decoded MIB failed structural validation')
        frames.append(dict(cell,center_hz=meta['center_hz'],integrity_unit='broadcast_block',
            integrity_check='LTE PBCH CRC16 with antenna-port mask',evidence_level='decoded',
            advertised_channel_bandwidth_hz={6:1.4e6,15:3e6,25:5e6,50:10e6,75:15e6,100:20e6}[cell['nof_prb']],
            timing_scope='extraction_window_only',packet_boundary_available=False))
    value=dict(artifact_id=artifact_id,protocol='LTE',profile='LTE downlink PSS/SSS + PBCH/MIB',
        backend='srsRAN_4G receive-only file adapter',source='https://github.com/srsran/srsRAN_4G',source_commit=COMMIT,
        adapter_version='v17',adapter_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        duplex_mode=duplex_mode,center_hz=meta['center_hz'],simulated=meta.get('simulated',False),
        synchronization_candidates=cells,synchronization_candidate_count=len(cells),frames=frames,valid_frames=len(frames),
        validated_broadcast_blocks=len(frames),integrity_unit='broadcast_block',
        evidence_level='decoded' if frames else 'sync_candidate' if cells else 'unknown',
        coverage=dict(provided_samples=len(iq),evaluated_samples=consumed,sample_rate_hz=1920000,
            intervals=[dict(sample_start=0,sample_stop=consumed)] if consumed else [],synchronization_trials=trials,
            warning='Search may stop early per root or reach an internal scan budget; supplied samples are not automatically full evaluated coverage. Recognition bounds remain extraction-window-only.',
            search_policy='At most one strongest PSS/SSS candidate per N_id_2, then PBCH attempt; not an exhaustive co-channel cell census'),
        warnings=['A PSS/SSS candidate alone is not CRC-confirmed LTE; no result does not establish absence.',
            'PBCH CRC confirms the decoded broadcast block, not all activity in the extraction window or a physical transmitter count.',
            'Advertised carrier bandwidth from MIB is not measured occupied bandwidth; only the central subband was decoded.',
            'No packet timestamp available; verified boundaries are extraction windows only.',
            'No SIB/PLMN/operator identification, subscriber traffic, NR5G or uplink decoding is implemented.'])
    aid=ctx.json_artifact('lte_verification',value)
    return dict(evidence_id=aid,**value)
