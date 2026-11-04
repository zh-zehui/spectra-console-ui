# Spectrum Agent — official DeepSeek Harness integration

本工程在 Jetson 上无界面执行任务，使用官方 @deepseek-ai/dsh，通过业务工具调用 GNU Radio/UHD，没有自建 agent loop。/home/sribd/usrp_client 只读，导入与修改仅使用 vendor/usrp_client 副本。

2026-10-29 更新：新增独立 `analyze_drone_video_link` 工具，在用户确认的 Mavic4 Pro 定频记录中找到 5803.5 MHz、ZC293/root123/30kHz 参考特征；新 DSH 任务检出 196 处匹配，1240 条 Wi-Fi 帧观测及报头证据逐项保留。另提供无模型的流式 NDJSON 检测模块。当前是样本特征识别，未解码视频，未完成真机端到端实时验收。详见 [图传识别与实时测试](docs/testing/2026-10-29-无人机图传识别与实时处理测试.txt)。

任务文件位置、不同任务的产物、历史回放、IQ 分享与清理说明见 [任务目录与文件说明](docs/任务目录与文件说明.md)。

2026-10-28 更新：已部署模型规划的通用协议批处理，覆盖 2.4 GHz 与 5.8 GHz。相同 5.8 GHz 历史 IQ 的真实 DSH 回放为 212.838 秒、1007 条有效 MAC 帧观测、80 组 VHT80 报头；2.4 GHz 为 183.999 秒、81 条 Wi-Fi 与 71 条 BLE 有效帧观测。旧 Wi-Fi 帧逐条比较均保留。VHT80 报头不等于完整宽带 MAC 解码，完整任务尚未达到 99 秒。详情及公开调用耗时见 [最新测试记录](docs/testing/2026-10-28-通用协议批处理与DSH提速测试.txt)；下文版本条目保留历史实现说明。

## 已验证与边界

- Node.js 24.22.1、官方 dsh 0.2.1-rc.2 独立安装在 runtime/。
- DeepSeek 真实模型工具闭环；本地模型路由使用本机 HTTP fixture 验证，不代表已部署本地大模型。
- 异步任务 API、鉴权、幂等、取消、SSE、工件、阶段计时。
- 频段先验、复制的传统分割、信号关联、GNU Radio频移FIR/抽取、子带重采样和数值特征。
- 实验性 BLE LE1M legacy advertising 解调、去白化、包头与CRC24验证。
- 项目内编译的gr-ieee802-11传统20MHz OFDM解码器，已知帧/错误FCS/噪声验证及真实IQ重放通过。
- X310+UBX-160：中心2.4GHz、分析跨度100MHz、200MS/s、0.1秒接收零overflow/时间缺口；真实IQ解出BLE有效帧。

这不代表长时间无丢样、经过校准的100MHz通带或全协议覆盖。Wi-Fi仅支持传统20MHz OFDM，不支持所有802.11 PHY；经典蓝牙/802.15.4等未验证协议保留unknown/unsupported，检测组不是发射机数量。

## 环境与启动

工程：/home/sribd/spectrum_agent。独立.venv只读使用usrp_dev已有包；新增依赖装入.venv，不改原conda环境。requirements.txt与runtime/dsh/package-lock.json固定依赖。

凭据：~/.config/spectrum-agent/credentials.json，目录700、文件600。字段deepseek_api_key、api_token，可选local_model_api_key；不写入命令参数、源码或文档。

启动（单worker、无UI）：

~~~bash
cd /home/sribd/spectrum_agent
bash scripts/start-api.sh
~~~

监听127.0.0.1:8765，无Swagger/Web UI。开发服务PID在runs/api.pid，日志runs/api.stdout、api.stderr，尚未配置开机自启。

提交真实任务：

~~~bash
cd /home/sribd/spectrum_agent
.venv/bin/python scripts/submit_task.py '中心2.4GHz、100MHz带宽内都有什么信号' --center 2.4e9 --span 100e6 --duration 0.1 --wait
~~~

