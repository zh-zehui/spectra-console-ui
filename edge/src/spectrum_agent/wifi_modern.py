"""Receive-only adapter for pinned cloud9477 GR-WiFi v1.0,20MHz SISO."""
import ctypes
import importlib
import sys
import zlib
import threading
from collections import Counter
import numpy as np
from .common import ROOT,DomainError
from .dsp import load_iq

PIN='dc93c8f30de46082690d5768d6ad03ea2c11e2cb'


def module():
    import gnuradio
    from gnuradio import gr,blocks,fft
    prefix=ROOT/'runtime/wifi-modern'
    library=prefix/'lib/libgnuradio-ieee80211.so'
    if not library.exists():raise DomainError('DEPENDENCY_MISSING','Project-local GR-WiFi is not built')
    ctypes.CDLL(str(library),mode=ctypes.RTLD_GLOBAL)
    site=str(prefix/'lib/python3.11/site-packages/gnuradio')
    if site not in gnuradio.__path__:gnuradio.__path__.insert(0,site)
    return importlib.import_module('gnuradio.ieee80211')


def parse_message(raw):
    if len(raw)<8 or raw[0] not in (0,1,2):return None
    size=int.from_bytes(raw[1:3],'little')
    if size<14 or len(raw)!=size+4:return None
    packet=raw[3:3+size]
    if zlib.crc32(packet[:-4])!=int.from_bytes(packet[-4:],'little'):return None
    return dict(phy_format=('legacy','HT','VHT')[raw[0]],mcs=raw[-1],
        frame_hex=packet[:-4].hex(),mac_header_hex=packet[:-4][:24].hex(),
        frame_length=len(packet)-4,fcs_hex=packet[-4:].hex(),fcs_pass=True,
        fcs_verified_by='GR-WiFi native CRC plus independent Python zlib CRC32',
        evidence_level='decoded')


def receive_iq(iq,return_diagnostics=False):
    from gnuradio import gr,blocks
    import pmt
    ieee=module();tb=gr.top_block()
    # Padding drains finite-stream lookahead; returned timestamps must refer to original IQ.
    x=np.concatenate([np.asarray(iq,dtype=np.complex64),np.zeros(32768,np.complex64)])
    source=blocks.vector_source_c(x.tolist(),False)
    delay=blocks.delay(gr.sizeof_gr_complex,16)
    product=blocks.multiply_conjugate_cc(1)
    average=blocks.moving_average_cc(48,1.,4000,1)
    magnitude=blocks.complex_to_mag(1)
    power=blocks.complex_to_mag_squared(1)
    poweravg=blocks.moving_average_ff(64,1.,4000,1)
    divide=blocks.divide_ff(1)
    trigger=ieee.trigger();sync=ieee.sync();sig=ieee.signal()
    demod=ieee.demod(0,2);decoder=ieee.decode(False);messages=blocks.message_debug()
    tb.connect(source,delay,(product,0));tb.connect(source,(product,1))
    tb.connect(product,average,magnitude,(divide,0))
    tb.connect(source,power,poweravg,(divide,1))
    tb.connect(divide,trigger,(sync,0));tb.connect(average,(sync,1));tb.connect(source,(sync,2))
    tb.connect(sync,(sig,0));tb.connect(source,(sig,1));tb.connect(sig,demod,decoder)
    tb.msg_connect(decoder,'out',messages,'store')
    telemetry=[]
    header_sink=blocks.message_debug()
    tb.msg_connect(demod,'spectrum_headers',header_sink,'store')
    for block in (sig,demod,decoder):
        sink=blocks.message_debug();tb.msg_connect(block,'spectrum_status',sink,'store');telemetry.append(sink)
    tb.start(4096)
    finished=threading.Thread(target=tb.wait,daemon=True);finished.start();finished.join(8)
    if finished.is_alive():
        tb.stop();finished.join(3)
        raise DomainError('DECODER_TIMEOUT','GR-WiFi finite-stream scheduler did not finish; this is not a zero-frame result')
    frames=[]
    for i in range(messages.num_messages()):
        msg=messages.get_message(i);raw=bytes(pmt.to_python(pmt.cdr(msg)))
        frame=parse_message(raw)
        if frame:
            meta=pmt.car(msg)
            start=int(pmt.to_uint64(pmt.dict_ref(meta,pmt.mp('spectrum_start'),pmt.from_uint64(0))))
            stop=int(pmt.to_uint64(pmt.dict_ref(meta,pmt.mp('spectrum_stop'),pmt.from_uint64(0))))
            if 0<=start<stop<=len(iq):
                frame.update(subband_sample_start=start,subband_sample_stop=stop,
                    timing_status='sync_and_lsig_containing_ppdu_estimate',
                    timing_scope='containing_ppdu_not_individual_aggregated_mpdu',
                    timing_uncertainty_samples=16,timing_uncertainty_calibrated=False,
                    occupied_bandwidth_hz=17e6,bandwidth_kind='decoder_passband_not_measured_occupied_bandwidth')
            frames.append(frame)
    counts=Counter()
    from .wifi_headers import parse as parse_header
    headers=[]
    for i in range(header_sink.num_messages()):
        h=pmt.to_python(header_sink.get_message(i))
        h['bits']=list(map(int,h['bits']))
        parsed=parse_header(h,len(iq))
        if parsed:headers.append(parsed)
    for sink in telemetry:
        for i in range(sink.num_messages()):counts[pmt.symbol_to_string(sink.get_message(i))]+=1
    for frame in frames:
        matching=next((h for h in headers if abs(h['subband_sample_start']-frame.get('subband_sample_start',-1000))<=16),None)
        if matching:frame['lsig_rate_mbps']=matching['lsig_rate_mbps']
        elif frame['phy_format']=='legacy':frame['lsig_rate_mbps']=(6,9,12,18,24,36,48,54)[frame['mcs']] if frame['mcs'] in range(8) else None
    stages=dict(counts=dict(counts),headers=headers,strongest_stage='mac_fcs_pass' if frames else
        'phy_header_pass' if headers else
        'phy_header_pass' if counts['ht_sig_pass'] or counts['vht_sig_a_pass'] else
        'lsig_pass' if counts['lsig_pass'] else 'lsig_failed' if counts['lsig_failed'] else 'no_header_candidate',
        header_check='L-SIG rate/length/parity; HT-SIG or VHT-SIG-A native checks; MAC FCS independently rechecked',
        unique_valid_frames=len(frames),count_unit='Native header attempts and MPDU FCS checks; not unique emitters')
    return (frames,stages) if return_diagnostics else frames


