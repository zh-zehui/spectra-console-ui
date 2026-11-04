"""Explicit display grouping. Never changes detection or assigns protocol identity."""
from collections import defaultdict
from statistics import median


def segmentation_quality(report):
    groups=report.get('signal_groups',[])
    fs=(report.get('captures') or [{'metadata':{}}])[0]['metadata'].get('sample_rate_hz',1)
    short=sum(g.get('duration_s',(g['sample_stop']-g['sample_start'])/fs)<.0001 for g in groups)
    by_id={g['group_id']:g for g in groups};expansions=[];unmatched=0;packets=0
    for link in report.get('frame_associations',[]):
        if link.get('association_level')!='packet_time_frequency_overlap':continue
        packets+=1
        matches=[by_id[m['group_id']] for m in link.get('candidate_groups',[]) if m['group_id'] in by_id]
        if not matches:unmatched+=1;continue
        expansions.append(min((g['sample_stop']-g['sample_start'])/max(1,link['sample_stop']-link['sample_start']) for g in matches))
    flags=[]
    selected=next((s for s in report.get('segmentations',[]) if s.get('evidence_id')==report.get('selected_segmentation_id')),None)
    if selected and selected.get('segmentation_level')=='coarse_activity':
        if selected.get('boundary_composition')=='mixed':
            return dict(status='mixed_boundaries_with_unknown_residual',region_count=len(groups),
                flags=['工作边界包含细检测几何与未解释残余；不是协议或设备数量'],
                packet_evidence_count=packets,unmatched_packet_count=unmatched,
                note='采用记录可追溯到父区域和细查工件；功率统计继承父区域，几何保护不证明准确率提高。')
        return dict(status='coarse_with_local_detail' if report.get('scene_refinements') else 'coarse_only',
            flags=['粗区域是活动包络，不是单帧或协议边界'],region_count=len(groups),
            packet_evidence_count=packets,unmatched_packet_count=unmatched,
            note='全局粗层与局部细查分开；未做细查的粗区域仍待验证，不能用控制帧IoU评价粗层边界精度。')
    if len(groups)>=20 and short/max(1,len(groups))>.5:flags.append('短区域占比高')
    if expansions and median(expansions)>10:flags.append('关联区域远大于单帧')
    if unmatched:flags.append('部分帧没有检测区域关联')
    return dict(status='needs_review' if flags else 'not_calibrated',flags=flags,
        region_count=len(groups),short_under_100us_count=short,packet_evidence_count=packets,
        unmatched_packet_count=unmatched,median_best_time_expansion=median(expansions) if expansions else None,
        note='启发式复核提示，不是准确率；控制帧与整次突发不是同一层次，重叠覆盖不能证明边界正确。')


def activity_catalog(regions, packets, attempts=(),group_regions=True):
    families=defaultdict(list)
    for r in regions:
        width=r['f1']-r['f0']
        category,grid=('窄带',.5) if width<1 else ('中等带宽',2) if width<5 else ('宽带',5)
        related=any(overlap(r,p) for p in packets)
        tested=any(overlap(r,a) for a in attempts)
        r['evidence_state']='存在有效帧重叠' if related else '局部验证未通过' if tested else '尚未验证'
        r['name']=f"{category}区域 · {r['evidence_state']}"
        # Bucket width categories separately; this is a navigational index, not a new detector.
        key=(category,round((r['f0']+r['f1'])/2/grid)) if group_regions else ('粗活动区域',r['id'])
        families[key].append(r)
    result=[]
    for (category,_),members in families.items():
        related=[p for p in packets if any(overlap(r,p) for r in members)]
        tested=any(overlap(r,a) for r in members for a in attempts)
        state='存在有效帧重叠' if related else '局部验证未通过' if tested else '尚未验证'
        base=dict(members[0]);base.update(id='activity_'+str(len(result)),
            name=f'{category}活动 · {state}',type='unknown',status='unknown',
            f0=min(r['f0'] for r in members),f1=max(r['f1'] for r in members),
            t0=min(r['t0'] for r in members),t1=max(r['t1'] for r in members),
            members=members,member_count=len(members),evidence_state=state,
            feature=f'{len(members)} 个检测区域 · {category}',action='展开成员与验证状态',
            description=f'按频率位置和带宽尺度组织的活动索引，包含{len(members)}个区域。{state}。这不是同一发射机、同一协议或单次突发的结论。',
            steps=[f'保留全部 {len(members)} 个成员坐标',f'{len(related)} 条有效帧与部分成员重叠；不确认整组协议',state],
            change='概览只组织导航，不合并底层检测边界；选中后显示最多40个成员框，全部成员仍在原始视图及报告中。')
        result.append(base)
        if not group_regions:
            base.update(id=members[0]['id'],name='粗活动区域 · '+state,
                feature=f"{members[0].get('coarse_time_cell_us',0):g}μs × {members[0].get('coarse_frequency_cell_mhz',0):g}MHz粗网格",
                description='整段IQ的连续STFT功率聚合产生的粗活动包络。可含多次突发、重叠信号与未解释能量，不是单帧、协议或设备边界。'+state+'。',
                action='选择区域，查看局部细查',change='粗层保留原始IQ和均值/峰值网格；局部细查独立成层，不把细框数量加作新信号数量。')
            role=base.get('boundary_role')
            if role in ('adopted_fine','unresolved_residual'):
                title='已采用细边界' if role=='adopted_fine' else '未解释残余区域'
                base.update(name=title+' · '+state,feature=title+' · 父区域 '+str(base.get('parent_group_id')),
                    description='局部边界融合保留父子关系。'+('细检测几何候选通过密度和分区保护，尚非协议确认或准确率证明。' if role=='adopted_fine' else '原粗区域中尚未被细候选解释的范围；保留为未知，不代表新增信号。'),
                    action='查看来源与验证证据',change='来源细查 '+str(base.get('refinement_id')),
                    steps=['父区域 '+str(base.get('parent_group_id')),'细查 '+str(base.get('refinement_id')),title],
                    segmentation_level='fine_detection' if role=='adopted_fine' else 'coarse_activity')
    packet_groups=defaultdict(list)
    for p in packets:
        packet_groups[(p['name'],round((p['f0']+p['f1'])/2,3),p['feature'])].append(p)
    for key,members in packet_groups.items():
        base=dict(members[0]);base.update(id='packet_activity_'+str(len(result)),
            name=key[0].replace('有效帧证据',f'{len(members)} 帧已校验'),members=members,member_count=len(members),
            f0=min(r['f0'] for r in members),f1=max(r['f1'] for r in members),
            t0=min(r['t0'] for r in members),t1=max(r['t1'] for r in members),
            evidence_state='有效帧校验',feature=f'{len(members)} 帧 · 同频证据索引',
            description=f'{len(members)}条真实完整性校验帧的同频索引，不表示同一设备，也不确认帧间其他活动。')
        result.append(base)
    return sorted(result,key=lambda r:(r['status']!='verified',-r['member_count']))


def overlap(a,b):
    return min(a['f1'],b['f1'])>max(a['f0'],b['f0']) and min(a['t1'],b['t1'])>max(a['t0'],b['t0'])
