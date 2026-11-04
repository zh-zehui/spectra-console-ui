import { spawn } from 'node:child_process';
import { appendFileSync, readFileSync, readdirSync } from 'node:fs';
import { performance } from 'node:perf_hooks';
import { defineTool } from '@deepseek-ai/dsh-tools';

export const name = 'spectrum-tools';
export const inject = ['tools'];
const num = (description, required = true) => ({type:'number',description,...(required?{required:true}:{})});
const str = (description, required = true) => ({type:'string',description,...(required?{required:true}:{})});
const integer = (description, required = true) => ({type:'integer',description,...(required?{required:true}:{})});
const definitions = [
 ['list_drone_video_profiles','List installed recording-derived drone reference modes,parameters and validation scope. Not evidence of current signal presence. Query before selecting the observed bandwidth and reference mode.',{}],
 ['analyze_drone_hopping','Independent frequency-agile and bandwidth-changing drone reference investigation; does NOT replace the fixed-frequency tool or Wi-Fi decoding. Rediscover energy-proposed centers throughout the entire saved time and test selected installed modes. Complete means all time blocks/proposals checked,not all RF signals detected. No aircraft identity,MAC CRC or video decoding. Repeated unchanged calls advance checkpoints,including partially checked blocks.',
  {capture_id:str('Original task-owned saved capture ID'),reason:str('Why frequency/bandwidth changes are suspected; observations and user context'),evidence_ids:{type:'array',items:{type:'string'},required:true},profile_ids:{type:'array',items:{type:'string'},description:'Installed modes to test; omit for all; explicitly select fitting modes if recorded scope/rate is narrow'},max_blocks:integer('1..64; default1 completed10ms blocks per call',false),max_probes:integer('1..4096; default128 hypotheses per call; use4096 for multi-block batches and repeat until coverage.complete',false),discovery_threshold_db:num('Energy proposal threshold3..15dB,default6; weak/unproposed activity remains unresolved',false),acquisition_threshold:num('0.4..0.9,default0.55',false),signature_threshold:num('0.6..0.99,default0.72',false)}],
 ['analyze_drone_video_link','Add an independent drone video recording-signature check without changing Wi-Fi. Full saved-time checkpointed10ms blocks. Choose an installed reference using list_drone_video_profiles and current observed bandwidth/structure evidence. NOT universal DJI identity,MAC CRC,NR verification or video decryption. Repeat same parameters until coverage.complete.',
  {capture_id:str('Task-owned original capture'),center_hz:num('Observed target center in Hz; selected profile nominal channel must fit analysis band'),reason:str('Why this target and profile; user context is not protocol confirmation'),evidence_ids:{type:'array',items:{type:'string'},required:true},profile_id:str('Installed profile ID; use list_drone_video_profiles. Default zc293_u123_30k_observed_v1 is10MHz only.',false),max_blocks:integer('1..64,default64; repeat unchanged to complete',false),search_span_hz:num('0..40MHz optional coarse center search around center_hz; use20MHz if envelope center is uncertain. Searches first10ms only,then locks observed best center.',false),acquisition_threshold:num('0.4..0.9,default0.55',false),signature_threshold:num('0.6..0.99,default0.72',false)}],
 ['analyze_signal_windows','Batch1..4 model-selected residual windows and numerical metrics in one tool call. YOU choose center,width,rate,time and features/quality/bursts/sweeps/ofdm. No protocol inference or RF. Each measurement keeps its coverage limits.',
  {capture_id:str('Original capture'),reason:str('Observed residual evidence and why these measurements'),evidence_ids:{type:'array',items:{type:'string'},required:true},selections:{type:'array',required:true,items:{type:'object',properties:{center_hz:{type:'number'},bandwidth_hz:{type:'number'},output_rate_hz:{type:'number'},sample_start:{type:'integer'},sample_stop:{type:'integer'},metrics:{type:'array',items:{type:'string'}}},additionalProperties:false}}}],
 ['plan_protocol_checks','YOU select profiles/channels from priors and observations. Declare baseline checks over the ENTIRE model-resolved time interval. Each chosen profile/channel is fully swept in checkpointed batches. Rejects wide-envelope centers accidentally supplied to20MHz Wi-Fi receivers. No RF. Returns plan_id; call run_protocol_batch next.',
  {capture_id:str('Original capture'),checks:{type:'array',required:true,items:{type:'object',properties:{profile:{type:'string'},center_hz:{type:'number'},bandwidth_hz:{type:'number'},output_rate_hz:{type:'number'},channel:{type:'integer'},duplex_mode:{type:'string'},ssb_scs_khz:{type:'integer'},ssb_pattern:{type:'string'},reason:{type:'string'},nonstandard_center_reason:{type:'string'}},additionalProperties:false}},reason:str('Why these protocols and channels,including any excluded applicable candidates'),evidence_ids:{type:'array',items:{type:'string'},required:true},window_s:num('Main receive window seconds,default0.01',false),context_s:num('Context seconds each side,default0.002',false)}],
 ['run_protocol_batch','Execute the next bounded batch of a model-selected plan. Up to64 extraction units per call; internally many10ms receive windows. Checkpoints prevent recomputation. Repeat SAME plan_id until complete=true. Errors remain incomplete and never mean no signal. No RF.',
  {plan_id:str('Plan returned by plan_protocol_checks'),max_units:integer('1..64,default64; use64 to minimize model round trips',false),retry_errors:{type:'boolean',description:'Retry failed units after inspecting their error; defaultfalse'}}],
 ['inspect_protocol_progress','Read exact per-profile/channel/time coverage and unresolved units. No DSP or RF.',{plan_id:str('Protocol plan')}],
 ['inspect_wifi_headers','Observe CRC-checked HT20/HT40 and VHT20/40/80/160 SIG headers from one extracted20MHz slice. Reports signalled bandwidth,MCS,streams,coding and L-SIG timing. Does NOT decode wideband MAC; identical headers across slices can corroborate a wide PPDU. Also retains any20MHz MAC FCS results. Requires20MS/s,>=17MHz passband,<=0.2s.',{artifact_id:str('Task-owned20MHz observation subband; select center and time from evidence')}],
 ['analyze_spectrum_windows','Checkpointed full-IQ coarse STFT using model-selected main windows (e.g.10ms) with overlapping context. Repeated identical calls advance to completion; returned stitched segmentation is the original coarse parent. Original coordinates retained; associations are hypotheses,not protocol confirmation. YOU then select protocol extraction/verification tools per window.',
  {capture_id:str('Task-owned original capture'),window_s:num('Main window seconds,default0.01',false),context_s:num('Context seconds each side,default0.002; extend if evidence requires',false),max_windows:integer('1..64 windows this call,default64',false),fft_size:integer('512/1024/2048/4096',false),time_bin_s:num('Time cell20us..2ms',false),frequency_bin_hz:num('Frequency cell100kHz..5MHz',false),threshold_db:num('3..15dB',false),sample_start:integer('Optional original start sample',false),sample_stop:integer('Optional original exclusive stop sample',false)}],
 ['analyze_ofdm_structure','Measure CP-based useful symbol duration,subcarrier spacing and guard interval candidates on a selected <=40MS/s subband. Does NOT confirm Wi-Fi,LTE or drone protocol. Coverage and tested parameter limits are explicit.',
  {artifact_id:str('Task-owned filtered IQ subband'),sample_start:integer('Start sample',false),sample_stop:integer('Exclusive stop sample',false)}],
 ['plot_spectrogram','Render full-record STFT into the EXISTING web waterfall control. No segmentation,protocol identification or RF acquisition. Use for requests to only draw a time-frequency plot.',{capture_id:str('Task-owned saved capture ID; returned by resolve_task_intent for source-task replay')}],
 ['resolve_task_intent','FIRST tool for a new task. YOU interpret the full original user prompt: separate global survey scope from target-signal hints. Form numbers are provisional defaults. Supply numeric SI parameters and exact evidence_quotes; the tool only validates, persists intent and returns execution instructions. No RF or DSP. Do not silently guess conflicting explicit requirements. Cannot change resolved intent later.',
  {center_hz:num('Global analysis center Hz; omit for source_task_id if user did not specify a new scope: source metadata is inherited',false),span_hz:num('Global analysis span Hz; omit for source_task_id if unspecified by user',false),
   evidence_quotes:{type:'array',items:{type:'string'},required:true,description:'Exact quotes copied from the original user prompt supporting this interpretation'},
   reason:str('Concise public explanation distinguishing survey scope and target hints'),
   source_task_id:str('Existing task ID explicitly named by the user; attaches its saved IQ and prohibits any new RF capture. Do not substitute a new capture if loading fails.',false),
   inherit_analysis:{type:'boolean',required:true,description:'True ONLY when user explicitly asks to continue previous analysis evidence. Reusing original raw IQ means false. A source without analysis cannot be continued.'},
   drone_video_analysis_requested:{type:'boolean',description:'True when user explicitly asks to find/test a drone video link. Focused recording-signature checks may set protocol_analysis_requested=false; preserve existing Wi-Fi evidence if continuing.'},
   drone_hopping_analysis_requested:{type:'boolean',description:'True for frequency-agile/hopping or bandwidth-changing drone links; requires analyze_drone_hopping over complete requested time. Fixed-frequency checks alone cannot fulfill this request.'},
   visualization_requested:{type:'boolean',required:true,description:'User requests a spectrum/time-frequency plot; independent of signal analysis. True for only plotting.'},
   acquisition_requested:{type:'boolean',required:true,description:'True only when user requests a NEW acquisition. False for existing task IQ. Never reacquire to replace unavailable saved IQ.'},
   task_kind:str('Your short description of the user intent; free text,not an enumerated task class',false),
   analysis_window_s:num('User-requested analysis main window seconds;10ms=0.01,NOT capture duration or STFT hop',false),
   protocol_analysis_requested:{type:'boolean',required:true,description:'True for signal recognition/decoding; false for segmentation or generic-feature-only work'},
   full_local_review_requested:{type:'boolean',required:true,description:'True only for explicitly required local resegmentation of EVERY coarse parent. General recognition does not imply this expensive step.'},
   comprehensive_protocol_survey:{type:'boolean',required:true,description:'True for comprehensive/all-signal surveys across the band. Applicable installed candidate channels must be covered,including weak ones. False only for explicitly focused checks.'},
   wide_wifi_headers_requested:{type:'boolean',required:true,description:'True when user requests HT40/VHT80/wide Wi-Fi format identification; requires a SIG-capable profile in the full candidate coverage.'},
   windowed_analysis_requested:{type:'boolean',required:true,description:'True when user explicitly asks to split analysis into time windows,e.g.10ms; then analysis_window_s must be supplied.'},
   time_subset_requested:{type:'boolean',required:true,description:'True ONLY for an explicitly requested time crop/prefix in the ORIGINAL prompt. False for all recorded time. A provisional UI observation duration is NOT a subset request. When false OMIT analysis_start_s/stop_s and inherit actual saved duration.'},
   analysis_time_evidence_quote:str('Exact original quote supporting explicitly requested time crop; mandatory when time_subset_requested=true',false),
   analysis_start_s:num('Explicit analysis time subset start;default0',false),analysis_stop_s:num('Explicit analysis time subset exclusive end;default entire recording,not analysis deadline',false),
   analysis_requested:{type:'boolean',description:'Your interpretation of whether user requested signal analysis; false for only recording/storage'},
   targets:{type:'array',items:{type:'object',properties:{label:{type:'string'},frequency_low_hz:{type:'number'},frequency_high_hz:{type:'number'},bandwidth_hz:{type:'number'}},additionalProperties:true},description:'User-reported target hints,not protocol confirmations'},
   sample_rate_hz:num('Optional resolved acquisition sample rate Hz; preserve saved IQ rate',false),observation_s:num('Optional capture duration seconds; preserve saved IQ duration',false),
   acquisition_center_hz:num('Optional acquisition LO Hz if explicitly different from survey center',false),deadline_s:num('Optional user-requested analysis deadline seconds; not capture duration',false)}],
 ['follow_ble_aux','Follow one AuxPtr from a saved CRC-valid BLE frame using existing IQ only. Chooses bounded time/channel/LE1M or LE2M extraction from that pointer, then CRC verification. No RF, no recursive chain following. Returns timing candidates, never advertiser identity; outside-recording and unsupported Coded are explicit. YOU decide whether and which pointer to follow.',
  {verification_id:str('Task-owned ble_verification artifact ID'),frame_index:integer('Zero-based index into the full saved frames, not a summary index')}],
 ['localize_verified_frames','Add recovered-clock timing estimates to previously saved IEEE802.15.4 CRC-valid frames. Reruns the original subband and matches exact frame bytes; produces timing overlay, never duplicate verified frame counts. Exposes uncalibrated accumulated clock uncertainty. Use to resolve extraction-window-only evidence before final reporting.',
  {verification_id:str('Task-owned ieee154_verification artifact ID')}],
 ['review_activity_batch','Batch full-parent local segmentation and generic feature review across ALL coarse activity parents. Checkpointed, at most8 windows/call. YOU choose strategy, FFT and threshold. Repeatedly call until progress.remaining_regions=0. Does not choose protocol decoders or confirm protocols; afterward select evidence-based verification tools. Never substitutes a representative family for unchecked parents.',
  {segmentation_id:str('Original coarse segmentation'),review_id:str('Scene review for this segmentation'),strategy:str('traditional,adaptive_hysteresis,multiscale_energy,local_power_tiles; defaultlocal_power_tiles',false),fft_size:integer('512,1024,2048,4096; default2048',false),threshold_db:num('3..30dB; default6',false),max_regions:integer('1..8 parents/call; default8',false)}],
 ['query_band_priors','Read versioned prior knowledge for exactly the given center and span. NOT evidence of observed signals.',
  {center_hz:num('RF center Hz'),span_hz:num('Requested full bandwidth Hz'),region:str('Optional region; absence stays unknown',false)}],
 ['inspect_capabilities','Inspect the powered USRP, daughterboard and software capabilities; does not capture.',{}],
 ['make_synthetic_capture','Create clearly labeled synthetic CW and noise-burst data for tests only.',
  {sample_rate_hz:num('Sample rate up to 20MHz',false),center_hz:num('Metadata center frequency',false),duration_s:num('Up to 0.5s',false)}],
 ['capture_iq','Receive-only capture on USRP. Respect task center/span/duration exactly. Do not repeat completed capture.',
  {center_hz:num('RF center Hz'),span_hz:num('Full requested span Hz'),duration_s:num('Observation seconds 0.001..10'),gain_db:num('RX gain dB, default20',false),sample_rate_hz:num('Optional rate, hardware must provide guard band',false)}],
 ['coarse_segment_spectrum','FIRST segmentation stage: STFT across the entire task-owned IQ (all continuous samples), aggregate LINEAR power into coarse time/frequency cells with mean and separately normalized peak branch, then propose whole-record coarse activity envelopes. FFT batches NEVER create detection boundaries. Preserves raw IQ and positive tiles. NOT packet/protocol/device boundaries. Choose promising or residual regions for fine analysis next.',
  {capture_id:str('Task-owned capture'),fft_size:integer('512,1024,2048,4096; default1024',false),
   time_bin_s:num('Coarse cell20us..2ms, default100us; >= FFT hop/sample rate',false),
   frequency_bin_hz:num('Coarse cell100kHz..5MHz, default1MHz; >= FFT bin spacing',false),
   threshold_db:num('Background margin3..15dB, default6',false),decision_reason:str('Observed reason for coarse scale choices',false)}],
 ['inspect_spectrum','Inspect numerical PSD and time activity in up to256 sampled FFT windows across the recording. Choose detection strategy from observations. Sparse overview may miss bursts; NOT full detection coverage.',
  {artifact_id:str('Task-owned IQ or subband'),fft_size:integer('512,1024,2048,4096; default1024',false)}],
 ['segment_spectrum','Run a detector chosen by YOU. multiscale_energy uses full-continuous-segment background and independent broad/narrow layers without FFT-batch seams (32M STFT-cell limit); denoising may miss weak/very short activity. traditional and adaptive_hysteresis remain alternatives. Compare boundaries and weak/overlap risks, not box count alone. At most3 attempts/task.',
  {capture_id:str('Task-owned IQ id'),strategy:str('traditional, adaptive_hysteresis or multiscale_energy; YOU choose from observations',false),
   fft_size:integer('512,1024,2048 or4096; choose time/frequency tradeoff',false),threshold_db:num('Detection margin3..30dB, default12',false),
   decision_reason:str('Observed evidence, chosen strategy/parameters and expected tradeoff',false)}],
 ['compare_segmentations','Numerically compare 1..4 candidates from SAME recording: fragmentation, resolution, known packet overlap and coverage. No automatic winner or ground-truth accuracy. Recompare after new verification evidence.',
  {segmentation_ids:{type:'array',items:{type:'string'},required:true,description:'Task-owned full-band segmentation IDs, distinct'}}],
 ['consolidate_signal_regions','Propose conservative merging of same-band near-time fragments. Preserves every member and unknown label, separates narrow-in-wide activity, never crosses capture gaps. NOT a validated identity or a new decoded signal. Compare original/merged and select explicitly; at most2 proposals.',
  {segmentation_id:str('Original detector segmentation, not already consolidated'),reason:str('16..2000 characters: observed fragmentation and tradeoff'),max_gap_s:num('Maximum temporal gap0..0.0001s, default0.00002',false)}],
 ['fuse_refined_regions','Propose mixed boundaries from original coarse parents and local refinements. Requires a post-refinement scene review. Rejects dense/invalid proposals and retains rejected parents whole. Accepted fine geometry is clipped to the requested window; remaining coarse support stays UNKNOWN residual rectangles. Geometry safeguards are NOT calibrated accuracy. Compare original and fused candidates then explicitly select; at most2 proposals.',
  {segmentation_id:str('Original coarse segmentation, not already fused'),refinement_ids:{type:'array',items:{type:'string'},required:true,description:'1..8 scene refinement IDs, one per parent'},review_id:str('Post-refinement scene review of original coarse segmentation'),reason:str('16..2000 characters: evidence, tradeoff and retained uncertainty')}],
 ['select_segmentation','Record YOUR evidence-based working segmentation choice using a comparison. All originals remain. Rejects a candidate losing known packet overlap relative to a compared alternative. Fewer boxes is not sufficient evidence.',
  {segmentation_id:str('Chosen full-band candidate'),comparison_id:str('Comparison containing chosen candidate'),reason:str('16..2000 characters: observations, tradeoffs, retained uncertainties and stopping reason')}],
 ['extract_subband','GNU Radio frequency-translating FIR filter and decimation, optional polyphase resampling. Retain burst margins.',
  {capture_id:str('Task-owned IQ id'),center_hz:num('Target center Hz'),bandwidth_hz:num('Full subband passband Hz'),
   sample_start:integer('Start file sample, default0',false),sample_stop:integer('Exclusive end sample',false),
   output_rate_hz:num('Optional output rate >=1.1*bandwidth_hz and <=input sample rate; omit for safe native rate; BLE needs4MHz',false),
   margin_s:num('Context padding on each side, default0.005s, range0.001..0.02; clipped at capture gaps',false)}],
 ['profile_signal','Compute numerical bandwidth/power/flatness/phase features. Does not guess a protocol.',
  {artifact_id:str('Task-owned IQ or subband id')}],
 ['analyze_iq_quality','NumPy/SciPy IQ diagnostics: gaps, DC, I/Q statistics, possible normalized saturation. At most2M samples, explicit coverage; not calibrated receiver faults.',
  {artifact_id:str('Task-owned IQ or subband id')}],
 ['analyze_burst_timing','SciPy envelope burst/periodicity analysis within continuous segments. Use a representative filtered subband; not protocol or source identification.',
  {artifact_id:str('Task-owned IQ/subband id'),bin_s:num('Envelope bin seconds1e-6..0.001, default0.00002',false)}],
 ['analyze_sweep_structure','NumPy/SciPy STFT dominant linear-ridge analysis for chirp/sweep candidates. Does NOT identify LoRa/radar/drone by itself.',
  {artifact_id:str('Task-owned IQ/subband id')}],
 ['summarize_spectral_activity','Group all initial detected events into frequency-grid activity families; timing and repeated activity hints, not proven transmitters or hopping networks.',
  {segmentation_id:str('Initial full-band segmentation artifact')}],
 ['export_sigmf_recording','Export local IQ and SigMF1.2.6 metadata validated with sigmf library; optional detected-event annotations remain unknown, never ground truth. No external upload.',
  {artifact_id:str('Task-owned IQ/subband id'),segmentation_id:str('Optional initial segmentation of this exact recording',false)}],
 ['match_signal_hypotheses','Match observed groups to saved priors. Heuristic ranking is not a calibrated probability.',
  {segmentation_id:str('Segmentation artifact'),prior_snapshot_id:str('Prior snapshot artifact')}],
 ['list_signal_groups','Page through all observed groups, sorted by strength,duration,bandwidth or time. Use for diversity beyond truncated summaries.',
  {segmentation_id:str('Segmentation artifact'),sort_by:str('strength,duration,bandwidth,time',false),
   offset:integer('Zero-based page offset',false),limit:integer('1..50 groups',false)}],
 ['analyze_bluetooth_classic','Analyze a focused classic Bluetooth BR/EDR channel with open-source libbtbb exact access-code search. Requires1MHz passband at a nominal2402..2480MHz center in1MHz steps, output4MS/s. Returns synchronization CANDIDATES, not HEC/CRC-validated frames, full packets or confirmed region labels. Zero hits does not establish absence. Use for measured narrow/GFSK-like hypotheses, not every unknown.',
  {artifact_id:str('Task-owned focused subband'),frequency_offset_hz:num('Optional CFO center correction -200k..200kHz, default0; bounded residual trials are documented',false)}],
 ['verify_protocol','Validate BLE, legacy Wi-Fi, IEEE802.15.4 frames, or LTE PSS/SSS and CRC-checked PBCH/MIB. LTE requires central1.4MHz passband at1.92MS/s,40ms..1s continuous IQ; prefer the whole100ms recording. Short detection boxes cannot supply enough LTE context. LTE sync alone is only a candidate; PBCH CRC confirms a broadcast block, not an entire region. NR FR1 supported: nr_pbch,15kHz/A at4.4MHz passband/7.68MS/s;30kHz/B,C at8.8MHz/15.36MS/s,20..500ms continuous input. SSB center is not necessarily LTE/carrier center; use NR synchronization raster. Only NR PBCH CRC24C plus MIB unpack confirms a broadcast block.',
  {artifact_id:str('Task-owned subband id'),protocol:str('wifi_crafter_all: libcrafter combined DSSS/CCK,legacy OFDM,HT20 single-data-stream including A-MPDU,BCC/LDPC; wifi_crafter_dsss: focused802.11b DSSS/CCK1,2,5.5,11Mb/s. Both require20MS/s,17..18MHz extracted passband;18MHz preferred for DSSS. Fixed CF32-to-CS8 quantization is recorded. No HT40/VHT/HE/EHT. wifi_gr20_siso: complementary GR-WiFi20MHz legacy/HT(nonaggregated)/VHT at20MS/s,17MHz passband,MCS0..7. Retain wifi_legacy_ofdm evidence; no wideband,b/ax/be support in that old receiver. Choose by observed evidence rather than trying every decoder on every unknown. BLE: ble_1m_advertising primary37..39 at4MS/s; ble_aux_1m channels0..36,2MHz at4MS/s; ble_aux_2m channels0..36,3MHz at8MS/s. Prefer follow_ble_aux for CRC-valid AuxPtr. Also ieee802154_oqpsk,lte_pbch,nr_pbch'),
   channel:integer('Required for802.15.4:11..26; BLE primary37..39, auxiliary0..36; omitted forWi-Fi/LTE/NR',false),duplex_mode:str('LTE: auto,tdd,fdd; NR auto uses A/B-FDD,C-TDD validated profiles',false),ssb_scs_khz:integer('NR only:15 or30, default30',false),ssb_pattern:str('NR only:A with15kHz; B orC with30kHz, defaultC for30',false)}],
 ['assess_signal_scene','Review initial segmentation together with all available verification evidence. Returns heuristic quality flags and local review targets; not measured accuracy.',
  {segmentation_id:str('Initial full-band segmentation artifact')}],
 ['refine_signal_region','Feedback-driven local segmentation. Preserves initial segmentation and maps events to original IQ. Calls follow task max_local_refinements, distinct target and dynamic time-reserve guards; inspect analysis_budget on each result. Reverify returned subband or extract protocol-specific subband next.',
  {segmentation_id:str('Initial segmentation'),group_id:str('Observed group to review'),
   reason:str('Observation-based reason and expected test;8..1200 characters'),
   evidence_ids:{type:'array',items:{type:'string'},required:true,description:'1..6 task-owned evidence IDs; must include an assess_signal_scene review for this segmentation'},
   fft_size:integer('512,1024,2048,4096; default2048',false),threshold_db:num('Detection margin3..30dB, default12',false),
   offset_s:num('Fine window start relative to selected parent region0..10s, default0; choose later windows for long coarse regions',false),
   duration_s:num('Fine window length0.0001..0.02s, default0.02; context margins are added',false),
   strategy:str('traditional, adaptive_hysteresis, multiscale_energy or local_power_tiles; choose based on target and compare evidence, defaultadaptive_hysteresis',false),
   center_hz:num('Optional focused subband center Hz; must overlap parent and remain inside requested RF band',false),
   bandwidth_hz:num('Optional focused subband full passband Hz; do not analyze the entire broad parent when targeting a narrow signal',false),
   output_rate_hz:num('Optional output rate >=1.1*bandwidth_hz and <=input sample rate; do not set equal to bandwidth; omit for safe native rate',false),
   margin_s:num('Filter/sync context seconds0.001..0.02; default0.001; context detections excluded from target candidates',false),
   local_time_bin_s:num('local_power_tiles linear-power time tile1us..1ms, default20us; actual tile >= STFT hop',false),
   local_frequency_bin_hz:num('local_power_tiles frequency tile1kHz..2MHz, default200kHz; actual tile >= FFT bin',false)}],
 ['compare_local_refinements','Compare1..3 local alternatives on IDENTICAL original parent/time/frequency scope. Recomputes current contained packet-reference coverage after any new decoding; rejects comparisons mixing context-inclusive legacy counts or different scopes. No automatic winner or calibrated accuracy. Use a fresh scene review and fuse_refined_regions only when justified.',
  {refinement_ids:{type:'array',items:{type:'string'},required:true,description:'1..3 distinct task-owned scene refinement IDs on identical requested scope'}}],
 ['plan_unknown_investigations','Build an evidence-bound queue over all coarse unknown regions. Selects representative width/duration/strength strata and similarity families, NOT transmitters. Includes exact protocol attempt windows and partial analysis coverage. A valid frame never labels a whole group. Refresh after new analysis; deferred regions reopen on fresh overlapping evidence. Does not execute DSP or pick a protocol for you.',
  {segmentation_id:str('Original coarse segmentation whose regions are being investigated'),max_targets:integer('Representative targets1..8, capped by task candidate budget; default4',false)}],
 ['record_investigation_decision','Record YOUR evidence-based next action or deferral for an unknown target. Refresh queue after evidence changes. Deferral retains unknown activity, not a rejection of all signals/protocols. Reference overlapping task-owned evidence; state missing evidence or specific next tool/window/parameter change. Avoid repeating identical decoder attempts.',
  {queue_id:str('Fresh queue_id from plan_unknown_investigations'),group_id:str('Target group in that queue'),action:str('continue or defer'),reason:str('16..1600 characters: observations, scope, budget tradeoff and uncertainty'),evidence_ids:{type:'array',items:{type:'string'},required:true,description:'0..8 relevant evidence IDs; continue requires at least1'},next_step:str('8..1000 characters: specific next action or missing evidence')}],
 ['build_report','Build deterministic evidence report, all groups/unknowns, stage timings and analysis budget. Record why work stops; completed checks never mean exhaustive recognition.',{stop_reason:str('requested_checks_complete, tool_capability_limit, time_budget, call_budget, model_round_budget or other',false),stop_detail:str('16..1600 characters: stopping basis, unchecked scope and next action',false)}],
];

