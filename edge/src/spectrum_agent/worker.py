from __future__ import annotations
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from .common import ROOT,Context,DomainError,dump


def match(ctx,segmentation_id,prior_snapshot_id):
    segmentation=json.loads(Path(ctx.resolve(segmentation_id)["path"]).read_text())
    priors=json.loads(Path(ctx.resolve(prior_snapshot_id)["path"]).read_text())
    groups=[]
    for g in segmentation["signal_groups"]:
        candidates=[]
        for p in priors["candidates"]:
            overlap=min(g["freq_high_hz"],p["high_hz"])-max(g["freq_low_hz"],p["low_hz"])
            if overlap<=0: continue
            widths=p["typical_bandwidth_hz"]
            distance=min((abs(g["bandwidth_hz"]-w)/w for w in widths),default=2)
            candidates.append(dict(prior_id=p["id"],name=p["name"],
                ranking_score=round(1/(1+distance),3),
                supporting_evidence=["frequency range overlap",
                    "approximate bandwidth compatibility" if distance<.5 else "bandwidth needs further verification"],
                missing_evidence=[p["verification"]],status="unverified",
                sources=p["sources"]))
        candidates.sort(key=lambda c:c["ranking_score"],reverse=True)
        groups.append(dict(group_id=g["group_id"],sample_start=g["sample_start"],
            sample_stop=g["sample_stop"],freq_low_hz=g["freq_low_hz"],freq_high_hz=g["freq_high_hz"],
            candidates=candidates,protocol="unknown",evidence_level="detected"))
    value=dict(segmentation_id=segmentation_id,prior_snapshot_id=prior_snapshot_id,
               groups=groups,warning="Ranking scores are heuristic, not probabilities; overlap does not confirm protocol")
    aid=ctx.json_artifact("hypotheses",value)
    return dict(hypotheses_id=aid,groups=groups[:20],total_groups=len(groups),
                summary_truncated=len(groups)>20,warning=value["warning"])


def list_groups(ctx,segmentation_id,sort_by="strength",offset=0,limit=20):
    if not isinstance(offset,int) or offset<0 or not isinstance(limit,int) or not 1<=limit<=50:
        raise DomainError("INVALID_ARGUMENT","offset>=0 and limit1..50 required")
    keys={"strength":"snr_db","duration":"duration_s","bandwidth":"bandwidth_hz","time":"sample_start"}
    if sort_by not in keys:
        raise DomainError("INVALID_ARGUMENT","sort_by must be strength,duration,bandwidth,time")
    data=json.loads(Path(ctx.resolve(segmentation_id)["path"]).read_text())
    groups=sorted(data["signal_groups"],key=lambda x:x[keys[sort_by]],reverse=sort_by!="time")
    capture=ctx.resolve(data['capture_id'])
    metadata=json.loads(Path(capture['metadata_path']).read_text());rate=metadata['sample_rate_hz']
    groups=[dict({k:v for k,v in g.items() if k!='time_start_s'},
        file_time_start_s=g['sample_start']/rate,file_time_stop_s=g['sample_stop']/rate,
        hardware_clock_time_start_s=g.get('time_start_s'),
        time_reference='file_time fields are relative to saved IQ; hardware clock is a separate origin') for g in groups]
    return dict(groups=groups[offset:offset+limit],total_groups=len(groups),
                next_offset=offset+limit if offset+limit<len(groups) else None,
                grouping_note="Spectral tracks, not physical transmitters")


