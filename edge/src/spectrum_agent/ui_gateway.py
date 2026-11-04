"""Separate loopback-only visualization gateway; the official harness remains headless."""
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlparse
import httpx
import numpy as np
from fastapi import FastAPI,HTTPException,Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from .common import ROOT,Context,safe_id,dump
from .service import credentials
from .evidence_display import stage_notes

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(GZipMiddleware,minimum_size=1000,compresslevel=3)
UI=Path(os.environ.get('SPECTRUM_UI_DIR',str(ROOT/'ui/dist')))
TERMINAL={'succeeded','failed','partial','cancelled'}


async def proxy(path,method='GET',body=None):
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8765',timeout=15) as c:
        r=await c.request(method,path,json=body,headers={'Authorization':'Bearer '+credentials()['api_token']})
    if not r.is_success:
        try: detail=r.json()
        except Exception: detail='Task API unavailable'
        raise HTTPException(r.status_code,detail)
    return r.json()


def task_path(task_id):
    try: safe_id(task_id)
    except Exception: raise HTTPException(422,'Invalid task id')
    p=ROOT/'runs'/task_id
    if not p.is_dir(): raise HTTPException(404,'Task not found')
    return p


@app.middleware('http')
async def same_origin(request:Request,call_next):
    if request.method in ('POST','PUT','DELETE','PATCH'):
        origin=request.headers.get('origin')
        if (origin and urlparse(origin).netloc!=request.headers.get('host')) or request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse(status_code=403,content={'detail':'Same-origin requests only'})
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='no-store'
    return response


@app.get('/edge/health')
async def health():
    try:
        async with httpx.AsyncClient(timeout=3) as c: r=await c.get('http://127.0.0.1:8765/healthz')
        return dict(connected=r.is_success,device='Jetson-server-new',receive_only=True)
    except httpx.HTTPError: return dict(connected=False,device='Jetson-server-new')


@app.get('/edge/capabilities')
async def capabilities(): return await proxy('/v1/capabilities')


@app.post('/edge/tasks',status_code=202)
async def create(request:Request):
    body=await request.json()
    if body.get('source','usrp')!='usrp': raise HTTPException(422,'Real-device gateway accepts receive-only RF tasks')
    return await proxy('/v1/tasks','POST',body)


@app.get('/edge/tasks/{task_id}')
async def status(task_id:str):
    task_path(task_id)
    return await proxy('/v1/tasks/'+task_id)


@app.post('/edge/tasks/{task_id}/cancel')
async def cancel(task_id:str):
    task_path(task_id)
    return await proxy('/v1/tasks/'+task_id+'/cancel','POST')


@app.get('/edge/tasks')
def history():
    tasks=[]
    from .task_records import MARKER,removed
    reports=[p for p in (ROOT/'runs').glob('*/report.json') if not (p.parent/MARKER).exists()]
    for p in sorted(reports,key=lambda p:p.stat().st_mtime,reverse=True)[:250]:
        try:
            report=json.loads(p.read_text());capture=report.get('captures',[None])[0]
            if not capture or capture['metadata'].get('simulated'): continue
            m=capture['metadata'];outcome=p.parent/'outcome.json'
            out=json.loads(outcome.read_text()) if outcome.exists() else {}
            if not out: continue  # Completed user jobs, not partial test/replay folders.
            from .frequency_scope import effective_metadata
            scope=effective_metadata(Context(p.parent.name),m)['frequency_scope']
            tasks.append(dict(id=p.parent.name,center=scope['analysis_center_hz']/1e6,bandwidth=scope['analysis_span_hz']/1e6,
                date=out.get('completed_utc',''),status=out.get('status','unknown')))
            if len(tasks)>=30: break
        except (KeyError,ValueError,IndexError): continue
    database=ROOT/'runs/tasks.sqlite'
    if database.exists():
        db=sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True)
        try:
            known={t['id'] for t in tasks}
            for tid,request,status,created,idem in db.execute('SELECT id,request,status,created,idem FROM tasks ORDER BY created DESC'):
                value=json.loads(request)
                if tid in known or removed(tid) or value.get('source')!='usrp' or str(idem).startswith('api-test-'): continue
                c=value['constraints']
                tasks.append(dict(id=tid,center=c['center_hz']/1e6,bandwidth=c['span_hz']/1e6,date=created,status=status))
                if len(tasks)>=100:break
        finally: db.close()
    tasks.sort(key=lambda t:t.get('date',''),reverse=True)
    return dict(tasks=tasks[:30])


