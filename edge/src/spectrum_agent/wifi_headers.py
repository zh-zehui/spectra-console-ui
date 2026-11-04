"""CRC-checked HT/VHT header evidence, independent of payload reception support."""
RATES=(6,9,12,18,24,36,48,54)


def crc8(bits):
    state=255
    for b in bits:
        feedback=((state>>7)&1)^int(b)
        state=((state<<1)&255)^(7 if feedback else 0)
    value=state^255
    return [(value>>(7-i))&1 for i in range(8)]


def parse(header,input_samples):
    bits=list(header.get('bits',[]));fmt=header.get('format');a=header.get('sample_start');b=header.get('sample_stop')
    if len(bits)!=48 or any(x not in (0,1) for x in bits) or crc8(bits[:34])!=bits[34:42] or any(bits[42:]):return None
    if not isinstance(a,int) or not isinstance(b,int) or not 0<=a<b<=input_samples:return None
    def number(start,count):return sum(bits[start+i]<<i for i in range(count))
    common=dict(phy_format=fmt,header_bits=''.join(map(str,bits)),header_crc_pass=True,mac_fcs_pass=None,
        evidence_level='phy_header_pass',subband_sample_start=a,subband_sample_stop=b,
        lsig_rate_mbps=RATES[header['lsig_mcs']] if header.get('lsig_mcs') in range(8) else None,
        lsig_length=header.get('lsig_length'),timing_status='sync_and_lsig_containing_ppdu_estimate',
        timing_uncertainty_calibrated=False,payload_decoded=False)
    if fmt=='VHT':
        if bits[2]!=1 or bits[23]!=1 or bits[33]!=1:return None
        group=number(4,6);su=group in (0,63);nsts=number(10,3)+1
        common.update(channel_width_hz=(20e6,40e6,80e6,160e6)[number(0,2)],group_id=group,
            transmission='SU' if su else 'MU',short_gi=bool(bits[24]),stbc=bool(bits[3]),
            mcs=number(28,4) if su else None,num_space_time_streams=nsts if su else None,
            num_spatial_streams=(nsts//2 if bits[3] else nsts) if su else None,
            coding=('LDPC' if bits[26] else 'BCC') if su else None)
        if su and (common['mcs']>9 or (bits[3] and nsts%2)):return None
    elif fmt=='HT':
        if bits[26]!=1:return None
        mcs=number(0,7)
        common.update(channel_width_hz=40e6 if bits[7] else 20e6,mcs=mcs,short_gi=bool(bits[31]),
            coding='LDPC' if bits[30] else 'BCC',aggregation=bool(bits[27]),psdu_length=number(8,16),
            num_spatial_streams=mcs//8+1 if mcs<=31 else None)
    else:return None
    return common
