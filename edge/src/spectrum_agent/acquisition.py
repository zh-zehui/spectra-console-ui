from __future__ import annotations
import contextlib
import json
from pathlib import Path
import shutil
import time
import numpy as np
from .common import ROOT, DomainError, dump, finite_number, request_constraints,utc


@contextlib.contextmanager
def device():
    import fcntl
    import uhd
    lock=(ROOT/"runs"/"usrp.lock").open("a")
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise DomainError("DEVICE_BUSY","Another spectrum task owns the USRP")
    try:
        for proc in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                args=proc.read_bytes().decode(errors="replace").split("\0")
            except (OSError,PermissionError):
                continue
            if any(Path(a).name in ("zmq_usrp_client.py","untitled1_stft2d.py","uhd_rx_cfile","uhd_rx_nogui") for a in args):
                raise DomainError("DEVICE_BUSY","An existing receiver may own the USRP; it was not stopped")
        radio=uhd.usrp.MultiUSRP("addr=192.168.10.2,recv_frame_size=8000,recv_buff_size=134217728")
        yield radio
    finally:
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()


def capabilities(ctx):
    from gnuradio import gr
    import uhd
    with device() as radio:
        freq=radio.get_rx_freq_range(0)
        bandwidth=radio.get_rx_bandwidth_range(0)
        gains=radio.get_rx_gain_range(0)
        info=dict(backend="UHD",device_addr="192.168.10.2",
            device_info=radio.get_usrp_rx_info(0),
            frequency_range_hz=[freq.start(),freq.stop()],
            bandwidth_range_hz=[bandwidth.start(),bandwidth.stop()],
            sample_rate_range_hz=[radio.get_rx_rates(0).start(),radio.get_rx_rates(0).stop()],
            high_rate_options_hz=[20e6,25e6,40e6,50e6,200e6/3,100e6,200e6],
            gain_range_db=[gains.start(),gains.stop()],
            antennas=list(radio.get_rx_antennas(0)),gnuradio_version=gr.version(),
            supports_receive=True,supports_transmit=False,
            measured_100mhz_coverage=False,
            protocol_plugins={"ble_1m_legacy_advertising":"experimental",
                              "wifi_gr20_siso":"experimental20MHz-legacy-HT-nonaggregate-VHT-offline-tested",
                              "wifi_crafter":"DSSS-CCK-and-HT20-AMPDU-offline-tested",
                              "ieee802154_oqpsk_250k":"experimental-offline-IQ-tested",
                              "wifi_legacy20mhz_ofdm":"experimental-tested",
                              "bluetooth_classic":"not_installed"},
            warnings=["Hardware ranges are not sustained throughput or calibrated passband measurements"])
    aid=ctx.json_artifact("capabilities",info)
    return dict(artifact_id=aid,**info)


