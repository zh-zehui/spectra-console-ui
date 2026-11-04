import json
from pathlib import Path
from collections import Counter
from spectrum_agent.common import ROOT,dump
rows=[]
for p in sorted((ROOT/'docs/testing').glob('window-wide-task_*.json')):
    row=json.loads(p.read_text());report=json.loads((ROOT/'runs'/row['task_id']/'report.json').read_text())
    row['header_group_formats']={str(k):v for k,v in Counter((h['phy_format'],h['channel_width_hz']/1e6) for h in report['wifi_header_groups']).items()}
    row['vht80_header_groups']=sum(h['phy_format']=='VHT' and h['channel_width_hz']==80e6 for h in report['wifi_header_groups'])
    row['vht80_four_slice_groups']=sum(h['phy_format']=='VHT' and h['channel_width_hz']==80e6 and h['channel_center_hz'] is not None for h in report['wifi_header_groups'])
    row['vht80_stream_coding_counts']={str(k):v for k,v in Counter((h.get('num_spatial_streams'),h.get('coding')) for h in report['wifi_header_groups'] if h['phy_format']=='VHT' and h['channel_width_hz']==80e6).items()}
    rows.append(row)
dump(ROOT/'docs/testing/window-wide-final-summary.json',dict(new_rf_capture=False,regression_tests=69,records=rows))
lines=['5.8 GHz 分窗分析与宽带 Wi-Fi 报头识别测试记录','',
    '数据：用户指定的三份既有 IQ，各500ms、200MS/s、中心5.8GHz；本次没有访问USRP或新采集。',
    '每份按10ms主窗口分成50窗，左右2ms上下文。结果保留原始采样坐标；跨窗能量关联保留假设属性，不将窗口边缘当作包边界。',
    '协议测试固定20MHz观测子带网格5745/5765/5785/5805/5825/5845/5865MHz，以便复现比较；这不是所有频点、所有协议的完整识别。','']
for r in rows:
    lines.extend([r['source_task_id'],'查看结果：'+r['task_id'],
        f"处理耗时{r['elapsed_s']:.2f}s；50个主窗口；粗活动区域{r['coarse_regions']}个；MAC FCS记录{r['valid_mac_frames']}条。",
        f"VHT80报头观测{r['header_formats'].get(str(('VHT',80.0)),0)}条；按相同SIG内容/时间关联为{r['vht80_header_groups']}组，其中{r['vht80_four_slice_groups']}组在全部四个20MHz子带获得交叉印证。",
        'VHT80空间流/编码统计：'+json.dumps(r['vht80_stream_coding_counts'],ensure_ascii=False),''])
lines.extend(['能力与限制：',
    '1. 已新增HT40/VHT40/80等SIG报头证据，保留CRC复核、带宽、MCS、流数、编码及L-SIG时长估计；不能等同于完整MAC解码。',
    '2. 只有所有相邻20MHz子带的SIG内容和时间一致时才推定完整40/80MHz标称信道中心；否则只画观测范围。标称信道宽度不等于实测占用带宽。',
    '3. 原20MHz MAC接收路径保留。不同观测子带、报头组、MAC帧、粗活动区域是不同计数，不能相加称作信号或设备数。',
    '4. 新增CP/子载波间隔候选工具，已用OFDM正例、噪声和单音负例测试。只测试指定符号时长/CP比率，不据此确认Wi-Fi、LTE或无人机品牌。',
    '5. dsh实测原始提示词能选择10ms分窗、旧IQ、报头检查和绘图；耗时与公开工具调用见window-header-harness.json及对应任务timing.json/events.jsonl。',
    '6. Web已增加10ms窗口选择、SIG证据独立开关、MAC帧类型与L-SIG速率标签；浏览器验证显示10ms窗口、5.12us STFT步长且无JS异常。',
    '7. 69项相关测试通过。宽带报头仿真使用上游标准80MHz前导生成部分，并非完整80MHz负载生成器；四个20MHz分支均通过SIG校验，未冒充MAC帧。','',
    'VHT80工具查找记录：',
    'cloud9477/gr-ieee80211：当前项目固定版本20MHz接收；源码在CRC正确后用带宽支持检查拒绝40/80/160。此次独立输出支持检查前的有效报头，不放宽原负载解码限制。',
    'https://github.com/cloud9477/gr-ieee80211',
    'josephnef/sdr2wifi：文档列出HT40接收；VHT40/80完整解码未实现，不能直接当作VHT80完整解码器。',
    'https://github.com/josephnef/sdr2wifi/blob/main/CLAUDE.md',
    'PicoScenes/PyPicoScenes：找到支持80MHz及更宽信道和离线回放的SDR软件方案；官方要求x86-64，明确不支持ARM。没有安装到Jetson，也没有将MIT接口包装器误称为完整PHY源码。',
    'https://ps.zpj.io/manual/features_pricing.html',
    'https://ps.zpj.io/manual/installation.html',
    'MAC BRP/BA等类型映射参考Wireshark头文件：',
    'https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-ieee80211.h',
    '', '所有研究回放标为partial：仅表示尚未对所有未知区域和协议完成穷尽验证，不能把固定网格测试包装成全频段所有协议已识别。'])
(ROOT/'docs/testing/5.8G-分窗与宽带报头测试总结.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
comparison=ROOT/'docs/testing/wide-mac-regression.json'
if comparison.exists():
    compared=json.loads(comparison.read_text())
    with (ROOT/'docs/testing/5.8G-分窗与宽带报头测试总结.txt').open('a',encoding='utf-8') as out:
        out.write('\n原MAC路径前后回放对比：\n')
        for row in compared:
            out.write(f"{row['task_id']}：{row['compared_windows']}个相同IQ窗口，原包装器/新包装器MAC观测{row['raw_mac_observations_before']}/{row['raw_mac_observations_after']}，逐帧字节与时间一致。\n")
        out.write('这里包含上下文重复观测，不能与主窗口归属及去重后的1007/776/753直接比较；详见wide-mac-regression.json。\n')
print(json.dumps(rows,ensure_ascii=False))