2.4GHz是中心，范围2.350–2.450GHz。默认观察0.1秒，最多允许10秒，长任务时间/内存仍需实测；不静默改分段扫描。合成测试加--synthetic，实际有效跨度3.2MHz、时长0.05秒，会明确标记差异。

外部机器可通过SSH隧道访问：

~~~powershell
ssh -N -L 8765:127.0.0.1:8765 Jetson-server-new
~~~

请求Authorization头使用单独api_token，不使用DeepSeek API Key。暂未直接向局域网开放端口。

## API

- GET /healthz：不采集的存活检查。
- GET /v1/capabilities：静态能力。
- POST /v1/tasks：prompt、constraints、budgets、source、provider、idempotency_key；返回202/task_id。
- GET /v1/tasks/{id}、GET /v1/tasks/{id}/result。
- POST /v1/tasks/{id}/cancel：仅取消本任务。
- GET /v1/tasks/{id}/events：SSE/Last-Event-ID。
- GET /v1/tasks/{id}/files/report、timing、events。
- GET /v1/tasks/{id}/artifacts/{artifact_id}：仅任务内工件。

除healthz外需Bearer鉴权。同幂等键同请求返回原任务，改请求返回冲突。队列16项、任务串行。重启不自动重放执行中采集。

## 官方dsh与工具

configs/spectrum.patch.yml在官方headless profile装载plugins/spectrum-tools/index.mjs；禁用编码/shell及无关网络工具。插件用官方defineTool、ctx.tools.register，通过固定Python worker传递结构化参数，不执行模型生成代码。

工具：query_band_priors、inspect_capabilities、make_synthetic_capture、capture_iq、segment_spectrum、extract_subband、profile_signal、match_signal_hypotheses、list_signal_groups、verify_protocol、build_report。

业务入口只监管进程、队列、取消和结果；模型循环、上下文和会话属于官方dsh。

## 本地模型

复制configs/local-model.patch.example.yml为local-model.patch.yml，填写真实端点与模型ID，通过官方llm-pi-ai适配器调用。必须验证原生工具调用，任务指定provider=local，默认不回退。尚未部署真实本地规划模型；专用波形分类模型以后作为分析工具接入。

## 工件与计时

runs/{task_id}/保存请求、官方dsh事件、stderr、worker日志、report.json/report.md、timing.json/timing.md及索引；IQ等在artifacts/{task_id}/。

报告是确定性工具证据投影。模型文字仅辅助说明，数值以采集元数据与report.json为准。记录模型流、工具、采集、分割、验证及墙钟；并行span不能相加，首流事件时延不等于服务端思考时间。

v0.2 按时间排序归并事件，保留 event_ids 和原始事件，不跨硬件丢样间隙。报告的 frame_associations 提供解码帧与谱迹的候选关联：BLE 使用包位置及滤波延迟估计映射原始采样区间；当前 Wi-Fi 后端不提供包采样位置，只标记 extraction_window_only。候选关联不自动把整条谱迹标为已确认协议，多个候选保留歧义。

v0.3 将分割与识别纳入同一个官方 Harness 反馈流程：初始分割/特征或验证 → assess_signal_scene → 有依据的 refine_signal_region → 特征或协议复核 → 再次评估 → 报告。局部工具采用时频背景估计和双门限连通生长；单任务最多2次复查、单次目标原始组最长20ms并补上下文。噪声背景估计可能被持续信号污染，工具输出是替代候选，未证明优于初始分割。初始全带结果及未知区域均保留，报告包含 scene_reviews、scene_refinements、模型撰写的复查原因、实际参数、证据ID及阶段计时。当前未自动判定某次复查提高了识别准确率；需要标注样本评估。

固定真实 IQ 的闭环回放：`PYTHONPATH=src .venv/bin/python scripts/replay_feedback.py`，不采集、不调用模型，使用已有真实采集和协议证据；输出 docs/testing/feedback-replay-v03.json 中记录本次新建的任务ID。官方 Harness 的本地HTTP夹具测试也覆盖评估、重分割、特征复核、再次评估和报告；它验证工具编排，不代表真实本地模型已部署。