export function apply(ctx) {
  const root = process.env.SPECTRUM_ROOT;
  const task = process.env.SPECTRUM_TASK_ID;
  if (!root || !/^[A-Za-z0-9_-]{1,100}$/.test(task ?? '')) throw new Error('Missing valid task binding');
  const emit = event => appendFileSync(root+'/runs/'+task+'/events.jsonl',
    JSON.stringify({task_id:task,utc:new Date().toISOString(),...event})+'\n');
  let steps=0;
  let maxSteps=12;
  let followupStart=null;
  const protocolComplete=()=>{
    const directory=root+'/runs/'+task;
    const files=readdirSync(directory).filter(n=>n.startsWith('protocol-progress-')&&n.endsWith('.json'));
    if(!files.length)return false;
    return files.every(name=>{const state=JSON.parse(readFileSync(directory+'/'+name,'utf8'));
      const index=JSON.parse(readFileSync(directory+'/index/'+state.plan_id+'.json','utf8'));
      const plan=JSON.parse(readFileSync(index.path,'utf8'));
      return state.completed.length===plan.units.length&&!state.errors.length;});
  };
  ctx.on('agent/pre-step',async(payload,next)=>{
    const request=JSON.parse(readFileSync(root+'/runs/'+task+'/request.json','utf8'));
    maxSteps=request.budgets?.max_model_rounds ?? 12;
    if(++steps>maxSteps) throw new Error('MODEL_ROUND_LIMIT');
    if(request.task_intent?.protocol_workflow_version===2&&request.task_intent?.protocol_analysis_requested&&!request.task_intent?.full_local_review_requested&&!request.task_intent?.drone_video_analysis_requested&&protocolComplete()&&followupStart===null){
      followupStart=steps;emit({stage:'followup_model_budget',phase:'start',step:steps,followup_rounds:3,note:'Three focused follow-up rounds after full baseline coverage; then report. Explicit exhaustive local review bypasses this performance budget.'});
    }
    return next();
  });
  const attempts=new Map();
  ctx.on('agent/assistant-stream',({frame})=>{
    if(frame.type==='start') {
      attempts.set(frame.attemptId,{start:performance.now(),first:null});
      emit({stage:'model_stream',phase:'start',span_id:frame.attemptId});
    } else {
      const item=attempts.get(frame.attemptId);
      if(!item) return;
      if(frame.type==='chunk' && item.first===null) item.first=performance.now()-item.start;
      if(frame.type==='end') {
        emit({stage:'model_stream',phase:'end',span_id:frame.attemptId,
          elapsed_ms:performance.now()-item.start,first_stream_chunk_ms:item.first,
          status:frame.outcome.kind==='committed'?'settled':'abandoned',
          note:'Observable provider stream interval; not server internal reasoning time'});
        attempts.delete(frame.attemptId);
      }
    }
  });
  for (const [toolName, description, parameters] of definitions) {
    const dispose = ctx.tools.register(defineTool({
      name:toolName, description, parameters, parallelSafe:false, timeoutMs:180000,
      output:{schema:{type:'object',additionalProperties:true},
        render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},
      async execute(args, exec) {
        const started = performance.now();
        return await new Promise((resolve,reject)=>{
          const env = {...process.env,PYTHONPATH:root+'/src',PYTHONDONTWRITEBYTECODE:'1'};
          delete env.DEEPSEEK_API_KEY;
          const child = spawn(root+'/.venv/bin/python',['-m','spectrum_agent.worker'],
                              {cwd:root,env,stdio:['pipe','pipe','pipe']});
          let stdout='',stderr='';
          child.stdout.on('data',data=>{stdout+=data.toString();if(stdout.length>2000000) child.kill('SIGTERM');});
          child.stderr.on('data',data=>{if(stderr.length<100000)stderr+=data.toString();});
          const abort=()=>child.kill('SIGTERM');
          exec.signal?.addEventListener('abort',abort,{once:true});
          if(exec.signal?.aborted) abort();
          child.on('error',reject);
          child.on('close',(code)=>{
            exec.signal?.removeEventListener('abort',abort);
            appendFileSync(root+'/runs/'+task+'/worker-runtime.log',
              JSON.stringify({tool:toolName,code,elapsed_ms:performance.now()-started,stderr:stderr.slice(-5000)})+'\n');
            const line=stdout.split('\n').findLast(l=>l.startsWith('SPECTRUM_RESULT:'));
            if(!line) { reject(new Error('Domain worker exited '+code+' without a result'));return; }
            try {resolve(JSON.parse(line.slice(16)));} catch {reject(new Error('Invalid worker response'));}
          });
          child.stdin.end(JSON.stringify({task_id:task,tool:toolName,arguments:args,call_id:exec.callId}));
        });
      }
    }));
    ctx.effect(()=>dispose);
  }
  // Ensure no coding/shell/network tools can reach the read-only legacy project.
  const allowed = new Set(definitions.map(d=>d[0]));
  ctx.tools.guard((call)=> {
    const toolName=call.name ?? call.toolName ?? call.tool?.name;
    if(toolName && !allowed.has(toolName)) return 'Only spectrum domain tools are allowed';
    const request=JSON.parse(readFileSync(root+'/runs/'+task+'/request.json','utf8'));
    if(request.task_intent?.protocol_workflow_version===2&&request.task_intent?.comprehensive_protocol_survey&&!protocolComplete()){
      const firstSweep=new Set(['resolve_task_intent','query_band_priors','inspect_capabilities','capture_iq','make_synthetic_capture','inspect_spectrum','analyze_iq_quality','coarse_segment_spectrum','analyze_spectrum_windows','plan_protocol_checks','run_protocol_batch','inspect_protocol_progress','plot_spectrogram','list_drone_video_profiles','analyze_drone_video_link','analyze_drone_hopping','build_report']);
      if(!firstSweep.has(toolName))return 'BASELINE_FIRST: finish model-selected plan_protocol_checks/run_protocol_batch before individual decoder probes or local refinements. Failed coverage stays partial. Use priors and STFT to choose channels/profiles.';
    }
    if(followupStart!==null&&steps>=followupStart+3&&toolName!=='build_report')return 'FOLLOWUP_REPORT_RESERVE: baseline coverage is complete. Focused follow-up rounds exhausted; build_report now,retain unresolved structures and state limitations. Do not start another model/DSP loop.';
    if(maxSteps>=4 && steps>=maxSteps-2 && toolName!=='build_report')
      return 'MODEL_ROUND_RESERVE: remaining rounds are reserved for build_report and a final summary; retain unknowns, do not start more analysis.';
    return undefined;
  });
}
