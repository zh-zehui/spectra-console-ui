"""MAC type names from decoded frame-control bits,not spectral inference."""
def subtype(frame):
    raw=frame.get('mac_header_hex') or frame.get('frame_hex','')
    try:fc=int.from_bytes(bytes.fromhex(raw)[:2],'little')
    except ValueError:return 'MAC'
    if len(raw)<4 or fc&3:return 'MAC'
    kind=(fc>>2)&3;sub=(fc>>4)&15
    if kind==1:return {4:'BRP',5:'VHT NDP Announcement',7:'Control Wrapper',8:'BAR',9:'BA',10:'PS-Poll',11:'RTS',12:'CTS',13:'ACK',14:'CF-End',15:'CF-End+ACK'}.get(sub,'Control '+str(sub))
    if kind==0:return {0:'Association Request',1:'Association Response',4:'Probe Request',5:'Probe Response',8:'Beacon',10:'Disassociation',11:'Authentication',12:'Deauthentication',13:'Action'}.get(sub,'Management '+str(sub))
    if kind==2:return {0:'Data',4:'Null',8:'QoS Data',12:'QoS Null'}.get(sub,'Data '+str(sub))
    return 'MAC'


def label(frame):
    phy=frame.get('phy_format','legacy' if 'phy_encoding' in frame else 'PHY未解析')
    rate=frame.get('lsig_rate_mbps')
    if rate is None and ('phy_encoding' in frame or phy=='legacy'):
        encoding=frame.get('phy_encoding',frame.get('mcs'))
        if isinstance(encoding,int) and 0<=encoding<8:rate=(6,9,12,18,24,36,48,54)[encoding]
    fields=['Wi-Fi',phy,subtype(frame),'MAC FCS通过']
    if rate is not None:fields.append('L-SIG R'+str(rate))
    elif frame.get('mcs') is not None:fields.append('MCS '+str(frame['mcs']))
    return ' · '.join(fields)