v0.4 面向整个观察频段的信号画像：候选库 configs/band_priors.json + band_extensions.json 包含2.4/5.8GHz标准协议、专有链路、图传及非通信干扰，版本与联合SHA256保存在任务先验快照；候选不是实际存在。5.8GHz不会使用蓝牙先验。

除 GNU Radio 外，已注册 NumPy/SciPy 工具 analyze_iq_quality（IQ质量诊断）、analyze_burst_timing（包络突发及周期候选）、analyze_sweep_structure（强线性扫频主脊线候选）、summarize_spectral_activity（全量事件频率网格活动归纳），以及 sigmf==1.13.0 工具 export_sigmf_recording（SigMF1.2.6规范校验、可选未知事件标注）。IQ分析最多读取2M有效采样，明确列出实际覆盖区间，不跨硬件间隙推断周期；优先对子带分析。波形、周期、用途和协议是不同证据层次，不凭OFDM判Wi-Fi、FSK判蓝牙或扫频判LoRa/雷达。

新分析证据进入确定性报告 generic_signal_analysis 和场景评估 generic_evidence，可作为重分割反馈。SigMF数据仅在本地创建硬链接和元数据，原始IQ不改动；导出的检测标注是unknown，不是训练真值。工具能力及尚未安装的后端见 configs/analysis_catalog.json 和鉴权API /v1/capabilities。libbtbb、gr-ieee802-15-4、TorchSig、URH没有注册为空工具或被宣称已经提供新协议识别；inspectrum仅作为可选人工离线复核工具，不引入边缘端界面。

v0.5 接入 IEEE802.15.4 2.4GHz O-QPSK/DSSS250kb/s（bastibl/gr-ieee802-15-4 maint-3.10固定 a210b11fed2d307ef797ea79842b54f4e8ed3dd5）。调用 verify_protocol(protocol="ieee802154_oqpsk",channel=11..26)，先提取对应2405+5*(channel-11)MHz、2MHz通带、4MS/s子带。接收器实际同步解调，再校验MAC字段和CRC16；上层保持unknown，不直接断言Zigbee/Thread。工程内build-ieee154.sh可重建，不修改原GNU Radio环境。此协议已通过离线已知IQ、损坏帧、错误同步、噪声及小频偏/噪声测试；受控射频正例尚待验证。

Wi-Fi时间定位补丁由 scripts/patch-wifi-timestamps.py应用到工程内固定源码副本，随build-wifi.sh重建。原始采样索引经过短/长同步及均衡器传入校验帧，按LTF位置和PHY帧长度映射包区间；结合子带滤波群延迟映射原始IQ。已知起点、前置偏移、重复帧和滤波重采样测试通过。时间位置为同步估计，8个子带采样点是名义容差，未作为真实复杂信道的校准误差界。新的Wi-Fi证据可进行包级候选关联；旧证据若没有位置仍保留窗口级。802.15.4当前仍是窗口级关联。

固定真实IQ回放：`PYTHONPATH=src .venv/bin/python scripts/replay_protocols_v05.py`，复用历史采集，不启动射频或模型；结果 docs/testing/protocol-replay-v05.json。v0.4中802.15.4未注册的说明为历史状态，当前能力以v0.5及analysis_catalog.json为准。

固定历史数据的归并基线：`PYTHONPATH=src .venv/bin/python scripts/benchmark_catalog.py`，结果在 docs/testing/catalog-benchmark-v02.json；回放报告在 runs/catalog_replay_v02/。只读取历史 IQ/事件，不触发 RF 采集或模型请求，原始任务保持不变。

## 测试

### v0.9：整段IQ先粗后细

新任务默认 `segmentation_workflow=coarse_to_fine`。Harness 首先调用 `coarse_segment_spectrum` 对全部原始IQ进行重叠Hann窗STFT；FFT按批计算仅为内存调度，检测背景与连通区域跨越整段连续采集，采集间隙仍分开。末尾不足一个标准hop的尾部用末端对齐窗覆盖，短于FFT的独立片段明确报告未分析，不能宣称完整覆盖。