def capture(ctx,center_hz,span_hz,duration_s=.1,gain_db=20.,sample_rate_hz=None):
    import uhd
    finite_number(center_hz,"center_hz",1e6,6e9)
    finite_number(span_hz,"span_hz",1e4,160e6)
    finite_number(duration_s,"duration_s",.001,10)
    finite_number(gain_db,"gain_db",0,60)
    constraints=request_constraints(ctx)
    for key,value in (("center_hz",center_hz),("span_hz",span_hz)):
        expected=constraints.get('acquisition_center_hz',constraints.get('center_hz')) if key=='center_hz' else constraints.get(key)
        if expected is not None and abs(expected-value)>1:
            raise DomainError("TASK_CONSTRAINT","Tool cannot change task "+key)
    if duration_s>constraints.get("observation_s",10)+1e-9:
        raise DomainError("TASK_CONSTRAINT","Tool cannot extend observation budget")
    aid,path=ctx.artifact("capture","cf32")
    max_bytes=32*1024**3
    with device() as radio:
        fr=radio.get_rx_freq_range()
        br=radio.get_rx_bandwidth_range()
        grange=radio.get_rx_gain_range()
        info=radio.get_usrp_rx_info(0)
        if not grange.start()<=gain_db<=grange.stop():
            raise DomainError("INVALID_GAIN","Gain outside daughterboard range")
        if center_hz-span_hz/2<fr.start() or center_hz+span_hz/2>fr.stop():
            raise DomainError("OUT_OF_BAND","Requested RF interval not supported")
        if br.stop()<span_hz:
            raise DomainError("INSUFFICIENT_BANDWIDTH","Requested span exceeds nominal frontend capability")
        requested_rate=sample_rate_hz or constraints.get('sample_rate_hz') or span_hz*1.4
        if constraints.get('sample_rate_hz') is not None and abs(requested_rate-constraints['sample_rate_hz'])>1:
            raise DomainError('TASK_CONSTRAINT','Sample rate differs from requested acquisition rate')
        radio.set_rx_rate(requested_rate)
        fs=radio.get_rx_rate()
        if fs+1<span_hz*1.25 and sample_rate_hz is None and 'sample_rate_hz' not in constraints:
            radio.set_rx_rate(200e6)
            fs=radio.get_rx_rate()
        if fs+1<span_hz*1.25:
            raise DomainError("INSUFFICIENT_BANDWIDTH","Actual sample rate must be at least1.25 times analysis bandwidth for full-passband filtering")
        count=int(round(fs*duration_s))
        if count*8>max_bytes or shutil.disk_usage(path.parent).free<count*8+1024**3:
            raise DomainError("RESOURCE_LIMIT","Capture exceeds 32GiB limit or free disk reserve")
        tune_result=radio.set_rx_freq(uhd.types.TuneRequest(center_hz))
        radio.set_rx_gain(gain_db)
        actual_gain=radio.get_rx_gain()
        tune_details={key:float(getattr(tune_result,key)) for key in ('target_rf_freq','actual_rf_freq','target_dsp_freq','actual_dsp_freq') if hasattr(tune_result,key)}
        fixed_frontend=abs(br.stop()-br.start())<1
        if not fixed_frontend:radio.set_rx_bandwidth(min(br.stop(),span_hz*1.2))
        actual_bw=br.stop() if fixed_frontend else radio.get_rx_bandwidth()
        if actual_bw<span_hz:
            raise DomainError("INSUFFICIENT_BANDWIDTH","Actual analog filter bandwidth too narrow")
        actual_fc=radio.get_rx_freq()
        from .frequency_scope import describe
        frequency_scope=describe(dict(center_hz=actual_fc,sample_rate_hz=fs,frontend_nominal_bandwidth_hz=actual_bw,
            requested_span_hz=span_hz,device_info=info),constraints)
        time.sleep(.1)
        streamargs=uhd.usrp.StreamArgs("fc32","sc16");streamargs.channels=[0]
        stream=radio.get_rx_stream(streamargs)
        command=uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        command.num_samps=count;command.stream_now=False
        metadata=uhd.types.RXMetadata()
        available=int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                           if line.startswith("MemAvailable:")))*1024
        if count*8>available//3:
            raise DomainError("RESOURCE_LIMIT","Capture exceeds one third of available RAM")
        # Fixed snapshot: receive into prefaulted RAM, then write after streaming stops.
        # No disk writes or buffer copies on the receive hot path.
        storage=np.empty(count,dtype=np.complex64)
        storage.fill(0)
        segments=[];gaps=[];overflow=0;received=0
        started=time.monotonic()
        host_start=utc()
        command.time_spec=uhd.types.TimeSpec(radio.get_time_now().get_real_secs()+.15)
        stream.issue_stream_cmd(command)
        try:
            while received<count:
                    ctx.check()
                    if time.monotonic()-started>duration_s+15:
                        gaps.append(dict(reason="receive_wall_timeout",file_offset=received));break
                    buffer=storage[received:min(received+1_048_576,count)].reshape(1,-1)
                    n=stream.recv(buffer,metadata,1.)
                    code=metadata.error_code
                    if code==uhd.types.RXMetadataErrorCode.overflow:
                        overflow+=1;gaps.append(dict(reason="overflow",file_offset=received));continue
                    if code!=uhd.types.RXMetadataErrorCode.none:
                        gaps.append(dict(reason=str(code),file_offset=received));break
                    if n<=0:
                        continue
                    n=min(n,count-received)
                    stamp=metadata.time_spec.get_real_secs()
                    if segments:
                        prev=segments[-1]
                        expected=prev["hardware_start_s"]+prev["count"]/fs
                        if abs(stamp-expected)<=2/fs and not (gaps and gaps[-1]["file_offset"]==received):
                            prev["count"]+=n
                        else:
                            gaps.append(dict(reason="timestamp_discontinuity",file_offset=received,
                                             missing_time_s=stamp-expected))
                            segments.append(dict(file_start=received,count=n,hardware_start_s=stamp))
                    else:
                        segments.append(dict(file_start=0,count=n,hardware_start_s=stamp))
                    received+=n
        finally:
            stream.issue_stream_cmd(uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont))
        receive_elapsed=time.monotonic()-started
        receive_finished_utc=utc()
        storage[:received].tofile(path)
        info=radio.get_usrp_rx_info(0)
    if received==0:
        raise DomainError("CAPTURE_FAILED","No IQ samples received")
    meta=dict(kind="rf_capture",simulated=False,center_hz=actual_fc,requested_center_hz=center_hz,
              requested_span_hz=span_hz,sample_rate_hz=fs,analog_bandwidth_hz=actual_bw,
              frontend_nominal_bandwidth_hz=actual_bw,frequency_scope=frequency_scope,
              analog_bandwidth_source='fixed_hardware_range' if fixed_frontend else 'driver_readback_uncalibrated',
              calibrated_effective_passband=False,sample_count=received,requested_sample_count=count,
              requested_duration_s=duration_s,duration_s=received/fs,gain_db=actual_gain,requested_gain_db=gain_db,tune_details=tune_details,
              host_stream_command_utc=host_start,host_receive_complete_utc=receive_finished_utc,
              hardware_time_reference="device clock seconds; not Unix UTC",
              dtype="complex64_le",wire_format="sc16",segments=segments,gaps=gaps,
              overflow_count=overflow,device_info=info,receive_elapsed_s=receive_elapsed,
              complete=received==count and not gaps,
              warnings=["Effective RF passband is not calibrated",
                        "Power is relative; no absolute dBm calibration",
                        "A short observation does not prove absence of intermittent signals"])
    if constraints.get('capture_context') is not None:
        meta['capture_context']=constraints['capture_context']
    dump(path.with_suffix(".json"),meta)
    ctx.register(aid,path,kind="capture",metadata_path=str(path.with_suffix(".json")))
    return dict(capture_id=aid,**meta)