def waterfall(ctx,capture):
    cache=ctx.run/'ui-waterfall-v1.json'
    if cache.exists(): return json.loads(cache.read_text())
    rec=ctx.resolve(capture['artifact_id']);m=capture['metadata']
    iq=np.memmap(rec['path'],dtype='<c8',mode='r');fs=m['sample_rate_hz'];n=len(iq)
    if n<2048: return None
    nfft=2048;hop=1024;rows=192;columns=512
    image=np.full((rows,columns),1e-20);psd=np.zeros(columns);count=0;win=np.hanning(nfft)
    # Scan the recording in bounded batches, then max-pool time/frequency cells.
    # This is visualization after task completion, never protocol evidence.
    for segment in m['segments']:
        a=segment['file_start'];b=min(n,a+segment['count'])
        for offset in range(a,b-nfft+1,hop*128):
            data=np.asarray(iq[offset:min(offset+hop*127+nfft,b)])
            frames=np.lib.stride_tricks.sliding_window_view(data,nfft)[::hop]
            power=abs(np.fft.fftshift(np.fft.fft(frames*win,axis=1),axes=1))**2/max(float(np.sum(win**2)),1e-20)
            pooled=power.reshape(len(power),columns,4).max(axis=2)
            idx=np.minimum(rows-1,((offset+np.arange(len(power))*hop+nfft/2)/n*rows).astype(int))
            np.maximum.at(image,idx,pooled)
            psd+=power.reshape(len(power),columns,4).mean(axis=2).sum(axis=0);count+=len(power)
    db=10*np.log10(np.maximum(image,1e-20))
    measured=db[db>-190]
    low=float(np.percentile(measured,20)) if measured.size else -100
    high=max(low+25,float(np.percentile(measured,99.5))) if measured.size else -50
    value=dict(rows=rows,columns=columns,db=np.round(db,2).tolist(),psd_db=np.round(10*np.log10(np.maximum(psd/max(count,1),1e-20)),2).tolist(),
        low_mhz=(m['center_hz']-fs/2)/1e6,high_mhz=(m['center_hz']+fs/2)/1e6,min_db=low,max_db=high,
        coverage_note='全记录 STFT 最大池化预览；横轴频率，纵轴为文件采样时间',
        gaps=m.get('gaps',[]),fft_size=nfft,time_pooling_rows=rows,
        warning='Display power is relative and uncalibrated; pooled pixels do not replace original IQ or event evidence')
    dump(cache,value)
    return value


def drone_display_evidence(report):
    """Normalize agile per-reference modes without rewriting fixed evidence."""
    displayed=list(report.get('drone_video_evidence',[]))
    for hopping in report.get('drone_hopping_evidence',[]):
        for pid,parameters in hopping['profiles'].items():
            selected=[r for r in hopping['references'] if r['profile_id']==pid]
            if selected:displayed.append(dict(hopping,profile=parameters,references=selected,evidence_id=hopping['evidence_id']+'_'+pid))
    return displayed