先对线性功率按时间/频率网格聚合，均值分支描述活动包络，单独归一化的峰值分支保留短窄活动线索，再生成粗活动区域；默认1024点、100μs×1MHz、6dB。保存粗STFT的均值/峰值网格和原IQ，所有初始正瓦片保留。粗网格尺度不等于细边界精度，不保证弱短信号召回；粗层没有<100μs框也不能作为保留短包的证明。

Harness 选择粗区域后，用 `refine_signal_region(offset_s,duration_s,strategy)` 回到原IQ滤波、局部细查；可以选择长区域后段，单窗口最多20ms加上下文，不再固定只查前段。局部检波策略和参数由模型选择。FIR过渡带约束导致的局部通带收缩明确写入工件，不声称细查了整个粗父层。

真实官方Harness回放 `task_coarse_f7b10d72f92c`：约102.59秒、不新采集，20,000,000 IQ样本全部STFT覆盖，39,062个FFT窗；生成144个粗区域，22条已有校验帧均有粗区域关联。模型选择2处局部细查，产生1652个尚未采用的局部细候选（1483/169），说明局部检测仍有密集候选和质量问题，不代表1652种信号或新增设备。全局粗父层不被子层候选或单帧自动覆盖；记录原始、粗层、局部层及模型选择理由，准确率仍未标定。

界面分为全段粗分割、局部细查、工作结果：初始144/144；工作粗层默认144个粗框；帧证据单独开关（22条）；细查层显示200/1652候选，可按父窗口筛选。三个层级分别计数，不混成一个“信号框总数”。旧任务仍保留原显示及前200截断说明。实验数据 `docs/testing/coarse-v09.json`、`coarse-harness-v09.json`；阶段耗时和工具顺序见该任务 `timing.md`、`events.jsonl`、`dsh.jsonl`。

5项新增回归覆盖热噪声负例、短窄活动线索、计算批次不变性、尾部样本与真实间隙、先粗后细约束、长父区域后段细查和父子报告；现有场景4项、分割决策6项、API3项、网关4项通过，真实浏览器验证分层计数、帧开关、细查窗口、局部STFT与移动布局。不更新飞书，不改usrp_client。

### v0.8：帧边界与局部时频图

界面区分单帧主体估计、检测区域与活动索引跨度。已确认48.54608–48.57408ms对应28μs的RTS主体估计，不是Wi-Fi整次活动；位置未独立标定，ERP额外6μs空闲扩展不计入主体。解码器17MHz验证通带不称为实测占用带宽。

取消强制4px高矩形和固定±1%时间命中；未分辨短帧以细线标记，同像素多帧先放大再选择。绘制时对实际像素覆盖区间最大池化，不跳采192行预览；时间缩放到局部后由只读 `waterfall-window` 接口从原IQ重新计算STFT，单次最多20ms/4M样本，不启动新采集。原数据200MS/s时局部STFT步长5.12μs。

`compare_segmentations` 增加相对控制帧的时间膨胀和IoU诊断，明确其不是突发边界真值。报告与UI的 `segmentation_quality` 标记短区域占比高、区域远大于帧或缺少帧关联等启发式复核提示。任务完成不等于分割合格。

新增实验性 `multiscale_energy` 候选：只在请求频带估计背景，整个连续片段统一背景，分离宽窄尺度、物理时间平滑，不跨采集间隙；最多32M有效频带STFT单元。现有传统/自适应检测的背景统计也修正为请求频带，旧工件保留。受平滑/持续性限制，弱短活动可能漏检，且满带持续信号会影响背景估计。

同IQ实测数值回归保存于 `docs/testing/boundaries-v08.json`；实验中框数减少、部分时间边界收紧，但已知帧覆盖不足，因此没有替换1312区域的原工作结果。新候选保持实验性，不能声称修好了底层分割或提升了识别率。原工作分割仍需复核。通过3项多尺度测试（含热噪声功率斑点负例）、6项分割决策回归、4项网关回归（含局部STFT），以及浏览器微秒显示、邻帧命中、局部真实STFT和概览交互测试。不写飞书。

### v0.7：碎片归并与活动概览

