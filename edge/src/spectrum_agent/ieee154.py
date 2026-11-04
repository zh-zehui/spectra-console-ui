"""Receive-only 2.4GHz IEEE802.15.4 O-QPSK/DSSS250kb/s wrapper."""
import ctypes
import sys
import numpy as np
from .common import ROOT,DomainError
from .dsp import load_iq

UPSTREAM='a210b11fed2d307ef797ea79842b54f4e8ed3dd5'


def module():
    from gnuradio import gr,blocks,analog,digital,filter
    prefix=ROOT/'runtime/radio-plugins'
    lib=prefix/'lib/libgnuradio-ieee802_15_4.so'
    if not lib.exists(): raise DomainError('DEPENDENCY_MISSING','Project-local IEEE802.15.4 receiver not built')
    ctypes.CDLL(str(lib),mode=ctypes.RTLD_GLOBAL)
    site=str(prefix/'lib/python3.11/site-packages')
    if site not in sys.path: sys.path.insert(0,site)
    import ieee802_15_4
    return ieee802_15_4


def crc16(data):
    crc=0
    for byte in data:
        crc^=byte
        for _ in range(8): crc=(crc>>1)^0x8408 if crc&1 else crc>>1
    return crc


def receive_iq(iq):
    from gnuradio import gr,blocks,analog,digital,filter
    import pmt
    ieee=module();frames=[];stats=dict(phy_candidates=0,crc_rejected=0,header_rejected=0)
    class Collector(gr.basic_block):
        def __init__(self):
            gr.basic_block.__init__(self,name='validated802.15.4 PDUs',in_sig=None,out_sig=None)
            self.message_port_register_in(pmt.intern('in'));self.set_msg_handler(pmt.intern('in'),self.handle)
        def handle(self,msg):
            stats['phy_candidates']+=1
            raw=bytes(pmt.to_python(pmt.cdr(msg)))
            if not 5<=len(raw)<=127 or crc16(raw)!=0:
                stats['crc_rejected']+=1;return
            fcf=int.from_bytes(raw[:2],'little');frame_type=fcf&7;version=(fcf>>12)&3
            if version==3 or frame_type==4:
                stats['header_rejected']+=1;return
            if len(frames)>=500: return
            timing={}
            chip_stop=pmt.dict_ref(pmt.car(msg),pmt.intern('spectrum_chip_stop'),pmt.PMT_NIL)
            if not pmt.is_null(chip_stop):
                # M&M nominal omega=2 input IQ samples/chip. Its allowed omega
                # variation makes accumulated mapping drift possible; disclose it.
                recovered_chip_stop=int(pmt.to_uint64(chip_stop))
                stop=min(len(iq),recovered_chip_stop*2)
                duration=(6+len(raw))*128  # 4MHz *32us/byte, including SHR+PHR
                timing=dict(subband_sample_start=max(0,stop-duration),subband_sample_stop=stop,
                    recovered_chip_stop=recovered_chip_stop,
                    timing_reference='recovered-chip stream index mapped with nominal M&M omega2; PPDU byte duration',
                    timing_status='sync-derived_estimate',
                    timing_uncertainty_samples=int(np.ceil(16+recovered_chip_stop*.0004)),
                    timing_uncertainty_calibrated=False,occupied_bandwidth_hz=2e6)
            frames.append(dict(protocol='IEEE802.15.4',profile='2.4GHz O-QPSK DSSS250kb/s',
                frame_length=len(raw),frame_control=fcf,frame_type=frame_type,frame_version=version,
                frame_hex=raw.hex(),fcs_pass=True,fcs_verified_by='CRC16/KERMIT polynomial0x1021 reflected0x8408 init0 residue0',
                evidence_level='decoded',upper_layer_protocol='unknown',
                **(timing or dict(timing_status='packet_input_timestamp_not_available'))))
    tb=gr.top_block();source=blocks.vector_source_c(np.asarray(iq,dtype=np.complex64).tolist(),False)
    demod=analog.quadrature_demod_cf(1.)
    dc=filter.single_pole_iir_filter_ff(.00016,1);subtract=blocks.sub_ff()
    clock=digital.clock_recovery_mm_ff(2.,.000225,.5,.03,.0002)
    sink=ieee.packet_sink(10);collector=Collector()
    tb.connect(source,demod,(subtract,0));tb.connect(demod,dc,(subtract,1))
    tb.connect(subtract,clock,sink);tb.msg_connect(sink,'out',collector,'in');tb.run()
    return frames,stats


def verify(ctx,artifact_id,channel):
    if not isinstance(channel,int) or not 11<=channel<=26:
        raise DomainError('INVALID_ARGUMENT','IEEE802.15.4 channels11..26 required')
    iq,meta=load_iq(ctx,artifact_id);center=2405e6+5e6*(channel-11)
    if abs(meta['center_hz']-center)>1000 or abs(meta['sample_rate_hz']-4e6)>1:
        raise DomainError('INVALID_ARGUMENT','Use matching2405+5*(channel-11)MHz center and4MS/s subband')
    if len(iq)>4_000_000: raise DomainError('RESOURCE_LIMIT','At most1s/4M samples per call')
    if len(meta['segments'])!=1: raise DomainError('CAPTURE_GAP','Decode only one continuous subband')
    ctx.check();frames,stats=receive_iq(iq)
    for frame in frames:frame['center_hz']=center
    value=dict(artifact_id=artifact_id,protocol='IEEE802.15.4',profile='2.4GHz O-QPSK DSSS250kb/s',
        channel=channel,center_hz=center,backend='bastibl/gr-ieee802-15-4 maint-3.10 '+UPSTREAM,
        frames=frames,valid_frames=len(frames),**stats,simulated=meta.get('simulated',False),
        evidence_level='decoded' if frames else 'unknown',upper_layer_protocol='unknown',
        warnings=['Does not identify Zigbee versus Thread without upper-layer evidence',
                  'No valid frame does not establish absence; legacy O-QPSK receiver only',
                  'When available packet positions are recovered-clock estimates with accumulated drift, not calibrated source timestamps'])
    aid=ctx.json_artifact('ieee154_verification',value)
    return dict(evidence_id=aid,**{k:v for k,v in value.items() if k!='frames'},
        frames=[{k:v for k,v in f.items() if k!='frame_hex'} for f in frames[:10]],frame_summary_truncated=len(frames)>10)
