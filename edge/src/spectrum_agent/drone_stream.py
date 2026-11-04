"""Headless signature event stream from cf32 stdin or recorded IQ. No model calls or RF control."""
import argparse,json,sys,time
from pathlib import Path
import numpy as np
from .common import DomainError
from .drone_video import DEFAULT_PROFILE,profile,StreamingFrontend,SignatureDetector


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',default='-',help='cf32 file or - for continuous stdin')
    ap.add_argument('--sample-rate-hz',type=float,required=True)
    ap.add_argument('--input-center-hz',type=float,required=True)
    ap.add_argument('--target-center-hz',type=float,required=True)
    ap.add_argument('--block-ms',type=float,default=10)
    ap.add_argument('--profile',default=DEFAULT_PROFILE)
    ap.add_argument('--node-id',default='jetson-edge')
    ap.add_argument('--gain-db',type=float)
    ap.add_argument('--hardware-start-s',type=float)
    ap.add_argument('--max-blocks',type=int)
    args=ap.parse_args()
    if not .5<=args.block_ms<=100:raise DomainError('INVALID_ARGUMENT','block-ms must be0.5..100')
    p=profile(args.profile)
    if round(p['sample_rate_hz']*args.block_ms/1000)+p['fft_size']*5>2_000_000:raise DomainError('RESOURCE_LIMIT','Reduce block-ms for this wider profile; canonical detector limit is2M samples')
    frontend=StreamingFrontend(args.sample_rate_hz,args.input_center_hz,args.target_center_hz,p['sample_rate_hz'],p['nominal_channel_bandwidth_hz']);detector=SignatureDetector(p)
    source=sys.stdin.buffer if args.input=='-' else Path(args.input).open('rb')
    count=max(1,round(args.sample_rate_hz*args.block_ms/1000));times=[];references=0;blocks=0
    def emit(v):print(json.dumps(v,allow_nan=False),flush=True)
    def publish(rows):
        nonlocal references
        for r in rows:
            references+=1;relative=r['reference_sample_start']/p['sample_rate_hz']
            emit(dict(type='signature_detection',node_id=args.node_id,profile_id=args.profile,evidence_level='recording_signature_match',
                source_kind='baseband_stdin' if args.input=='-' else 'file_replay',protocol_confirmed=False,video_decoded=False,
                emitter_identity_confirmed=False,
                user_reported_attribution=p['attribution'],center_hz=args.target_center_hz,
                estimated_carrier_hz=args.target_center_hz+r['carrier_offset_hz'],reference_time_s=relative,
                hardware_time_s=None if args.hardware_start_s is None else args.hardware_start_s+relative,
                hardware_time_reference='caller-provided device clock,not automatically UTC synchronized',
                gain_db=args.gain_db,power_calibrated=False,
                input_buffer_delay_ms=(frontend.received/args.sample_rate_hz-relative)*1000,
                reference_active_bandwidth_hz=p['zc_length']*p['subcarrier_spacing_hz'],**r))
    emit(dict(type='ready',node_id=args.node_id,source_kind='baseband_stdin' if args.input=='-' else 'file_replay',
        input_sample_rate_hz=args.sample_rate_hz,canonical_sample_rate_hz=p['sample_rate_hz'],block_ms=args.block_ms,
        model_in_data_path=False,radio_control=False,profile_id=args.profile,note='Stream must be continuous; restart/reset on hardware gaps. No live USRP source is opened.'))
    try:
        while True:
            data=source.read(count*8)
            if not data:break
            if len(data)%8:raise DomainError('INVALID_IQ','cf32 stream ended inside a complex sample')
            iq=np.frombuffer(data,dtype='<c8');start=time.perf_counter();x,origin=frontend.process(iq)
            front=(time.perf_counter()-start)*1000;start=time.perf_counter();rows=detector.process(x,origin) if len(x) else []
            core=(time.perf_counter()-start)*1000;publish(rows);blocks+=1
            times.append(dict(frontend_ms=front,core_ms=core,total_ms=front+core,input_duration_ms=len(iq)/args.sample_rate_hz*1000))
            if args.max_blocks and blocks>=args.max_blocks:break
        x,origin=frontend.process(np.empty(0,dtype=np.complex64),final=True)
        if len(x):publish(detector.process(x,origin))
        publish(detector.flush())
    finally:
        if source is not sys.stdin.buffer:source.close()
    warm=times[4:] if len(times)>4 else times
    emit(dict(type='summary',node_id=args.node_id,input_blocks=blocks,matched_references=references,
        input_samples=frontend.received,canonical_samples=frontend.next_output,
        processing_ratio_all_blocks=sum(r['total_ms'] for r in times)/max(sum(r['input_duration_ms'] for r in times),1e-30),
        warmup_blocks_excluded_for_percentiles=4 if len(times)>4 else 0,
        timing_ms={k:{q:float(np.percentile([r[k] for r in warm],v)) for q,v in [('p50',50),('p95',95),('p99',99)]} for k in ('frontend_ms','core_ms','total_ms')} if warm else {},
        note='Processing timing excludes radio/network queues and output transport. File replay benchmark is not a live RF latency validation; input_buffer_delay is a sample-time estimate.'))


if __name__=='__main__':main()
