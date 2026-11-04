"""Bounded BR/EDR access-code evidence using open-source libbtbb; no payload claim."""
import ctypes
import hashlib
import json
from pathlib import Path
import numpy as np
from .common import ROOT,DomainError,finite_number

SOURCE='https://github.com/greatscottgadgets/libbtbb'
COMMIT='f0fe1768b080b1333a5357e8308d900e1273ec7f'


def library():
    path=ROOT/'runtime/libbtbb/lib/libbtbb.so'
    if not path.exists():raise DomainError('DEPENDENCY_MISSING','Build project-local libbtbb before using classic Bluetooth access-code analysis')
    lib=ctypes.CDLL(str(path))
    lib.btbb_init.argtypes=[ctypes.c_int];lib.btbb_init.restype=ctypes.c_int
    lib.btbb_find_ac.argtypes=[ctypes.POINTER(ctypes.c_char),ctypes.c_int,ctypes.c_uint32,ctypes.c_int,ctypes.POINTER(ctypes.c_void_p)]
    lib.btbb_find_ac.restype=ctypes.c_int
    lib.btbb_packet_get_lap.argtypes=[ctypes.c_void_p];lib.btbb_packet_get_lap.restype=ctypes.c_uint32
    lib.btbb_packet_get_ac_errors.argtypes=[ctypes.c_void_p];lib.btbb_packet_get_ac_errors.restype=ctypes.c_uint8
    lib.btbb_packet_unref.argtypes=[ctypes.c_void_p];lib.btbb_packet_unref.restype=None
    lib.btbb_gen_syncword.argtypes=[ctypes.c_int];lib.btbb_gen_syncword.restype=ctypes.c_uint64
    if lib.btbb_init(0)!=0:raise DomainError('DEPENDENCY_ERROR','libbtbb initialization failed')
    return lib


def scan_symbols(lib,bits,limit=64):
    bits=np.ascontiguousarray(bits,dtype=np.uint8);cursor=0;hits=[]
    while len(bits)-cursor>72 and len(hits)<limit:
        pointer=bits[cursor:].ctypes.data_as(ctypes.POINTER(ctypes.c_char));packet=ctypes.c_void_p()
        position=lib.btbb_find_ac(pointer,len(bits)-cursor-72,0xffffffff,0,ctypes.byref(packet))
        if position<0:break
        try:
            start=cursor+position
            hits.append(dict(symbol_start=start,lap=int(lib.btbb_packet_get_lap(packet)),access_code_bit_errors=int(lib.btbb_packet_get_ac_errors(packet)),
                alternating_preamble=bool(start>=4 and np.all(np.diff(bits[start-4:start].astype(int))!=0))))
        finally:
            if packet:lib.btbb_packet_unref(packet)
        cursor=start+64
    return hits


def analyze(ctx,artifact_id,frequency_offset_hz=0):
    from .dsp import load_iq
    rec=ctx.resolve(artifact_id)
    if rec['kind']!='subband':raise DomainError('INVALID_ARTIFACT','Extract a focused1MHz subband at a nominal classic Bluetooth channel first')
    iq,meta=load_iq(ctx,artifact_id);rate=meta['sample_rate_hz'];center=meta['center_hz']
    if abs(rate-4e6)>1:raise DomainError('UNSUPPORTED_RATE','Classic access-code analysis requires4MS/s; extract1MHz passband with output_rate_hz=4000000')
    channel=round((center-2402e6)/1e6)
    if not 0<=channel<=78 or abs(center-(2402e6+channel*1e6))>1000:
        raise DomainError('OUT_OF_BAND','Choose a nominal classic Bluetooth center2402..2480MHz in1MHz steps')
    if not .8e6<=meta['requested_span_hz']<=1.5e6:raise DomainError('INVALID_ARGUMENT','Use an approximately1MHz passband to isolate a channel')
    finite_number(frequency_offset_hz,'frequency_offset_hz',-200000,200000)
    lib=library();n=min(len(iq),2_000_000)
    if n<512:raise DomainError('INSUFFICIENT_EVIDENCE','At least512 IQ samples required')
    if not any(s['file_start']==0 and s['count']>=n for s in meta['segments']):
        raise DomainError('CAPTURE_GAP','Analyze one continuous subband interval at a time')
    x=np.asarray(iq[:n]);discriminator=np.angle(x[1:]*np.conj(x[:-1]))
    candidates=[]
    for phase in range(4):
        ctx.check();count=(len(discriminator)-phase)//4
        soft=discriminator[phase:phase+count*4].reshape(-1,4).mean(axis=1)
        for residual in (-125000,0,125000):
            ctx.check();offset=frequency_offset_hz+residual
            bits=(soft>2*np.pi*offset/rate).astype(np.uint8)
            for hit in scan_symbols(lib,bits):
                sample=phase+4*hit['symbol_start']
                if any(h['lap']==hit['lap'] and abs(h['syncword_sample_start']-sample)<=8 for h in candidates):continue
                candidates.append(dict(lap=hit['lap'],lap_hex=f"{hit['lap']:06x}",
                    lap_fingerprint=hashlib.sha256(str(hit['lap']).encode()).hexdigest()[:12],
                    syncword_sample_start=sample,syncword_sample_stop=min(n,sample+64*4),
                    symbol_phase=phase,frequency_offset_trial_hz=offset,
                    access_code_bit_errors=hit['access_code_bit_errors'],alternating_preamble=hit['alternating_preamble'],
                    evidence_level='access_code_candidate',protocol_confirmed=False))
                if len(candidates)>=64:break
            if len(candidates)>=64:break
        if len(candidates)>=64:break
    value=dict(artifact_id=artifact_id,protocol='unknown',hypothesis='Bluetooth BR/EDR access code',
        simulated=meta.get('simulated',False),
        backend='libbtbb exact access-code search + numerical GFSK discriminator',source=SOURCE,source_commit=COMMIT,
        channel=channel,center_hz=center,sample_rate_hz=rate,
        coverage=dict(evaluated_samples=n,total_samples=len(iq),intervals=[dict(sample_start=0,sample_stop=n)],truncated=n<len(iq)),
        access_code_hit_count=len(candidates),hits=candidates,hits_truncated=len(candidates)>=64,
        valid_frames=0,protocol_confirmed=False,evidence_level='access_code_candidate' if candidates else 'unknown',
        limitations=['Access-code structure only: no independently validated HEC, payload CRC or complete packet decode.',
            'No hits does not prove absence; limited timing/CFO trials and recorded time/frequency coverage.',
            'Syncword spans are not packet or full-activity boundaries. Unknown labels remain.'])
    aid=ctx.json_artifact('bluetooth_classic_analysis',value)
    return dict(evidence_id=aid,**{k:v for k,v in value.items() if k!='hits'},
        hits=[{k:v for k,v in h.items() if k not in ('lap','lap_hex')} for h in candidates[:12]],summary_truncated=len(candidates)>12)