def verify(ctx,artifact_id):
    iq,meta=load_iq(ctx,artifact_id)
    if abs(meta['sample_rate_hz']-20e6)>1:
        raise DomainError('INVALID_ARGUMENT','GR-WiFi20 SISO requires20MS/s IQ')
    if meta.get('requested_span_hz',0)<17e6:
        raise DomainError('INVALID_ARGUMENT','Extract at least17MHz passband for20MHz OFDM before verification')
    if len(iq)>4_000_000:raise DomainError('RESOURCE_LIMIT','At most0.2s per call')
    ctx.check();frames,stages=receive_iq(iq,return_diagnostics=True)
    for f in frames:f['center_hz']=meta['center_hz']
    result=dict(artifact_id=artifact_id,protocol='IEEE802.11',profile='GR-WiFi20MHz SISO legacy/HT/VHT',
        verification_profile='wifi_gr20_siso',backend='cloud9477/gr-ieee80211 v1.0 '+PIN,
        verification_stages=stages,
        analysis_coverage=dict(input_samples=len(iq),processed_samples=len(iq),complete=True),
        valid_frames=len(frames),frames=frames,simulated=meta.get('simulated',False),
        evidence_level='decoded' if frames else 'phy_header_pass' if stages.get('headers') else 'unknown',
        warnings=['Full MAC reception is20MHz single stream only; independently CRC-checked HT40/VHT40/80/160 header observations are available,not wideband data decoding',
            'HT A-MPDU is not implemented by this pinned backend; HT non-aggregated and VHT MPDU FCS paths only',
            'A CRC-valid MPDU confirms that packet, not an entire spectral region or payload decryption'])
    aid=ctx.json_artifact('wifi_verification',result)
    return dict(result,evidence_id=aid,frames=[{k:v for k,v in f.items() if k!='frame_hex'} for f in frames[:10]],
        frame_summary_truncated=len(frames)>10)