def report(ctx, stop_reason=None, stop_detail=None):
    from . import analysis_budget
    records=[json.loads(p.read_text()) for p in sorted((ctx.run/"index").glob("*.json"),key=lambda p:p.stat().st_mtime_ns)] if (ctx.run/"index").exists() else []
    segmentations=[];verified=[];captures=[];priors=[];features=[];reviews=[];refinements=[];generic=[]
    decisions=[];comparisons=[];overviews=[]
    for rec in records:
        if rec.get('kind') in ('segmentation_selection','segmentation_comparison','spectrum_overview'):
            value=json.loads(Path(rec['path']).read_text());value['evidence_id']=rec['artifact_id']
            {'segmentation_selection':decisions,'segmentation_comparison':comparisons,'spectrum_overview':overviews}[rec['kind']].append(value)
        if rec.get('kind') in ('ofdm_structure','iq_quality','burst_analysis','sweep_analysis','activity_analysis','bluetooth_classic_analysis','ble_aux_followup'):
            value=json.loads(Path(rec['path']).read_text());value['evidence_id']=rec['artifact_id'];value['kind']=rec['kind']
            generic.append(value)
        if rec.get("kind")=="capture":
            captures.append(dict(artifact_id=rec["artifact_id"],metadata=json.loads(Path(rec["metadata_path"]).read_text())))
        elif rec.get("kind") in ("segmentation","ble_verification","wifi_verification","ieee154_verification","lte_verification","nr_verification","priors","features","scene_review","scene_refinement"):
            value=json.loads(Path(rec["path"]).read_text())
            value["evidence_id"]=rec["artifact_id"]
            {"segmentation":segmentations,"ble_verification":verified,"wifi_verification":verified,"ieee154_verification":verified,"lte_verification":verified,"nr_verification":verified,"priors":priors,"features":features,"scene_review":reviews,"scene_refinement":refinements}[rec["kind"]].append(value)
    from .frame_localization import apply
    verified=apply(ctx,verified)
    # The report is a deterministic evidence projection. A model cannot create decoded records.
    result=dict(task_id=ctx.task_id,captures=captures,prior_snapshots=priors,
        segmentations=segmentations,verified_protocols=[v for v in verified if v["valid_frames"]>0],
        verification_attempts=verified,features=features,
        scene_reviews=reviews,scene_refinements=refinements,generic_signal_analysis=generic,
        limitations=["Installed experimental profiles: legacy20MHz Wi-Fi OFDM plus complementary GR-WiFi20MHz SISO HT(nonaggregated)/VHT MCS0..7 offline-tested; no wideband/MIMO/b/ax/be. BLE LE1M primary and non-periodic LE1M/LE2M auxiliary advertising (no recursive automatic chain following),2.4GHz IEEE802.15.4 O-QPSK, LTE downlink PBCH/MIB, and NR FR1 SSB/PBCH/MIB15kHz/A,30kHz/B,C; other PHYs remain unsupported",
                     "libcrafter adds DSSS/CCK and HT20 A-MPDU single-data-stream BCC/LDPC receive; CS8 quantization/passband can reduce sensitivity; no HT40/VHT/HE/EHT in libcrafter",
                     "Generic features do not imply protocol confirmation",
                     "Detection completeness and absolute RF power are not calibrated",
                     "Classic Bluetooth access-code analysis is available as candidate evidence only; full classic payload decoding is not validated"])
    result['frequency_scope']=None
    intent_request=ctx.run/'request.json'
    result['task_intent']=json.loads(intent_request.read_text()).get('task_intent') if intent_request.exists() else None
    from .protocol_batch import summary as protocol_summary
    result['protocol_coverage']=protocol_summary(ctx)
    from .drone_video import project as drone_project
    result['drone_video_evidence']=drone_project(ctx)
    from .drone_hopping import project as hopping_project,coverage_complete as drone_complete
    result['drone_hopping_evidence']=hopping_project(ctx)
    intent=result['task_intent'] or {}
    require_protocol=intent.get('protocol_workflow_version')==2 and intent.get('protocol_analysis_requested')
    intent_only=json.loads(intent_request.read_text()).get('intent_only') if intent_request.exists() else False
    if intent.get('drone_video_analysis_requested') and not intent_only and stop_reason=='requested_checks_complete':
        if not drone_complete(result,intent):
            tool='analyze_drone_hopping' if intent.get('drone_hopping_analysis_requested') else 'analyze_drone_video_link'
            raise DomainError('DRONE_ANALYSIS_INCOMPLETE','Finish '+tool+' over the full intent interval,or report partial; signature evidence is not payload CRC')
    if require_protocol and not intent_only and stop_reason=='requested_checks_complete' and not result['protocol_coverage']['complete']:
        raise DomainError('PROTOCOL_COVERAGE_INCOMPLETE',json.dumps(result['protocol_coverage'])+' Create and finish a model-selected protocol plan; segmentation/feature review alone is not protocol coverage.')
    result['spectrograms']=[dict(evidence_id=r['artifact_id'],capture_id=json.loads(Path(r['path']).read_text())['capture_id']) for r in records if r.get('kind')=='spectrogram']
    if (result['task_intent'] or {}).get('visualization_requested') and not result['spectrograms']:
        raise DomainError('VISUALIZATION_INCOMPLETE','Requested plot missing: call plot_spectrogram before build_report')
    if captures:
        from .frequency_scope import effective_metadata
        result['frequency_scope']=effective_metadata(ctx,captures[0]['metadata'])['frequency_scope']
    if captures and all(c["metadata"].get("simulated") for c in captures):
        result["data_origin"]="synthetic"
    else:
        result["data_origin"]="rf" if captures else "no_capture"
    # Alternatives must never be concatenated and counted as extra observed activity.
    coarse_initial=next((s for s in segmentations if s.get('segmentation_level')=='coarse_activity' and not s.get('fused_from')),None)
    initial=coarse_initial or (segmentations[0] if segmentations else None)
    selected_id=decisions[-1]['segmentation_id'] if decisions else (initial['evidence_id'] if initial else None)
    selected=next((s for s in segmentations if s['evidence_id']==selected_id),None)
    result['initial_segmentation_id']=initial['evidence_id'] if initial else None
    result['selected_segmentation_id']=selected_id
    result['segmentation_level']=selected.get('segmentation_level','fine_detection') if selected else None
    result['coarse_to_fine']=dict(coarse_segmentation_count=sum(s.get('segmentation_level')=='coarse_activity' and not s.get('fused_from') for s in segmentations),
        initial_coarse_regions=len(coarse_initial['signal_groups']) if coarse_initial else 0,
        locally_reviewed_regions=len({(r['parent_segmentation_id'],r['parent_group_id']) for r in refinements}),
        fine_events=sum(len(r['mapped_events']) for r in refinements),
        note='Coarse envelopes, local fine events and decoded frames are separate levels, never additive signal counts.')
    result['segmentation_selections']=decisions
    result['boundary_fusion']=selected.get('fusion') if selected else None
    result['boundary_fusion_proposals']=[dict(segmentation_id=s['evidence_id'],**s['fusion']) for s in segmentations if s.get('fusion')]
    result['local_refinement_comparisons']=[dict(json.loads(Path(rec['path']).read_text()),evidence_id=rec['artifact_id']) for rec in records if rec.get('kind')=='local_refinement_comparison']
    result['boundary_composition']=selected.get('boundary_composition','coarse' if coarse_initial else 'fine') if selected else None
    result['segmentation_comparisons']=comparisons
    result['spectrum_overviews']=overviews
    result['segmentation_selection_status']='explicit_model_selection' if decisions else 'initial_only_unselected'
    if decisions:
        compared=next(c for c in comparisons if c['evidence_id']==decisions[-1]['comparison_id'])
        if sorted(v['evidence_id'] for v in verified)!=compared.get('verification_evidence_ids',[]):
            result['segmentation_selection_status']='needs_reassessment_after_new_protocol_evidence'
    result["signal_groups"]=selected['signal_groups'] if selected else []
    result["unknown_events"]=[g for g in result["signal_groups"] if g["protocol"]=="unknown"]
    from .catalog import associate
    result["frame_associations"] = associate(ctx,[selected] if selected else [],verified)
    from .header_evidence import project as project_headers
    result['wifi_header_evidence']=project_headers(ctx,verified)
    from .header_evidence import associate as associate_headers
    result['wifi_header_groups']=associate_headers(result['wifi_header_evidence'],captures[0]['metadata']['sample_rate_hz']) if captures else []
    from .presentation import segmentation_quality
    result['segmentation_quality']=segmentation_quality(result)
    result['feedback_loop']=dict(review_count=len(reviews),refinement_count=len(refinements),
        reassessed_after_refinement=any(r.get('refinements') for r in reviews),
        accuracy_improvement='not_measured',
        policy='Harness selects a compared working full-band hypothesis; all initial/alternative evidence is retained. Local alternatives never automatically replace it')
    result['unknown_investigation']=None
    result['catalog_review']=None
    if coarse_initial:
        from .investigation import project
        result['unknown_investigation']=project(ctx,coarse_initial['evidence_id'])
        from .coverage import progress
        result['catalog_review']=progress(ctx,coarse_initial['evidence_id'])
        request_path=ctx.run/'request.json'
        request=json.loads(request_path.read_text()) if request_path.exists() else {}
        needs_local=(not require_protocol and not intent.get('drone_video_analysis_requested')) or intent.get('full_local_review_requested')
        if needs_local and request.get('budgets',{}).get('coverage_mode')=='complete' and stop_reason=='requested_checks_complete' and not result['catalog_review']['all_regions_reviewed']:
            raise DomainError('ANALYSIS_INCOMPLETE',json.dumps(result['catalog_review'])+' Continue review_activity_batch; unfinished catalog cannot be declared complete.')
    result["analysis_coverage"] = dict(
        detected_groups=len(result["signal_groups"]),profile_artifacts=len(features),
        protocol_verification_attempts=len(verified),
        verified_frame_count=sum(v["valid_frames"] for v in verified),
        associated_frame_observations=len(result['frame_associations']),
        duplicate_packet_evidence_count=sum(len(l.get('duplicate_evidence',[])) for l in result['frame_associations']),
        validated_broadcast_blocks=sum(v.get("validated_broadcast_blocks",0) for v in verified),
        validated_packet_frames=sum(v["valid_frames"] for v in verified if v.get("integrity_unit")!="broadcast_block"),
        scene_review_count=len(reviews),local_refinement_count=len(refinements),
        generic_analysis_count=len(generic),
        note="Frame associations distinguish packet overlap from extraction-window overlap. Candidate associations never label an entire group; unknown groups remain, not physical-emitter counts.")
    if stop_reason is not None or stop_detail is not None:
        analysis_budget.record_stop(ctx, stop_reason, stop_detail)
    result['analysis_budget']=analysis_budget.summary(ctx)
    continuation_path=ctx.run/'continuation.json'
    result['continuation']=json.loads(continuation_path.read_text()) if continuation_path.exists() else None
    replay_path=ctx.run/'recording-replay.json'
    result['recording_replay']=json.loads(replay_path.read_text()) if replay_path.exists() else None
    if coarse_initial:
        reviewed={r['parent_group_id'] for r in refinements if r['parent_segmentation_id']==coarse_initial['evidence_id']}
        result['analysis_budget'].update(initial_coarse_regions=len(coarse_initial['signal_groups']),
            coarse_parents_with_local_checks=len(reviewed),
            coarse_parents_without_local_checks=sum(g['group_id'] not in reviewed for g in coarse_initial['signal_groups']),
            coverage_note='A parent with local checks may still contain unchecked time/frequency area; this is not full-region recognition coverage.')
    result["artifact_ids"]=[r["artifact_id"] for r in records]
    dump(ctx.run/"report.json",result)
    budget=result['analysis_budget']
    budget_lines=['## Local-analysis budget and stopping record',
        f"- Attempts: {budget['local_attempts']}/{budget['max_local_refinements']}; distinct reviewed parents: {budget['distinct_locally_reviewed_regions']}",
        f"- Stop constraint: {budget['local_stop_constraint']}; model stopping rationale: {json.dumps(budget['declared_stop'],ensure_ascii=False)}",
        '- Reviewed local windows are partial checks; unresolved and untested regions remain unknown.']
    lines=["# 频谱分析报告",f"任务：{ctx.task_id}",f"数据来源：{result['data_origin']}",
        "## 观察范围"]
    for c in captures:
        m=c["metadata"];f=m["center_hz"];span=m["requested_span_hz"]
        lines.extend([f"- IQ: {c['artifact_id']}",
            f"- 中心 {f/1e6:.6f} MHz，范围 {(f-span/2)/1e6:.6f}–{(f+span/2)/1e6:.6f} MHz",
            f"- 采样率 {m['sample_rate_hz']/1e6:.3f} MS/s，实际观察 {m['duration_s']:.6f} 秒",
            f"- 丢样溢出 {m.get('overflow_count',0)}，连续片段 {len(m['segments'])}"])
    lines.extend(["## 信号目录（谱迹不等于发射机）",
        "|ID|频率 MHz|时长 ms|带宽 MHz|SNR估计 dB|协议|",
        "|---|---|---|---|---|---|"])
    for s in ([selected] if selected else []):
        for g in s["signal_groups"]:
            lines.append(f"|{g['group_id']}|{g['freq_low_hz']/1e6:.5f}–{g['freq_high_hz']/1e6:.5f}|{g['duration_s']*1000:.3f}|{g['bandwidth_hz']/1e6:.4f}|{g['snr_db']:.1f}|unknown|")
    lines.append('## Harness 分割决策与候选对比')
    lines.append('工作分割：'+str(selected_id)+'；初始分割：'+str(result['initial_segmentation_id']))
    lines.append('策略由模型通过工具选择。替代分割不重复计入活动总数；准确率改善尚未测量。')
    for decision in decisions:
        lines.append(f"- {decision['evidence_id']}：采用 {decision['segmentation_id']}；对比 {decision['comparison_id']}；理由：{decision['reason']}")
    if not decisions: lines.append('没有显式选择，保留初始分割作为工作假设，不声称已经优化。')
    for comparison in comparisons:
        for candidate in comparison['candidates']:
            lines.append(f"- {comparison['evidence_id']} / {candidate['segmentation_id']}：{candidate['strategy']}，FFT {candidate['fft_size']}，门限 {candidate['threshold_db']} dB，区域 {candidate['group_count']}，短于100μs {candidate['short_under_100us_count']}，有效包重叠 {candidate['packet_overlap_count']}/{candidate['packet_evidence_count']}")
    lines.append("## 协议验证")
    for v in verified:
        lines.append(f"- {v['protocol']} / {v['profile']}: {v['valid_frames']} 个有效校验帧，证据 {v['evidence_id']}")
    if not result["verified_protocols"]:
        lines.append("没有协议通过当前插件验证；这不等于该频段没有协议活动。")
    lines.extend(["## 解码帧与谱迹候选关联",
        "关联不改变整条谱迹的协议标签；extraction_window_only 仅表示同一提取窗口。",
        "|证据/帧序号|协议|关联层级|原始采样区间|候选谱迹数|",
        "|---|---|---|---|---|"])
    for link in result["frame_associations"]:
        lines.append(f"|{link['evidence_id']}/{link['frame_index']}|{link['protocol']}|{link['association_level']}|{link['sample_start']}–{link['sample_stop']}|{len(link['candidate_groups'])}|")
    lines.append("## 分割与识别反馈迭代")
    for item in refinements:
        lines.append(f"- {item['evidence_id']}: 复查 {item['parent_group_id']}；依据 {item['feedback_evidence_ids']}；原因 {item['reason']}；局部候选事件 {len(item['mapped_events'])} 个，初始结果保留。")
        if item.get('quality_comparison',{}).get('fragmentation_warning'):
            lines.append("  - 局部结果触发碎片增多告警，未据此替换初始分割；准确率改善尚未测量。")
    if not refinements:
        lines.append("本任务没有执行局部重分割；不得视为闭环质量已验证。")
    lines.append('## 同范围局部候选比较')
    for comparison in result['local_refinement_comparisons']:
        lines.append(f"- {comparison['evidence_id']}：父区域 {comparison['parent_group_id']}；目标采样区间 {comparison['requested_parent_interval']}；人工参考待标注，准确率未测量。")
        for c in comparison['candidates']:
            lines.append(f"  - {c['strategy']}：FFT {c['fft_size']}，门限 {c['threshold_db']}dB；目标候选 {c['target_events']}，含上下文候选 {c['context_events']}；已知包参考 {c['quality']['known_packet_reference_count']}，未覆盖 {c['quality']['known_packet_unmatched_count']}。")
    lines.append('## 局部边界融合')
    for proposal in result['boundary_fusion_proposals']:
        lines.append(f"- 方案 {proposal['segmentation_id']}：采用 {proposal['accepted_parent_count']} 个父区域的局部几何；拒绝 {proposal['rejected_parent_count']} 个；未解释范围保留为 unknown 残余，不代表新增信号。")
        for d in proposal['decisions']:
            lines.append(f"  - 父区域 {d['parent_group_id']}：{d['status']}；细候选 {d['fine_candidate_count']}，采用细边界 {d['adopted_fine_count']}，残余区域 {d['residual_count']}。")
    if not result['boundary_fusion_proposals']:lines.append('本任务未提出局部边界融合方案。')
    lines.append('## 未知区域复查队列')
    investigation=result.get('unknown_investigation')
    if investigation:
        lines.append(f"原始粗区域 {investigation['total_regions']} 个，暂缓 {investigation['deferred_count']} 个。相似活动族仅用于安排代表样本，不是设备或协议归类。")
        for row in investigation['regions']:
            d=row.get('decision')
            if d:
                lines.append(f"- {row['group_id']}：{row['investigation_state']}；{d['action']}；{d['reason']}；下一步/缺少证据：{d['next_step']}；决定是否过期：{row['decision_stale']}")
        lines.append('### 协议尝试窗口（去重）')
        seen=set()
        for row in investigation['regions']:
            for attempt in row['verification_attempts']:
                if attempt['evidence_id'] in seen:continue
                seen.add(attempt['evidence_id']);a=attempt['scope'];args=attempt.get('arguments',{})
                lines.append(f"- {attempt['evidence_id']}：{args.get('protocol',attempt.get('protocol'))}；{attempt['status']}；校验帧 {attempt['valid_frames']}；原IQ采样 {a['sample_start']}–{a['sample_stop']}；频率 {a['freq_low_hz']/1e6:.6f}–{a['freq_high_hz']/1e6:.6f}MHz。仅限该协议及窗口，不能据此确认或排除整个区域。")
    else:lines.append('没有可用的粗分割队列。')
    lines.append('## 通用信号画像与数据质量')
    for item in generic:
        lines.append(f"- {item['kind']}: {item['evidence_id']}；证据级别 {item['evidence_level']}，不代表已确认协议。")
    if not generic: lines.append('本任务未执行新增通用画像工具，未知信号仍需进一步分析。')
    lines.extend(["## 限制"]+["- "+x for x in result["limitations"]])
    lines.extend(["## 证据工件"]+["- "+r["artifact_id"]+" ("+r.get("kind","unknown")+")" for r in records])
    if result['frequency_scope']:
        scope=result['frequency_scope']
        lines.extend(['## 频率范围',
            f"- 数字采样范围：{scope['digital_low_hz']/1e6:.6f}–{scope['digital_high_hz']/1e6:.6f} MHz；采样率 {scope['sample_rate_hz']/1e6:g} MS/s。",
            f"- 任务分析范围：{scope['analysis_low_hz']/1e6:.6f}–{scope['analysis_high_hz']/1e6:.6f} MHz。",
            f"- 模拟前端标称带宽：{scope['frontend_nominal_bandwidth_hz']} Hz；来源 {scope['frontend_source']}；边缘及平坦度未标定。"])
    drone_lines=[]
    if result['drone_video_evidence']:
        drone_lines=['## 图传样本特征验证','参考序列匹配与 MAC/广播 CRC 校验分别记录；不是视频解码或独立机型确认。',
            '|中心 MHz|匹配参考符号|覆盖单元|重复间隔 ms|名义通道 MHz|参考活跃 MHz|证据|','|---|---:|---|---:|---:|---:|---|']
        for v in result['drone_video_evidence']:
            coverage=v['coverage'];period=v.get('estimated_repetition_s')
            p=v['profile']
            drone_lines.append(f"|{v['center_hz']/1e6:.6f}|{v['matched_references']}|{coverage['completed_units']}/{coverage['total_units']}|{period*1000 if period else '未确定'}|{p['nominal_channel_bandwidth_hz']/1e6:g}|{p['zc_length']*p['subcarrier_spacing_hz']/1e6:g}|{v['evidence_id']}|")
        drone_lines+=['名义通道框来自所选样本配置；参考活跃带宽按参考子载波网格计算，不代表完整突发的校准占用带宽。关联能量包络不是解码帧边界，功率未校准为 dBm。',
            'DJI/Mavic4 Pro 来源为用户现场确认；当前特征仅由该记录训练验证，不是所有 DJI/O4+ 模式的通用解码支持。']
    if result['drone_hopping_evidence']:
        drone_lines+=['','## 无人机变频/变带宽参考检查','参考特征不是协议CRC或视频解码；频率/模式的时间关联不证明同一架无人机。']
        for v in result['drone_hopping_evidence']:
            drone_lines.append(f"- {v['evidence_id']}：{v['matched_references']}处参考匹配；时间块{v['coverage']['completed_units']}/{v['coverage']['total_units']}；参考检查{v['coverage']['reference_checks']}次；完整性{v['coverage']['complete']}。")
            drone_lines.append('模式观测计数：'+json.dumps(v['sequence']['bandwidth_observation_counts'],ensure_ascii=False))
            drone_lines.append('频率候选：'+json.dumps(v['sequence']['center_clusters'],ensure_ascii=False))
            drone_lines+=v['limitations']
    (ctx.run/"report.md").write_text("\n".join(lines+drone_lines+budget_lines)+"\n",encoding="utf-8")
    timing(ctx)
    return dict(report_path="runs/"+ctx.task_id+"/report.md",
                frequency_scope=result['frequency_scope'],
                result_path="runs/"+ctx.task_id+"/report.json",
                timing_path="runs/"+ctx.task_id+"/timing.md",
                total_groups=len(result["signal_groups"]),
                capture_metrics=[{k:c["metadata"].get(k) for k in
                    ("center_hz","requested_span_hz","sample_rate_hz","sample_count","duration_s","complete","overflow_count")}
                    for c in captures],
                verified_protocols=[dict(protocol=v["protocol"],valid_frames=v["valid_frames"],evidence_id=v["evidence_id"])
                                    for v in result["verified_protocols"]],
                verified_protocol_count=len(result["verified_protocols"]),data_origin=result["data_origin"],
                limitations=result["limitations"])


