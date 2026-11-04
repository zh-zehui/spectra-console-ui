"""Independent checks: upstream80MHz waveform ->20MHz observation -> SIG CRC,not MAC."""
import contextlib,io,json,sys
import numpy as np
from scipy.signal import resample_poly
from spectrum_agent.common import ROOT,dump
from spectrum_agent.wifi_modern import receive_iq
sys.path.insert(0,str(ROOT/'vendor/gr-wifi-modern/tools'))
import phy80211,phy80211header as ph
from pktGenExample import genMac80211UdpAmpduVht

checks=[]
for width in (ph.BW.BW20,ph.BW.BW80):
    with contextlib.redirect_stdout(io.StringIO()):
        generator=phy80211.phy80211(ifDebug=False)
        agg=genMac80211UdpAmpduVht([b'VHT wide header offline validation'])
        mod=ph.modulation(phyFormat=ph.F.VHT,mcs=0,bw=width,nSTS=1,shortGi=False)
        if width==ph.BW.BW20:
            generator.genFromAmpdu(agg,mod,vhtPartialAid=0,vhtGroupId=0)
            wave=generator.genFinalSig(multiplier=1.,cfoHz=0.,num=1,gap=True,gapLen=4000)
        else:
            # Upstream80MHz PAYLOAD interleaver is incomplete. Use its supported
            # standard80MHz preamble generator only; make no payload claim.
            generator.ampdu=agg;generator.m=mod;generator.m.ampdu=True
            generator.m.procPktLenAggre(len(agg));generator.vhtPartialAid=0;generator.vhtGroupId=0;generator.m.mu=False
            generator._phy80211__genLegacyTraining();generator._phy80211__genLegacySignal();generator._phy80211__genVhtSignalA()
            wave=np.concatenate([np.zeros(16000),generator.ssLegacyTraining[0],generator.ssLegacySig[0],generator.ssVhtSigA[0],np.zeros(16000)])
    x=np.asarray(wave,dtype=np.complex64)
    if x.ndim==2:x=x[0]
    for offset in ((0,) if width==ph.BW.BW20 else (-30e6,-10e6,10e6,30e6)):
        y=x if width==ph.BW.BW20 else resample_poly(x*np.exp(-2j*np.pi*offset/80e6*np.arange(len(x))),1,4).astype(np.complex64)
        frames,stages=receive_iq(y,True)
        headers=stages['headers'];expected=20e6 if width==ph.BW.BW20 else 80e6
        row=dict(channel_width_hz=expected,observed20MHz_offset_hz=offset,headers=headers,valid_mac_frames=len(frames))
        checks.append(row);print(json.dumps(row),flush=True)
        assert any(h['phy_format']=='VHT' and h['channel_width_hz']==expected for h in headers)
        if width==ph.BW.BW80:assert not frames
rng=np.random.default_rng(91)
_,noise=receive_iq((rng.normal(size=50000)+1j*rng.normal(size=50000)).astype(np.complex64),True)
assert not noise['headers']
dump(ROOT/'docs/testing/wide-wifi-headers-synthetic.json',dict(new_rf_capture=False,checks=checks,noise_header_false_positives=0,
    capability='CRC-checked VHT80 header recognition via20MHz duplicate preamble,not80MHz MAC decode'))
