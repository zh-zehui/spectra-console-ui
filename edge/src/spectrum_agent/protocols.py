"""Experimental LE1M primary advertising receiver; CRC gates all packet labels."""
import json
import numpy as np
from scipy import signal
from .common import DomainError
from .dsp import load_iq


def whitening(bits,channel):
    # Bluetooth LE whitening: x^7+x^4+1, least-significant bit first.
    state=channel|0x40
    result=np.empty(len(bits),dtype=np.uint8)
    for i,b in enumerate(bits):
        bit=state&1
        result[i]=int(b)^bit
        state>>=1
        if bit: state^=0x44
    return result


def extended_header(payload):
    """Parse the Common Extended Advertising Payload, without following AuxPtr."""
    if not payload:
        raise ValueError('Missing extended payload')
    size=payload[0]&63
    mode=payload[0]>>6
    if size+1>len(payload) or mode==3:
        raise ValueError('Invalid extended header length or reserved advertising mode')
    result=dict(extended_header_length=size,advertising_mode=mode)
    if not size:
        return result
    flags=payload[1];position=2;end=1+size
    if flags&128:
        raise ValueError('Reserved extended header flag')
    for bit,name,length in ((0,'advertiser_address',6),(1,'target_address',6),
            (2,'cte_info',1),(3,'advertising_data_info',2),(4,'aux_ptr',3),
            (5,'sync_info',18),(6,'tx_power',1)):
        if not flags&(1<<bit):continue
        if position+length>end:
            raise ValueError('Extended header fields exceed declared length')
        value=payload[position:position+length];position+=length
        if name=='aux_ptr':
            packed=int.from_bytes(value,'little')
            channel=packed&63;phy=(packed>>21)&7
            if channel>36 or phy>2:
                raise ValueError('Reserved auxiliary channel or PHY')
            unit=300 if packed&128 else 30
            offset=(packed>>8)&8191
            result[name]=dict(channel=channel,phy=('LE1M','LE2M','LE Coded')[phy],
                offset_us=offset*unit,offset_unit_us=unit,
                followup_status='not_attempted' if offset else 'incomplete_advertising_data')
        elif name=='tx_power':result[name+'_dbm']=int.from_bytes(value,'little',signed=True)
        else:result[name+'_hex']=value.hex()
    return result


def ble_channel_hz(channel):
    if isinstance(channel,bool) or not isinstance(channel,int) or not 0<=channel<=39:
        raise DomainError('INVALID_ARGUMENT','BLE channel must be an integer0..39')
    if channel>=37:return {37:2402e6,38:2426e6,39:2480e6}[channel]
    return (2404+2*channel if channel<=10 else 2428+2*(channel-11))*1e6


