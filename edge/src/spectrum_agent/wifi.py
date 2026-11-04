"""Receive-only wrapper around the pinned gr-ieee802-11 a/g/p implementation."""
import ctypes
import json
from pathlib import Path
import sys
import time
import numpy as np
from .common import ROOT,DomainError
from .dsp import load_iq


def module():
    # Load only the project-local library. No changes to system linker or GNU Radio installation.
    from gnuradio import gr,blocks,digital,analog,fft,filter
    prefix=ROOT/"runtime/radio-plugins"
    library=prefix/"lib/libgnuradio-ieee802_11.so"
    if not library.exists():
        raise DomainError("DEPENDENCY_MISSING","Project-local Wi-Fi decoder is not built")
    ctypes.CDLL(str(library),mode=ctypes.RTLD_GLOBAL)
    site=str(prefix/"lib/python3.11/site-packages")
    if site not in sys.path: sys.path.insert(0,site)
    import ieee802_11
    return ieee802_11


def receive_iq(iq,center_hz=2412e6,sample_rate_hz=20e6,threshold=.56,debug=False):
    from gnuradio import gr,blocks,fft
    import pmt
    ieee=module()
    packets=[]
    class Collector:
        def __init__(self):
            pass
        def handle(self,msg):
            if len(packets)>=500: return
            raw=bytes(pmt.to_python(pmt.cdr(msg)))
            metadata=pmt.car(msg)
            timestamp=pmt.dict_ref(metadata,pmt.intern('spectrum_ltf_sample'),pmt.PMT_NIL)
            location={}
            if not pmt.is_null(timestamp) and int(pmt.to_uint64(timestamp))<len(iq):
                ltf=int(pmt.to_uint64(timestamp))
                encoding=int(pmt.to_uint64(pmt.dict_ref(metadata,pmt.intern('encoding'),pmt.from_uint64(0))))
                size=int(pmt.to_uint64(pmt.dict_ref(metadata,pmt.intern('frame bytes'),pmt.from_uint64(len(raw)+4))))
                ndbps=(24,36,48,72,96,144,192,216)[encoding]
                start=max(0,ltf-192)
                duration=400+80*((16+8*size+6+ndbps-1)//ndbps)
                location=dict(subband_sample_start=start,subband_sample_stop=min(len(iq),start+duration),
                    subband_ltf_sample=ltf,timing_reference='first useful long-training symbol minus192 samples',
                    timing_status='sync-derived_estimate',timing_uncertainty_samples=8,timing_uncertainty_calibrated=False,center_hz=center_hz,
                    occupied_bandwidth_hz=17e6,phy_encoding=encoding)
            packets.append(dict(frame_length=len(raw),mac_header_hex=raw[:24].hex(),**location,
                frame_hex=raw.hex(),fcs_pass=True,
                fcs_verified_by="gr-ieee802-11 decode_mac CRC32 residue558161692",
                evidence_level="decoded"))
    tb=gr.top_block()
    source=blocks.vector_source_c(np.asarray(iq,dtype=np.complex64).tolist(),False)
    delay16=blocks.delay(gr.sizeof_gr_complex,16)
    conjugate=blocks.conjugate_cc()
    product=blocks.multiply_cc()
    avgcorr=blocks.moving_average_cc(48,1.,4000,1)
    magnitude=blocks.complex_to_mag()
    power=blocks.complex_to_mag_squared()
    avgpower=blocks.moving_average_ff(64,1.,4000,1)
    divide=blocks.divide_ff()
    short=ieee.sync_short(threshold,32,False,debug)
    delay320=blocks.delay(gr.sizeof_gr_complex,320)
    long=ieee.sync_long(320,False,debug)
    vector=blocks.stream_to_vector(gr.sizeof_gr_complex,64)
    transform=fft.fft_vcc(64,True,tuple([1.]*64),True,1)
    equalizer=ieee.frame_equalizer(ieee.LS,center_hz,sample_rate_hz,False,debug)
    decoder=ieee.decode_mac(False,debug)
    collector=Collector()
    messages=blocks.message_debug()
    tb.connect(source,delay16,conjugate,(product,1))
    tb.connect(source,(product,0))
    tb.connect(product,avgcorr,magnitude,(divide,0))
    tb.connect(source,power,avgpower,(divide,1))
    tb.connect(delay16,(short,0));tb.connect(avgcorr,(short,1));tb.connect(divide,(short,2))
    tb.connect(short,(long,0));tb.connect(short,delay320,(long,1))
    tb.connect(long,vector,transform,equalizer,decoder)
    tb.msg_connect(decoder,"out",messages,"store")
    tb.run(4096)
    for i in range(min(messages.num_messages(),500)):
        collector.handle(messages.get_message(i))
    return packets


def verify(ctx,artifact_id):
    iq,meta=load_iq(ctx,artifact_id)
    if abs(meta["sample_rate_hz"]-20e6)>1:
        raise DomainError("INVALID_ARGUMENT","Wi-Fi legacy OFDM decoder needs a20MS/s subband")
    if len(iq)>4_000_000:
        raise DomainError("RESOURCE_LIMIT","Decode at most0.2s/4M samples per call")
    ctx.check()
    packets=receive_iq(iq,meta["center_hz"],meta["sample_rate_hz"])
    result=dict(artifact_id=artifact_id,protocol="IEEE802.11",profile="legacy20MHz OFDM a/g",
        backend="bastibl/gr-ieee802-11 maint-3.10 ad0598e4a874f4b8e1f391a1e0323e80df2b34ff",
        frames=packets,valid_frames=len(packets),simulated=meta.get("simulated",False),
        evidence_level="decoded" if packets else "unknown",
        warnings=["Only legacy20MHz OFDM; not802.11b/n/ac/ax full PHY support",
                  "Payload may be encrypted; packet validation is not decryption",
                  "No decoded frame does not establish absence"])
    aid=ctx.json_artifact("wifi_verification",result)
    summary=dict(result)
    summary["frames"]=[{k:v for k,v in frame.items() if k!="frame_hex"} for frame in packets[:10]]
    summary["frame_summary_truncated"]=len(packets)>10
    return dict(evidence_id=aid,**summary)