def timing(ctx):
    events=[json.loads(l) for l in (ctx.run/"events.jsonl").read_text().splitlines()] if (ctx.run/"events.jsonl").exists() else []
    spans=[e for e in events if e.get("phase")=="end"]
    dump(ctx.run/"timing.json",dict(task_id=ctx.task_id,spans=spans,
        note="Parallel spans must not be summed as wall time; provider TTFT unavailable until instrumented"))
    lines=["# 各阶段耗时", "|阶段|耗时 ms|状态|","|---|---:|---|"]
    for e in spans:
        lines.append(f"|{e['stage']}|{e['elapsed_ms']:.3f}|{e['status']}|")
    lines.append("并行耗时不直接相加；模型耗时在supervisor结束后补充。TTFT暂不可用。")
    (ctx.run/"timing.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def dispatch(ctx,name,args):
    from .drone_video import analyze as drone_video_analyze,list_profiles as drone_video_profiles
    from .drone_hopping import analyze as drone_hopping_analyze
    from .signal_batch import analyze as signal_batch
    from .protocol_batch import plan as protocol_plan,run as protocol_run,progress as protocol_progress
    from .window_analysis import analyze as window_analyze
    from .ofdm_structure import analyze as ofdm_analyze
    from .wifi_modern import verify as inspect_headers
    from .visualization import plot
    from .task_intent import resolve
    from .ble_followup import follow
    from . import acquisition,dsp,knowledge,protocols,scene,analysis_tools,segmentation_tools,coarse,fusion,investigation,local_review,bluetooth_classic
    from .coverage import review_batch
    from .frame_localization import localize
    functions=dict(analyze_drone_hopping=drone_hopping_analyze,list_drone_video_profiles=drone_video_profiles,analyze_drone_video_link=drone_video_analyze,analyze_signal_windows=signal_batch,plan_protocol_checks=protocol_plan,run_protocol_batch=protocol_run,inspect_protocol_progress=protocol_progress,inspect_wifi_headers=inspect_headers,analyze_spectrum_windows=window_analyze,analyze_ofdm_structure=ofdm_analyze,plot_spectrogram=plot,resolve_task_intent=resolve,follow_ble_aux=follow,localize_verified_frames=localize,review_activity_batch=review_batch,inspect_capabilities=acquisition.capabilities,
        query_band_priors=knowledge.query,make_synthetic_capture=dsp.synthetic,
        capture_iq=acquisition.capture,segment_spectrum=dsp.segment,
        coarse_segment_spectrum=coarse.segment,
        fuse_refined_regions=fusion.fuse,
        inspect_spectrum=segmentation_tools.overview,compare_segmentations=segmentation_tools.compare,
        select_segmentation=segmentation_tools.select,consolidate_signal_regions=segmentation_tools.consolidate,
        extract_subband=dsp.extract,profile_signal=dsp.features,
        list_signal_groups=list_groups,
        match_signal_hypotheses=match,verify_protocol=investigation.verify,
        plan_unknown_investigations=investigation.plan,record_investigation_decision=investigation.decide,
        compare_local_refinements=local_review.compare,
        assess_signal_scene=scene.assess,refine_signal_region=scene.refine,
        analyze_iq_quality=analysis_tools.quality,analyze_burst_timing=analysis_tools.bursts,
        analyze_sweep_structure=analysis_tools.sweeps,summarize_spectral_activity=analysis_tools.activity,
        analyze_bluetooth_classic=bluetooth_classic.analyze,
        export_sigmf_recording=analysis_tools.export_sigmf,build_report=report)
    if name not in functions:
        raise DomainError("UNKNOWN_TOOL","No registered domain tool")
    return functions[name](ctx,**args)


def execute(task_id,name,args,call_id=None):
    ctx=Context(task_id)
    # Serialized by per-task tool lock; side-effect retries use exact argument fingerprints.
    import fcntl
    with (ctx.run/"tool.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        key=hashlib.sha256(json.dumps([name,args],sort_keys=True).encode()).hexdigest()
        cache=ctx.run/"tool-cache"/(key+".json")
        ctx.check()
        if cache.exists() and name not in ('analyze_drone_hopping','analyze_drone_video_link','run_protocol_batch','inspect_protocol_progress','plan_protocol_checks','analyze_spectrum_windows','assess_signal_scene','build_report','plan_unknown_investigations','review_activity_batch'):
            value=json.loads(cache.read_text());value["cached"]=True
            from .analysis_budget import snapshot
            value['analysis_budget']=snapshot(ctx,compact=True)
            return value
        request=json.loads((ctx.run/"request.json").read_text()) if (ctx.run/"request.json").exists() else {}
        if request.get('rf_capture_forbidden') and name in ('capture_iq','make_synthetic_capture','inspect_capabilities'):
            raise DomainError('REPLAY_ONLY','Existing-data request forbids new acquisition, including source lookup failure')
        if request.get('task_intent',{}).get('status')=='pending_model' and name not in ('resolve_task_intent','build_report'):
            raise DomainError('TASK_INTENT_REQUIRED','Interpret the full user prompt with resolve_task_intent before observation or DSP')
        if request.get('intent_only') and name not in ('resolve_task_intent','build_report'):
            raise DomainError('INTENT_ONLY','Interpretation acceptance forbids hardware and signal-processing tools')
        if name=='segment_spectrum' and request.get('segmentation_workflow')=='coarse_to_fine':
            rec=ctx.resolve(args.get('capture_id'))
            if rec.get('kind')=='capture':
                coarse_exists=any(json.loads(Path(json.loads(p.read_text())['path']).read_text()).get('segmentation_level')=='coarse_activity'
                    for p in (ctx.run/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='segmentation')
                if not coarse_exists:
                    raise DomainError('COARSE_FIRST','Run coarse_segment_spectrum on the whole recording before fine segmentation')
        if request.get('recording_capture_id') and name in ('capture_iq','make_synthetic_capture','inspect_capabilities'):
            raise DomainError('REPLAY_ONLY','Recorded-IQ replay cannot access hardware or create another capture')
        if name=="capture_iq" and request.get("source")=="synthetic":
            raise DomainError("TASK_CONSTRAINT","Synthetic tasks cannot start RF capture")
        if name=="make_synthetic_capture" and request.get("source")=="usrp":
            raise DomainError("TASK_CONSTRAINT","RF tasks cannot substitute synthetic data")
        if name=="capture_iq":
            for p in (ctx.run/"index").glob("*.json"):
                if json.loads(p.read_text()).get("kind")=="capture":
                    raise DomainError("CAPTURE_ALREADY_COMPLETED","Reuse the existing capture; do not repeat with changed arguments")
        if name=='refine_signal_region':
            from .analysis_budget import enforce
            enforce(ctx,args)
        if name in ('segment_spectrum','consolidate_signal_regions','coarse_segment_spectrum','fuse_refined_regions'):
            # Resource bound only; no predetermined strategy or parameter routing.
            path=ctx.run/'events.jsonl'
            count=sum(e.get('stage')==name and e.get('phase')=='start' for e in
                (json.loads(line) for line in path.read_text().splitlines())) if path.exists() else 0
            if count>=(3 if name=='segment_spectrum' else 2):
                raise DomainError('RESOURCE_LIMIT','At most three full-band segmentation attempts or two consolidation proposals; compare evidence and select a working hypothesis')
        if name in ("follow_ble_aux","extract_subband","profile_signal","verify_protocol","analyze_iq_quality","analyze_burst_timing","analyze_sweep_structure","analyze_bluetooth_classic"):
            max_candidates=request.get("budgets",{}).get("max_candidates",20)
            previous=0
            for p in (ctx.run/"tool-cache").glob("*.json"):
                if json.loads(p.read_text()).get("tool")==name: previous+=1
            if previous>=max_candidates*2:
                raise DomainError("RESOURCE_LIMIT","Deep-analysis tool budget exhausted; keep remaining events in report")
        with ctx.span(name,call_id=call_id):
            result=dispatch(ctx,name,args)
        value=dict(status="succeeded",task_id=task_id,tool=name,result=result,cached=False)
        if name not in ('run_protocol_batch','inspect_protocol_progress','plan_protocol_checks','build_report','assess_signal_scene','review_activity_batch'):
            dump(cache,value)
        from .analysis_budget import snapshot
        value['analysis_budget']=snapshot(ctx,compact=True)
        return value


def main():
    request=json.loads(sys.stdin.read(2_000_000))
    ctx=Context(request["task_id"])
    try:
        result=execute(request["task_id"],request["tool"],request.get("arguments",{}),request.get("call_id"))
    except DomainError as exc:
        result=dict(status="failed",error_code=exc.code,message=str(exc))
    except Exception as exc:
        # Full stack trace stays in the task directory, not in the model response.
        with (ctx.run/"worker-errors.log").open("a") as out:
            traceback.print_exc(file=out)
        result=dict(status="failed",error_code=type(exc).__name__,message="Tool failed; inspect local worker-errors.log")
    if 'analysis_budget' not in result:
        from .analysis_budget import snapshot
        result['analysis_budget']=snapshot(ctx,compact=True)
    print("SPECTRUM_RESULT:"+json.dumps(result,ensure_ascii=False,allow_nan=False))


if __name__=="__main__":
    main()