def decode_ble(ctx,artifact_id,channel,profile='ble_1m_advertising'):
    from scapy.layers.bluetooth4LE import BTLE
    if profile not in ('ble_1m_advertising','ble_aux_1m','ble_aux_2m'):
        raise DomainError('UNSUPPORTED_PROFILE','Unsupported BLE advertising PHY')
    center=ble_channel_hz(channel)
    auxiliary=profile!='ble_1m_advertising'
    if (auxiliary and channel>36) or (not auxiliary and channel<37):
        raise DomainError('INVALID_ARGUMENT','Primary profile requires37..39; auxiliary profiles require0..36')
    symbol_rate=2e6 if profile=='ble_aux_2m' else 1e6
    expected_rate=4*symbol_rate
    description=('LE2M' if symbol_rate==2e6 else 'LE1M')+(' non-periodic auxiliary advertising' if auxiliary else ' primary legacy and extended advertising')
    iq,meta=load_iq(ctx,artifact_id)
    if abs(meta["center_hz"]-center)>1000:
        raise DomainError("INVALID_ARGUMENT","BLE artifact must be centered on the specified advertising channel")
    fs=meta["sample_rate_hz"]
    if abs(fs-expected_rate)>1:
        raise DomainError("INVALID_ARGUMENT",f"Use extract_subband with output_rate_hz={int(expected_rate)} first")
    if auxiliary and meta.get('requested_span_hz',0)<(3e6 if symbol_rate==2e6 else 2e6):
        raise DomainError('INVALID_ARGUMENT','Auxiliary profile needs a2MHz LE1M or3MHz LE2M extraction passband')
    x=np.asarray(iq[:min(len(iq),8_000_000)])
    if len(x)<160:
        raise DomainError("INSUFFICIENT_EVIDENCE","Too few IQ samples")
    # quadrature discriminator, then integrate over each symbol at all four phase offsets.
    discriminator=np.angle(x[1:]*np.conj(x[:-1]))
    aa=np.unpackbits(np.frombuffer(bytes.fromhex("d6be898e"),dtype=np.uint8),bitorder="little")
    matched=0;frames=[];seen=set()
    stages=dict(access_address_candidates=0,header_rejected=0,header_pass=0,
                truncated=0,crc_failed=0,crc_pass_trials=0,duplicate_trials=0,extended_header_rejected=0)
    candidate_limit_reached=False
    for offset in range(4):
        ctx.check()
        n=(len(discriminator)-offset)//4
        values=discriminator[offset:offset+4*n].reshape(-1,4).mean(axis=1)
        bits=(values>0).astype(np.uint8)
        if len(bits)<100: continue
        corr=signal.correlate(bits.astype(np.int16)*2-1,aa.astype(np.int16)*2-1,mode="valid")
        for start in np.flatnonzero(corr>=28):
            if matched>=10000:
                candidate_limit_reached=True
                break
            matched+=1
            stages['access_address_candidates']+=1
            payload_start=start+32
            header=whitening(bits[payload_start:payload_start+16],channel)
            if len(header)!=16:
                stages['truncated']+=1
                continue
            header_bytes=np.packbits(header,bitorder="little").tobytes()
            pdu_type=header_bytes[0]&15
            extended=pdu_type==7
            length=header_bytes[1] if extended else header_bytes[1]&63
            if (auxiliary and not extended) or (extended and length<1) or (not extended and
                    (pdu_type>6 or length<6 or length>37 or header_bytes[1]&0xc0)):
                stages['header_rejected']+=1
                continue
            stages['header_pass']+=1
            total=16+length*8+24
            if payload_start+total>len(bits):
                stages['truncated']+=1
                continue
            decoded=whitening(bits[payload_start:payload_start+total],channel)
            packet=np.packbits(decoded,bitorder="little").tobytes()
            pdu,crc=packet[:-3],packet[-3:]
            if BTLE.compute_crc(pdu,init=0x555555)!=crc:
                stages['crc_failed']+=1
                continue
            stages['crc_pass_trials']+=1
            extra={}
            if extended:
                try:extra=extended_header(pdu[2:])
                except ValueError:
                    stages['extended_header_rejected']+=1
                    continue
            # Offset trials of the same received packet must not count as four packets.
            sample=(offset+4*int(start))
            signature=packet.hex()
            if any(sig==signature and abs(pos-sample)<=8 for sig,pos in seen):
                stages['duplicate_trials']+=1
                continue
            seen.add((signature,sample))
            if auxiliary:
                preamble_symbols=16 if symbol_rate==2e6 else 8
                extra.update(subband_sample_start=max(0,sample-4*preamble_symbols),
                    subband_sample_stop=min(len(x),sample+4*(32+total)),
                    occupied_bandwidth_hz=meta['requested_span_hz'],
                    bandwidth_kind='decoder_extraction_passband_not_measured_occupied_bandwidth',
                    timing_status='hard_symbol_sync_estimate',
                    timing_uncertainty_samples=8,timing_uncertainty_calibrated=False,
                    auxiliary_subtype='unresolved_without_chain_context')
            frame=dict(protocol="Bluetooth LE",profile=description if auxiliary else "LE1M extended primary advertising" if extended else "LE1M legacy advertising",
                channel=channel,center_hz=center,subband_sample_start=sample,
                pdu_type=pdu_type,payload_length=length,crc_pass=True,
                pdu_hex=pdu.hex(),crc_hex=crc.hex(),evidence_level="decoded")
            frame.update(extra);frames.append(frame)
    result=dict(artifact_id=artifact_id,protocol="Bluetooth LE",profile=description,
        decoder_version='advertising-v3',verification_profile=profile,
        channel=channel,frames=frames,valid_frames=len(frames),
        verification_stages=dict(counts=stages,unique_valid_frames=len(frames),
            strongest_stage='crc_pass' if frames else 'header_plausible' if stages['header_pass'] else 'access_address_candidate' if matched else 'no_sync_candidate',
            header_check='Advertising type/length plausibility and extended field bounds; not a header CRC',
            count_unit='Timing-phase trials; only unique_valid_frames counts deduplicated packets'),
        analysis_coverage=dict(input_samples=len(iq),processed_samples=len(x),
            complete=len(x)==len(iq) and not candidate_limit_reached,
            candidate_limit_reached=candidate_limit_reached),
        access_address_candidates=matched,simulated=meta.get("simulated",False),
        evidence_level="decoded" if frames else "unknown",
        warnings=["Auxiliary type7 alone does not distinguish AUX_ADV_IND/AUX_SCAN_RSP/AUX_CHAIN_IND; chain association requires separate timing/context evidence",
                  "No LE Coded, connected-data, periodic advertising or classic payload decoding; no automatic AuxPtr following",
                  "No valid frame does not establish absence; CFO/timing recovery is limited",
                  "IQ and packet metadata remain local"])
    aid=ctx.json_artifact("ble_verification",result)
    summary=dict(result)
    summary["frames"]=[{k:v for k,v in f.items() if k!="pdu_hex"} for f in frames[:10]]
    summary["frame_summary_truncated"]=len(frames)>10
    return dict(evidence_id=aid,**summary)


def verify(ctx,artifact_id,protocol,channel=37,duplex_mode='auto',ssb_scs_khz=30,ssb_pattern=None):
    if protocol in ('wifi_crafter_dsss','wifi_crafter_all'):
        from .wifi_crafter import verify as verify_crafter
        return verify_crafter(ctx,artifact_id,protocol)
    if protocol=='wifi_gr20_siso':
        from .wifi_modern import verify as verify_modern
        return verify_modern(ctx,artifact_id)
    if protocol=='nr_pbch':
        from .nr import verify as verify_nr
        return verify_nr(ctx,artifact_id,ssb_scs_khz,ssb_pattern,duplex_mode)
    if protocol=='lte_pbch':
        from .lte import verify as verify_lte
        return verify_lte(ctx,artifact_id,duplex_mode)
    if protocol=='ieee802154_oqpsk':
        from .ieee154 import verify as verify_ieee154
        return verify_ieee154(ctx,artifact_id,channel)
    if protocol in ('ble_1m_advertising','ble_aux_1m','ble_aux_2m'):
        return decode_ble(ctx,artifact_id,channel,protocol)
    if protocol=="wifi_legacy_ofdm":
        from .wifi import verify as verify_wifi
        return verify_wifi(ctx,artifact_id)
    return dict(status="unsupported",error_code="DEPENDENCY_MISSING",
                protocol=protocol,artifact_id=artifact_id,evidence_level="unknown",
                message="No validated decoder for this profile is installed; no protocol confirmation")
