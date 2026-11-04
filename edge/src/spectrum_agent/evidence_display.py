"""User-visible decoder stages; missing telemetry must remain unknown."""


def stage_notes(value):
    stage=value.get('verification_stages') or {}
    counts=stage.get('counts') or {}
    notes=[]
    if stage:
        labels={'crc_pass':'CRC通过','header_plausible':'头部结构检查通过（非头部CRC）',
            'mac_fcs_pass':'MAC FCS通过','phy_header_pass':'PHY头部检查通过',
            'no_valid_frame':'未输出有效帧（结合下列诊断判断）',
            'lsig_pass':'传统L-SIG检查通过','lsig_failed':'传统L-SIG检查未通过','no_header_candidate':'未到达PHY头部检查阶段',
            'access_address_candidate':'发现接入地址候选','no_sync_candidate':'未找到接入地址候选'}
        notes.append('接收阶段：'+labels.get(stage.get('strongest_stage'),stage.get('strongest_stage','未知')))
        fields=(('lsig_pass','L-SIG通过'),('lsig_failed','L-SIG失败'),('ht_sig_pass','HT-SIG通过'),
            ('vht_sig_a_pass','VHT-SIG-A通过'),('fcs_pass','MAC FCS通过'),('fcs_failed','MAC FCS失败')) if value.get('verification_profile')=='wifi_gr20_siso' or any(k in counts for k in ('lsig_pass','lsig_failed','fcs_pass')) else (('access_address_candidates','接入地址候选'),('header_rejected','头部结构不符'),
            ('header_pass','头部结构通过'),('truncated','数据不完整'),('crc_failed','CRC失败'),
            ('extended_header_rejected','扩展头结构不符'),('unique_valid_frames','去重有效包'))
        crafter=str(value.get('verification_profile','')).startswith('wifi_crafter_') or 'invalid_header' in counts
        if crafter:
            fields=(('ht_sig_pass','HT-SIG通过'),('invalid_header','头部校验失败诊断'),
                ('invalid_data','数据恢复失败诊断'),('invalid_fcs','MAC FCS失败诊断'),
                ('truncated_frame','截断诊断'),('unsupported_phy','不支持的PHY诊断'),
                ('fcs_pass','MAC FCS有效成员'),('ampdu_invalid_fcs','聚合成员FCS失败'),
                ('ampdu_invalid_delimiters','无效聚合分隔符'))
        notes.append('；'.join(f'{label} {stage.get(key,counts.get(key,0))}' for key,label in fields))
        notes.append('libcrafter输出的诊断次数，不是所有同步尝试的完整统计；聚合成员可共享PPDU时间。' if crafter else 'Wi-Fi按接收器头部尝试和MPDU校验次数计数，不代表设备数。' if any(k in counts for k in ('lsig_pass','lsig_failed','fcs_pass')) else '候选和失败数按定时相位尝试计数，同一包可能多次尝试；仅有效包数已去重。')
    elif value.get('protocol') in ('IEEE802.11','wifi_legacy_ofdm'):
        notes.append('Wi-Fi MAC FCS通过' if value.get('valid_frames',0)>0 else
            'Wi-Fi未输出FCS有效帧；当前未记录同步、PHY头部及FCS拒绝的分阶段计数，失败阶段未知。')
    coverage=value.get('analysis_coverage')
    if coverage:
        notes.append(f"IQ处理范围：{coverage.get('processed_samples',0)} / {coverage.get('input_samples',0)} 采样；"+
            ('该输入已完整处理' if coverage.get('complete') else '处理不完整，不能代表整个输入'))
    return notes