@app.get('/edge/tasks/{task_id}/visual')
def visual(task_id:str):
    p=task_path(task_id);path=p/'report.json'
    if not path.exists(): raise HTTPException(409,'Task report is not ready')
    ctx=Context(task_id);report=json.loads(path.read_text());caps=report.get('captures',[])
    if not caps: raise HTTPException(409,'No recording available')
    capture=caps[0];m=capture['metadata'];fs=m['sample_rate_hz'];n=m.get('sample_count',sum(s['count'] for s in m['segments']))
    if m.get('simulated'): raise HTTPException(422,'Synthetic recordings cannot be displayed as real-device observations')
    if (report.get('task_intent') or {}).get('analysis_requested') is False and not (report.get('task_intent') or {}).get('visualization_requested'):
        from .frequency_scope import effective_metadata
        scope=effective_metadata(ctx,m)['frequency_scope']
        outcome=json.loads((p/'outcome.json').read_text()) if (p/'outcome.json').exists() else {}
        request=json.loads((p/'request.json').read_text()) if (p/'request.json').exists() else {}
        return dict(status=outcome.get('status','report_ready'),capture_only=True,
            prompt=request.get('prompt',''),frequency_scope=scope,
            center_mhz=scope['analysis_center_hz']/1e6,bandwidth_mhz=scope['analysis_span_hz']/1e6,
            duration_ms=n/fs*1000,event_count=0,protocol_count=0,protocol_names=[],
            refinement_count=0,consolidation_count=0,signals=[],raw_signals=[],activity_catalog=[],
            activity_count=0,evidence_summary=dict(verified_frames=0),
            stages=[[],[],[]],visual=None,timing=[],elapsed_s=outcome.get('elapsed_ms',0)/1000,
            recording=dict(capture_id=capture['artifact_id'],sample_count=n,complete=m.get('complete'),
                overflow_count=m.get('overflow_count',0),iq_path=ctx.resolve(capture['artifact_id'])['path']),
            note='IQ recording only; no STFT,segmentation or recognition was requested or generated by visualization.',report=report)
    out=json.loads((p/'outcome.json').read_text()) if (p/'outcome.json').exists() else {}
    working_seg=next((s for s in report.get('segmentations',[]) if s.get('evidence_id')==report.get('selected_segmentation_id')), {})
    is_coarse=working_seg.get('segmentation_level')=='coarse_activity'
    investigation=report.get('unknown_investigation') or {}
    investigation_rows={r['group_id']:r for r in investigation.get('regions',[])}
    def investigation_notes(row):
        labels={'no_valid_frames':'该协议窗口未得到有效帧','valid_frames':'窗口内存在有效帧，未确认整组','unsupported':'解码器不支持','tool_error':'验证工具报错'}
        notes=[]
        if row.get('catalog_review_completed'):
            notes.append('已完成该粗区域全部时间和频率范围的局部细查及通用画像；这不等于整区协议确认。')
        if row.get('unresolved_reason'):notes.append('保留未知的依据：'+row['unresolved_reason'])
        for a in row.get('verification_attempts',[])[:6]:
            area=a['scope'];args=a.get('arguments',{})
            notes.append(f"{args.get('protocol',a.get('protocol','协议'))}：{area['sample_start']/fs*1000:.3f}–{area['sample_stop']/fs*1000:.3f}ms，{area['freq_low_hz']/1e6:.3f}–{area['freq_high_hz']/1e6:.3f}MHz；{labels.get(a['status'],a['status'])}（{a.get('valid_frames',0)}帧）")
            notes.extend(stage_notes(dict(a,protocol=args.get('protocol',a.get('protocol')))))
        d=row.get('decision')
        if d:notes.extend(['Harness决定（模型解释，需结合原始数值复核）：'+d['action']+'；'+d['reason'],'下一步/缺少证据：'+d['next_step']])
        return notes
    def item(e,id=None,name=None):
        result=dict(id=id or e.get('group_id',e.get('event_id','unknown')),name=name or '待解释 · 检测区域',
            type='unknown',f0=e['freq_low_hz']/1e6,f1=e['freq_high_hz']/1e6,
            t0=e['sample_start']/max(n,1)*100,t1=e['sample_stop']/max(n,1)*100,
            status='unknown',feature=f"{(e['sample_stop']-e['sample_start'])/fs*1000:.3f} ms · 能量检测",
            action='保留待验证',initial=1,description='真实采集中的检测活动。尚不能仅凭频率、带宽或外观确认协议。',
            steps=['初始检测工件保留','关联需结合实际同步与校验证据','未解释部分保持 unknown'],
            source_group_ids=e.get('source_group_ids',[]),aggregation_is_hypothesis=e.get('aggregation_is_hypothesis',False),
            boundary_role=e.get('boundary_role','coarse_retained'),parent_group_id=e.get('parent_group_id'),
            refinement_id=e.get('refinement_id'),
            segmentation_level='coarse_activity' if e.get('evidence_level')=='coarse_activity' else 'fine_detection',
            coarse_time_cell_us=working_seg.get('proposal_time_cell_s',0)*1e6,
            coarse_frequency_cell_mhz=working_seg.get('proposal_frequency_cell_hz',0)/1e6,
            change=('归并边界是假设；原始成员 '+str(e.get('source_group_ids',[]))+' 在来源分割中完整保留。' if e.get('aggregation_is_hypothesis') else '初始事件不会被局部复查结果自动覆盖。'))
        if e.get('boundary_role') in ('adopted_fine','unresolved_residual'):
            title='已采用细边界' if e['boundary_role']=='adopted_fine' else '未解释残余区域'
            result.update(name=name or title,description=title+'：几何工作假设，协议仍为 unknown；功率指标继承父区域，未重新测量。',
                feature=title+' · 非信号数量',change='父区域 '+str(e['parent_group_id'])+'；细查 '+str(e['refinement_id']),
                steps=['父区域 '+str(e['parent_group_id']),'细查 '+str(e['refinement_id']),'未知残余与原始IQ完整保留'],
                segmentation_level='fine_detection' if e['boundary_role']=='adopted_fine' else 'coarse_activity')
        row=investigation_rows.get(e.get('parent_group_id') or e.get('group_id'))
        if row:
            result['investigation']=dict(state=row['investigation_state'],evidence_state=row['evidence_state'],
                decision_stale=row['decision_stale'],notes=investigation_notes(row))
        return result
    groups=report.get('signal_groups',[])
    chosen=sorted(groups,key=lambda g:g.get('snr_db',0),reverse=True)[:200]
    working=[item(e) for e in chosen]
    original=next((s for s in report.get('segmentations',[]) if s.get('evidence_id')==report.get('initial_segmentation_id')),None)
    initial_source=sorted(original['signal_groups'],key=lambda g:g.get('snr_db',0),reverse=True) if original else chosen
    initial_total=len(initial_source)
    initial=[item(e,id='initial_'+e['group_id']) for e in (initial_source if original and original.get('segmentation_level')=='coarse_activity' else initial_source[:200])]
    signals=working.copy();refined=[]
    adopted_refs={d['refinement_id'] for d in working_seg.get('fusion',{}).get('decisions',[]) if d['accepted']}
    for ref in report.get('scene_refinements',[]):
        for e in sorted(ref.get('mapped_events',[]),key=lambda e:(e['sample_stop']-e['sample_start'])*(e['freq_high_hz']-e['freq_low_hz']),reverse=True)[:100]:
            it=item(e,id=ref['evidence_id']+'_'+e['event_id'],name='复查 · '+ref.get('strategy','替代候选'))
            it['change']=ref.get('reason','')+'；质量状态：'+str(ref.get('quality_comparison',{}))
            it['action']='已作为工作几何采用 · 非协议确认' if ref['evidence_id'] in adopted_refs else '替代候选 · 未采用';refined.append(it)
            it['steps']=[f"策略 {ref.get('strategy')}；FFT {ref.get('fft_size')}；门限 {ref.get('threshold_db')}dB",
                f"目标内 {len(ref.get('mapped_events',[]))} 个候选；含上下文 {ref.get('context_event_count','旧工件未区分')} 个",
                '计数范围：'+ref.get('count_scope','旧工件含滤波上下文，不能直接与新目标范围比较'),
                '已知帧仅为部分参考；人工活动边界尚待标注']
            it['parent_group_id']=ref['parent_group_id'];it['parent_segmentation_id']=ref['parent_segmentation_id']
            it['description']='由Harness选择粗区域与时间窗口，回到原IQ滤波后生成的局部细候选；不自动升级为协议识别。'
    for i,link in enumerate(report.get('frame_associations',[])):
        if link.get('capture_id')!=capture['artifact_id']: continue
        protocol=link['protocol'];kind='wifi' if protocol=='IEEE802.11' else 'ble' if protocol=='Bluetooth LE' else 'candidate'
        it=item(link,id='verified_'+str(i),name=protocol+' · 有效帧证据')
        verification=next((v for v in report.get('verified_protocols',[]) if v.get('evidence_id')==link['evidence_id']),None)
        frame=verification['frames'][link['frame_index']] if verification else {}
        duration_us=(link['sample_stop']-link['sample_start'])/fs*1e6
        if protocol=='IEEE802.11' and frame.get('mac_header_hex'):
            fc=int.from_bytes(bytes.fromhex(frame['mac_header_hex'])[:2],'little')
            subtype={11:'RTS',12:'CTS',13:'ACK'}.get((fc>>4)&15,'MAC') if ((fc>>2)&3)==1 else 'MAC'
            it.update(name='Wi-Fi · '+subtype+' 控制帧' if subtype!='MAC' else 'Wi-Fi · MAC 帧',
                frame_subtype=subtype,frame_duration_us=duration_us,frame_time_estimated=True,
                bandwidth_is_decoder_passband=True,
                timing_uncertainty_calibrated=frame.get('timing_uncertainty_calibrated',False))
        it.update(type=kind,status='verified',feature='包级候选关联' if link['association_level']=='packet_time_frequency_overlap' else '提取窗口级关联',
            action='校验依据 · 保留歧义',description='真实协议完整性校验结果；与检测区域的重叠关联不等于整条谱迹已确认。',
            steps=['协议证据 '+link['evidence_id'],'关联层级 '+link['association_level'],'候选谱迹数 '+str(len(link.get('candidate_groups',[])))],
            change='仅关联有效帧的观察范围，不覆盖相邻未知信号。')
        signals.append(it)
        if verification:
            it['steps'].extend(stage_notes(verification))
            it['steps'].append('接收模式：'+verification.get('profile','未记录'))
            if frame.get('phy_format'):
                it['name']='Wi-Fi · '+frame['phy_format']+' · '+it.get('frame_subtype','MAC')+'帧'
                parameter='MCS '+str(frame['mcs']) if frame.get('mcs') is not None else '速率 '+str(frame.get('rate_bps',0)/1e6)+' Mb/s'
                it['steps'].append('PHY格式 '+frame['phy_format']+'；'+parameter+'；MAC FCS通过')
            if frame.get('ampdu_member') is not None:
                it['steps'].append('HT聚合成员：分隔符字节偏移 '+str(frame['ampdu_member']['delimiter_offset'])+'；每个成员单独通过MAC FCS')
            if verification.get('input_conversion'):
                it['steps'].append('输入为浮点IQ经固定缩放量化成CS8；量化与提取通带可能影响弱信号检出，原IQ保留。')
            if frame.get('timing_scope')=='containing_ppdu_not_individual_aggregated_mpdu':
                it['steps'].append('时间框对应承载PPDU的同步估计；聚合包中的多个MAC帧共享该范围，不代表各MAC帧的独立起止。')
            it['bandwidth_is_decoder_passband']=True
            if frame.get('aux_ptr'):
                ptr=frame['aux_ptr']
                it['steps'].append(f"辅助包线索：信道 {ptr['channel']}，{ptr['phy']}，偏移 {ptr['offset_us']} μs；指针不等于辅助包已确认")
        it['recognition_boundary_kind']='packet_estimate' if link['association_level']=='packet_time_frequency_overlap' else 'extraction_window'
        if protocol=='IEEE802.11':
            from .mac_details import label,subtype
            it.update(name=label(frame),frame_subtype=subtype(frame))
        if it['recognition_boundary_kind']=='extraction_window':
            it.update(name=protocol+' · 已识别（提取窗口）',
                feature='校验通过 · 精确帧边界待定位',
                description='协议帧校验已通过，但当前解码器未提供包级时间戳。虚线框仅表示提取窗口，不是真实帧起止或实测占用带宽。',
                change='识别成功；精确帧边界待定位。多个帧可能共用同一提取窗口。')
        if it.get('frame_time_estimated'):
            it['feature']=f"{it['frame_subtype']} · {duration_us:.2f} μs · 主体估计"
            it['description']=f"Wi-Fi {it['frame_subtype']} 帧完整性校验通过。该时间是同步位置与帧长/速率推算的单帧主体，不代表整个Wi-Fi活动时长；位置未独立标定。"
            it['change']='仅为单帧证据；17MHz是解码验证通带，不是整片活动的实测占用带宽。ERP的6μs额外空闲扩展未计入主体。'
        if protocol=='LTE':
            it.update(name='LTE · PBCH/MIB（广播校验窗口）',
                feature='PCI '+str(frame.get('cell_id'))+' · PBCH CRC通过',
                description='LTE同步及PBCH广播完整性校验通过。虚线仅表示中心子带的提取时间窗口，不是整段LTE活动边界；MIB通告带宽不是实测占用带宽。',
                integrity_unit='broadcast_block',
                steps=it['steps']+['小区物理ID '+str(frame.get('cell_id')),
                    '双工模式 '+str(frame.get('duplex_mode')),'MIB通告PRB '+str(frame.get('nof_prb')),
                    '独立广播块校验，不代表整个父区域已识别'])
        if protocol=='5G NR':
            it.update(name='5G NR · SSB/PBCH/MIB（广播校验窗口）',
                feature='PCI '+str(frame.get('cell_id'))+' · NR PBCH CRC通过',integrity_unit='broadcast_block',
                description='NR SSB/PBCH CRC及MIB解析通过。虚线只表示SSB子带提取窗口，不代表整个NR载波的时频边界。MIB不提供完整载波占用带宽。',
                steps=it['steps']+['小区物理ID '+str(frame.get('cell_id')),'SSB索引 '+str(frame.get('ssb_index')),
                    'MIB系统帧号 '+str(frame.get('sfn')),'不据此识别运营商、用户或整个粗区域'])
    # Keep every working region available; an explicit overview avoids treating
    # detector fragments as thousands of independent unidentified signals.
    raw_regions=[item(e) for e in groups]
    header_items=[]
    for i,h in enumerate(report.get('wifi_header_groups',report.get('wifi_header_evidence',[]))):
        if h['capture_id']!=capture['artifact_id']:continue
        label=f"W-H{i+1:03d} · {h['phy_format']} {h['channel_width_hz']/1e6:g}M · SIG通过 · L-SIG R{h.get('lsig_rate_mbps')}"
        entry=item(h,id='header_'+str(i),name=label)
        entry.update(status='header',type='candidate',evidence_state='报头CRC通过 · MAC未校验',
            feature=f"{(h['sample_stop']-h['sample_start'])/fs*1e6:.2f} μs · MCS {h.get('mcs')} · {h.get('coding')}",
            action='查看报头证据与观测子带',recognition_boundary_kind='header_estimate',header_parameters=h,
            description=('SIG CRC独立复核通过。相同报头和时间在所有20MHz子带交叉印证，框为报头声明的标称信道范围，不是实测占用带宽；MAC数据未校验。' if h.get('channel_center_hz') else 'SIG CRC独立复核通过。框表示已观测子带的范围，报头声明带宽单独列出；完整宽信道中心尚未确定，MAC数据未校验。'),
            steps=['前导同步与L-SIG时长估计','HT/VHT SIG CRC通过','信道带宽来自报头字段','未确认MAC FCS，不计入有效帧数'])
        header_items.append(entry)
    signals.extend(header_items)
    drone_items=[]
    for evidence in drone_display_evidence(report):
        if evidence['capture_id']!=capture['artifact_id']:continue
        dp=evidence['profile'];reference_label=f"ZC{dp['zc_length']}/r{dp['zc_root']}"
        for i,row in enumerate(evidence['references']):
            entry=item(row,id='drone_'+evidence['evidence_id']+'_'+str(i),name='DJI 图传候选 · 样本特征匹配')
            entry.update(type='drone',status='signature',evidence_state='ZC参考匹配 · 非协议CRC',
                feature=f"{reference_label} · {dp['subcarrier_spacing_hz']/1000:g}kHz · 匹配 {row['signature_score']:.3f}",
                action='查看图传参考与功率',recognition_boundary_kind='signature_energy_envelope',
                description=f"本机用户确认的无人机样本特征匹配；机型归属依赖现场信息，未完成协议CRC或视频解码。框为关联能量包络及配置的名义{dp['nominal_channel_bandwidth_hz']/1e6:g}MHz通道，不是解码包边界或控制器设置回读。",
                steps=['证据 '+evidence['evidence_id'],
                    f"参考符号 {row['reference_sample_start']/fs*1000:.6f}–{row['reference_sample_stop']/fs*1000:.6f} ms；CP相关 {row['cp_correlation']:.3f}",
                    f"参考活跃子载波 {row['reference_active_bandwidth_hz']/1e6:.2f} MHz；名义通道 {row['nominal_channel_bandwidth_hz']/1e6:g} MHz",
                    f"参考带内功率 {row['reference_band_power_dbfs']:.2f} dB（输入IQ标度，非校准dBm）；接收增益 {row.get('gain_db')} dB",
                    'DJI/Mavic4 Pro为用户现场确认；当前仅为该样本配置验证，不泛化为全部型号或协议解码'],
                change='新增独立图传证据；保留原Wi-Fi帧和报头，不将重叠区域整体改名。')
            drone_items.append(entry)
            if evidence.get('analysis_mode')=='frequency_agile':
                entry['name']='DJI 图传候选 · 变频/变带宽匹配'
                entry['steps'].append('逐时间块重新搜索频率及参考模式；时间相邻不证明同一架无人机。')
                entry['change']='独立变频/变带宽参考检查；保持原Wi-Fi和定频证据。'
    signals.extend(drone_items)
    packets=[s for s in signals if s['status']=='verified']
    attempts=[]
    for v in report.get('verification_attempts',[]):
        if v.get('valid_frames',0)>0:continue
        rec=ctx.resolve(v['artifact_id'])
        vm=json.loads(Path(rec['metadata_path']).read_text())
        if vm.get('parent_artifact_id')!=capture['artifact_id']:continue
        attempts.append(dict(f0=(vm['center_hz']-vm['requested_span_hz']/2)/1e6,
            f1=(vm['center_hz']+vm['requested_span_hz']/2)/1e6,
            t0=vm['parent_sample_start']/max(n,1)*100,t1=vm['actual_parent_sample_stop']/max(n,1)*100))
    from .presentation import activity_catalog,segmentation_quality
    catalog=activity_catalog(raw_regions,packets,attempts,group_regions=not is_coarse)
    catalog.extend(header_items)
    catalog.extend(drone_items)
    evidence_labels={'unverified':'尚未验证','analyzed_unverified':'已分析 · 协议未确认',
        'attempted_no_valid_frame':'指定协议窗口未检出有效帧','valid_frames_overlap':'存在包级有效帧重叠',
        'valid_frames_in_extraction_window':'窗口内有有效帧 · 本区归属未确认',
        'localized_frames_not_associated':'窗口帧已定位 · 未关联本区',
        'decoder_unsupported':'解码器不支持 · 保留未知','decoder_error':'验证工具报错 · 保留未知'}
    for s in catalog:
        if s['status']=='verified' or not s.get('investigation'):continue
        info=s['investigation'];label=evidence_labels.get(info['evidence_state'],'保留未知')
        if info['state']=='deferred':label+=' · 暂缓复查'
        s.update(evidence_state=label,name=s['name'].split(' · ')[0]+' · '+label,
            action='查看复查与尝试窗口',steps=s.get('steps',[])+info['notes'],
            description=s.get('description','')+' 协议尝试仅对应所列窗口；暂缓不代表无信号或已识别。')
    elapsed=out.get('elapsed_ms',0)/1000
    totals={}
    timing=p/'timing.json'
    if timing.exists():
        for e in json.loads(timing.read_text()).get('spans',[]):
            if e['stage'] not in ('harness','build_report'): totals[e['stage']]=totals.get(e['stage'],0)+e.get('elapsed_ms',0)/1000
    labels={'follow_ble_aux':'AuxPtr复查总耗时（含子步骤）','aux_subband_extraction':'辅助包子带提取','aux_protocol_verification':'辅助包CRC校验','model_stream':'模型调用','capture_iq':'采集与配置','inspect_capabilities':'硬件检查','segment_spectrum':'全带分割','extract_subband':'子带提取','verify_protocol':'协议校验','refine_signal_region':'局部复查','profile_signal':'特征分析','assess_signal_scene':'场景评估'}
    spans=[dict(name=labels.get(k,k),seconds=v) for k,v in sorted(totals.items(),key=lambda t:-t[1])[:6]]
    extra_labels={'consolidate_signal_regions':'碎片归并','compare_segmentations':'方案比较',
        'select_segmentation':'方案选择','inspect_spectrum':'频谱概览','match_signal_hypotheses':'候选匹配',
        'query_band_priors':'频段先验','summarize_spectral_activity':'活动归纳','coarse_segment_spectrum':'全段STFT粗分割','fuse_refined_regions':'局部边界融合',
        'plan_unknown_investigations':'未知区域复查计划','record_investigation_decision':'复查决定'}
    for span in spans:span['name']=extra_labels.get(span['name'],span['name'])
    protocols=sorted({v['protocol'] for v in report.get('verified_protocols',[])})
    request_path=p/'request.json'
    prompt=json.loads(request_path.read_text()).get('prompt','') if request_path.exists() else ''
    from .frequency_scope import effective_metadata
    scope=effective_metadata(ctx,m)['frequency_scope']
    return dict(status=out.get('status','report_ready'),prompt=prompt,frequency_scope=scope,
        center_mhz=scope['analysis_center_hz']/1e6,bandwidth_mhz=scope['analysis_span_hz']/1e6,
        duration_ms=n/fs*1000,event_count=len(groups),protocol_count=len(protocols),protocol_names=protocols,
        refinement_count=len(report.get('scene_refinements',[])),
        consolidation_count=0 if is_coarse else sum(bool(s.get('consolidated_from')) for s in report.get('segmentations',[])),signals=signals,
        activity_catalog=catalog,raw_signals=raw_regions+packets+header_items+drone_items,activity_count=sum(s['status'] not in ('verified','header','signature') for s in catalog),
        drone_signature_count=len(drone_items),
        evidence_summary=dict(verified_frames=len(packets),activity_index_count=len(catalog),
            untested_activity_count=sum(s.get('evidence_state')=='尚未验证' for s in catalog),
            failed_activity_count=sum(s.get('evidence_state')=='局部验证未通过' for s in catalog),
            note='Activity indices are display groups, not detected signals, protocols or transmitters.'),
        segmentation_quality=segmentation_quality(report),
        segmentation_level=working_seg.get('segmentation_level','fine_detection'),
        boundary_composition=working_seg.get('boundary_composition'),boundary_fusion=working_seg.get('fusion'),
        coarse_scale=dict(time_cell_us=working_seg.get('proposal_time_cell_s',0)*1e6,
            frequency_cell_mhz=working_seg.get('proposal_frequency_cell_hz',0)/1e6),
        initial_total_count=initial_total,initial_display_count=len(initial),
        fine_total_count=sum(len(r['mapped_events']) for r in report.get('scene_refinements',[])),fine_display_count=len(refined),
        coarse_to_fine=report.get('coarse_to_fine',{}),
        stages=[initial,refined if is_coarse else refined or working,signals],visual=waterfall(ctx,capture),timing=spans,elapsed_s=elapsed,report=report,
        warning=(f"工作边界 {len(groups)} 个区域；局部采用 {working_seg['fusion']['accepted_parent_count']} 处、拒绝 {working_seg['fusion']['rejected_parent_count']} 处。未解释残余保持未知，区域数不是信号数量，几何保护不证明准确率提高。" if working_seg.get('fusion') else f"全段粗分割 {len(groups)} 个活动区域；局部细候选 {sum(len(r['mapped_events']) for r in report.get('scene_refinements',[]))} 个另层展示；{len(packets)} 条帧证据另层保留。粗区域不等于单帧、协议或设备。" if is_coarse else f"工作分割 {len(groups)} 个区域；概览组织为 {len(catalog)} 项活动/帧索引，不代表信号类型或设备数。切换检测区域可查看全部边界。"))