`consolidate_signal_regions` 为 Harness 提供同频、近时的保守归并候选，时间间隔由模型选择，默认20μs、允许0..100μs，每任务最多2次。宽带内部窄带、不同采集连续段和明显不同频率保持独立；保存每组 `source_group_ids`，不删除原成员，不升级协议标签。归并后的边界是假设，邻近独立帧仍可能被并组，需比较后显式采用。

`scripts/replay_activity_v07.py` 在官方 Harness 中复用已有真实IQ和已验证帧，不重采集。实际回放 `task_activity_f913d09da8e8` 用时59.10秒，模型自主比较20μs和60μs方案，选择60μs方案：1579→1312工作区域、原成员全部保留、22条包级重叠证据不变。未测量准确率，不能把框少当作信号识别率提高。结果和耗时在 `docs/testing/activity-v07.json` 及任务目录下。

真实界面默认“活动概览”，按频率/带宽组织导航索引，绘制校验帧及选中活动最多40个成员框；“检测区域”显示工作分割全部区域。1312个区域组织为141项索引，其中4项同频帧索引对应22帧，137项待解释活动中114项尚未验证、12项有局部未通过的尝试、11项与有效帧重叠。索引不是设备、协议或单次突发数量；存在有效帧重叠也不确认整组。底层报告保留所有原始与替代方案，未安装的协议解码能力不得由展示分组冒充。

本轮新增4项回归覆盖归并成员保留、窄宽带分离、跨采集段分离和验证状态区分；分割决策6项、网关4项回归通过；浏览器验证概览/全部区域切换、选区、双轴缩放、桌面与移动布局。按用户2026-10-23后续要求，不再同步飞书文档。

### v0.6：Harness 自主分割决策

官方 dsh 模型根据 `inspect_spectrum` 的稀疏数值概览、IQ质量和已有识别证据，自主选择 `segment_spectrum(strategy, fft_size, threshold_db, decision_reason)`。可选策略是 `traditional` 与 `adaptive_hysteresis`，不是固定的传统检测流水线。概览最多抽样256窗，可能漏掉短包，不能代替完整检测。

`compare_segmentations` 对同一IQ的替代方案返回碎片、分辨率和有效包重叠统计，不自动选赢家。模型通过 `select_segmentation` 记录工作方案与理由；已有协议证据发生变化时必须重新比较。减少框数不是准确率提升证明，自适应背景估计也可能削弱连续信号。

每任务最多3次全带尝试及2次局部复查，硬预算只限制资源，不替模型选择算法。报告保留所有方案，但 `signal_groups` 只计工作分割；没有显式选择则标记初始方案未优化，有新协议证据则标记需要重新评估。初始与最终可视化阶段分别使用初始方案和工作方案，替代方案不会叠加计数。

真实IQ和官方DeepSeek API回放：`PYTHONPATH=src .venv/bin/python scripts/replay_segmentation_choice.py`。此脚本会调用付费模型，但不启动新RF采集；保存于 `docs/testing/harness-segmentation-choice-v06.json`、对应任务的 `report.md`、`timing.md` 和 `dsh.jsonl`。调用顺序和参数由模型产生，脚本没有硬编码分割工具序列。新增领域回归：`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_segmentation_tools.py -v`。

~~~bash
cd /home/sribd/spectrum_agent
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
~~~

测试不使用真实USRP，本地模型fixture不产生DeepSeek费用。API测试使用独立临时数据库和暂停的队列worker，避免运行中的服务消费测试任务；实机测试单独记录，不与合成数据混淆。

Wi-Fi源码固定为bastibl/gr-ieee802-11 maint-3.10提交ad0598e4a874f4b8e1f391a1e0323e80df2b34ff。原GNU Radio导出CMake配置含旧Python路径，兼容修正仅写在该副本的CMakeLists中，补充Boost/Eigen头文件也只解压到runtime/build-deps。构建使用pybind11 2.12匹配现有GNU Radio绑定ABI；scripts/build-wifi.sh为本工程内重建命令。离线发射链只产生测试IQ，不连接任何RF发射设备。
