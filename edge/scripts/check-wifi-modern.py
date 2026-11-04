"""No RF: pinned upstream waveform generator -> native GNU Radio RX -> CRC."""
import contextlib
import io
import json
import sys
import numpy as np
from spectrum_agent.common import ROOT,dump
from spectrum_agent.wifi_modern import receive_iq
sys.path.insert(0,str(ROOT/'vendor/gr-wifi-modern/tools'))
import phy80211,phy80211header as ph
from pktGenExample import genMac80211UdpMPDU,genMac80211UdpAmpduVht

results=[]
for fmt in (ph.F.L,ph.F.HT,ph.F.VHT):
    with contextlib.redirect_stdout(io.StringIO()):
        generator=phy80211.phy80211(ifDebug=False)
        packet=genMac80211UdpMPDU(b'spectrum receiver acceptance')
        if fmt==ph.F.VHT:
            agg=genMac80211UdpAmpduVht([b'spectrum receiver acceptance'])
            generator.genFromAmpdu(agg,ph.modulation(phyFormat=fmt,mcs=0,bw=ph.BW.BW20,nSTS=1,shortGi=False),vhtPartialAid=0,vhtGroupId=0)
        else:generator.genFromMpdu(packet,ph.modulation(phyFormat=fmt,mcs=0,bw=ph.BW.BW20,nSTS=1,shortGi=False))
        wave=generator.genFinalSig(multiplier=1.,cfoHz=0.,num=1,gap=True,gapLen=4000)
    x=np.asarray(wave,dtype=np.complex64)
    if x.ndim==2:x=x[0]
    print('fixture',fmt,x.shape,flush=True)
    frames=receive_iq(x)
    row=dict(format=str(fmt),sample_count=len(x),valid_frames=len(frames),
        received_formats=[f['phy_format'] for f in frames],frames=frames)
    results.append(row);print(json.dumps(row),flush=True)
dump(ROOT/'docs/testing/wifi-modern-loopback.json',dict(new_rf_capture=False,checks=results))
assert all(row['valid_frames']>0 for row in results)