@app.get('/edge/tasks/{task_id}/waterfall-window')
def waterfall_window(task_id:str,start_ms:float,end_ms:float):
    p=task_path(task_id)
    if not np.isfinite([start_ms,end_ms]).all() or start_ms<0 or end_ms<=start_ms or end_ms-start_ms>20.001:
        raise HTTPException(422,'Window must be finite, positive and at most20ms')
    report=json.loads((p/'report.json').read_text());capture=report['captures'][0];m=capture['metadata']
    if m.get('simulated'):raise HTTPException(422,'RF recording required')
    ctx=Context(task_id);rec=ctx.resolve(capture['artifact_id']);iq=np.memmap(rec['path'],dtype='<c8',mode='r')
    fs=m['sample_rate_hz'];a=int(start_ms/1000*fs);b=min(len(iq),int(np.ceil(end_ms/1000*fs)))
    if b-a>4_000_000 or a>=len(iq):raise HTTPException(422,'Window exceeds recording or4M samples')
    nfft=2048;hop=1024;win=np.hanning(nfft);frames=[];positions=[]
    for seg in m['segments']:
        lo=max(a,seg['file_start']);hi=min(b,seg['file_start']+seg['count'])
        for off in range(lo,hi-nfft+1,hop):
            frames.append(np.asarray(iq[off:off+nfft])*win);positions.append(off+nfft/2)
    if not frames:raise HTTPException(422,'Window shorter than STFT resolution')
    power=abs(np.fft.fftshift(np.fft.fft(frames,axis=1),axes=1))**2/np.sum(win**2)
    pooled=power.reshape(len(power),512,4).max(axis=2)
    rows=min(512,len(frames));image=np.full((rows,512),1e-20)
    indices=np.clip(((np.array(positions)-a)/max(1,b-a)*rows).astype(int),0,rows-1)
    np.maximum.at(image,indices,pooled)
    db=10*np.log10(np.maximum(image,1e-20));valid=db[db>-190]
    low=float(np.percentile(valid,20));high=max(low+25,float(np.percentile(valid,99.5)))
    return dict(rows=rows,columns=512,db=np.round(db,2).tolist(),min_db=low,max_db=high,
        low_mhz=(m['center_hz']-fs/2)/1e6,high_mhz=(m['center_hz']+fs/2)/1e6,
        low_time_percent=a/len(iq)*100,high_time_percent=b/len(iq)*100,
        source='recorded-iq-window-stft',sample_start=a,sample_stop=b,
        fft_size=nfft,hop_samples=hop,time_step_us=hop/fs*1e6,
        warning='Local STFT from recorded IQ, no new capture. Frame positions remain uncalibrated sync estimates.')


@app.get('/edge/tasks/{task_id}/recording')
async def recording_info(task_id:str):
    task_path(task_id)
    return await proxy('/v1/tasks/'+task_id+'/recording')


@app.delete('/edge/tasks/{task_id}')
async def remove_task_record(task_id:str):
    task_path(task_id)
    return await proxy('/v1/tasks/'+task_id,'DELETE')


@app.post('/edge/tasks/{task_id}/restore')
async def restore_task_record(task_id:str):
    task_path(task_id)
    return await proxy('/v1/tasks/'+task_id+'/restore','POST')


app.mount('/',StaticFiles(directory=str(UI),html=True),name='ui')

