//! Bounded saved-IQ receiver only. No device backend or transmit feature enabled.
use crafter::radio::*;
use serde_json::{json, Value};
use std::{collections::BTreeMap, fs::File, io::BufReader, time::{Duration, Instant}};

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn diagnostic(d: &PhyDiagnostic) -> (&'static str, Value) {
    match d {
        PhyDiagnostic::HtSignal { fields:f, preamble_sample_index:p } => ("ht_sig_pass", json!({
            "preamble_sample_index":p,"mcs":f.mcs,"channel_width_40_mhz":f.channel_width_40_mhz,
            "psdu_bytes":f.psdu_bytes,"aggregation":f.aggregation,"stbc":f.stbc,
            "ldpc":f.ldpc,"short_guard_interval":f.short_guard_interval,
            "extension_spatial_streams":f.extension_spatial_streams})),
        PhyDiagnostic::HtGreenfield { preamble_sample_index:p } => ("ht_greenfield",json!({"preamble_sample_index":p})),
        PhyDiagnostic::Ampdu { delimiter_offset:o, control_bits:c } => ("ampdu_member",json!({"delimiter_offset":o,"control_bits":c})),
        PhyDiagnostic::AmpduErrors { preamble_sample_index:p,invalid_delimiters:d,invalid_fcs:f,truncated_mpdus:t,oversized_mpdus:o } =>
            ("ampdu_errors",json!({"preamble_sample_index":p,"invalid_delimiters":d,"invalid_fcs":f,"truncated_mpdus":t,"oversized_mpdus":o})),
        PhyDiagnostic::Dsss { short_preamble:s,frequency_offset_hz:c,timing_uncertainty_samples:u } =>
            ("dsss",json!({"short_preamble":s,"frequency_offset_hz":c,"timing_uncertainty_samples":u})),
        PhyDiagnostic::Ofdm { frequency_offset_hz:c,training_correlation:t } => ("ofdm",json!({"frequency_offset_hz":c,"training_correlation":t})),
        PhyDiagnostic::InvalidHeader => ("invalid_header",Value::Null),
        PhyDiagnostic::InvalidFcs => ("invalid_fcs",Value::Null),
        PhyDiagnostic::InvalidData => ("invalid_data",Value::Null),
        PhyDiagnostic::TruncatedFrame => ("truncated_frame",Value::Null),
        PhyDiagnostic::UnsupportedPhy => ("unsupported_phy",Value::Null),
        PhyDiagnostic::LdpcNonconvergence { codeword:c,iterations:i,failed_checks:f } =>
            ("ldpc_nonconvergence",json!({"codeword":c,"iterations":i,"failed_checks":f})),
        PhyDiagnostic::Reset(_) => ("reset",Value::Null),
        _ => ("other_diagnostic",Value::Null),
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    if args.len()!=4 { return Err("usage: spectra-crafter-rx FILE.cs8 CENTER_HZ all|dsss".into()); }
    let file=File::open(&args[1])?;let bytes=file.metadata()?.len();
    if bytes==0 || bytes%2!=0 || bytes>8_000_000 { return Err("CS8 requires1..4M complete complex samples".into()); }
    let count=bytes/2;
    let config=RxConfig { sample_rate_hz:20_000_000,center_frequency_hz:args[2].parse()?,
        max_chunk_samples:16384,max_buffer_samples:4_000_000,max_frame_bytes:8192,
        max_pending_frames:8192,max_capture_samples:count+1,max_duration:Duration::from_secs(1) };
    let position=IqPosition { epoch:0,sequence:0,sample_index:0,time_anchor:None,discontinuity:None };
    let mut source=ReaderIqSource::new(BufReader::new(file),config,position)?;
    let mut decoder: Box<dyn PhyDecoder> = match args[3].as_str() {
        "all"=>Box::new(WifiDecoder::new()), "dsss"=>Box::new(DsssCckDecoder::new()),
        _=>return Err("unknown mode".into()) };
    let mut frames=Vec::new();let mut counts=BTreeMap::<String,u64>::new();
    let mut headers=Vec::new();let start=Instant::now();let mut processed=0;
    loop {
        let event=source.next_event()?;let terminal=matches!(event,IqEvent::End(_));
        if let IqEvent::Chunk(ref c)=event {processed+=c.cs8().len()/2;}
        let out=decoder.consume(event)?;
        for d in out.diagnostics {
            let (name,value)=diagnostic(&d);*counts.entry(name.into()).or_default()+=1;
            if matches!(d,PhyDiagnostic::HtSignal{..}|PhyDiagnostic::AmpduErrors{..}) {
                if headers.len()>=8192 {return Err("diagnostic capacity exceeded".into());}
                headers.push(json!({"kind":name,"value":value}));
            }
        }
        for f in out.frames {
            if f.integrity!=FrameIntegrity::ValidFcs || f.framing.trailer_bytes!=4 {return Err("decoder emitted unverified Wi-Fi frame".into());}
            if frames.len()>=4096 {return Err("frame capacity exceeded".into());}
            let mut detail=Vec::new();let mut phy="legacy_ofdm";let mut uncertainty=16;
            for d in &f.diagnostics {
                let (name,value)=diagnostic(d);
                if let PhyDiagnostic::Dsss{timing_uncertainty_samples:u,..}=d {
                    phy=if f.rate_bps>2_000_000 {"CCK"} else {"DSSS"};uncertainty=*u;
                }
                if matches!(d,PhyDiagnostic::HtSignal{..}) {phy="HT20";}
                detail.push(json!({"kind":name,"value":value}));
            }
            frames.push(json!({"psdu_hex":hex(&f.bytes),"rate_bps":f.rate_bps,"phy_format":phy,
                "sample_start":f.start.sample_index,"sample_stop":f.end_sample_index,
                "timing_uncertainty_samples":uncertainty,"diagnostics":detail}));
        }
        if terminal {break;}
    }
    println!("{}",json!({"schema":"spectra.crafter.rx/v1","mode":args[3],"sample_count":count,
        "processed_samples":processed,"frames":frames,"diagnostic_counts":counts,
        "header_evidence":headers,"elapsed_s":start.elapsed().as_secs_f64()}));
    Ok(())
}

fn main() {
    if let Err(error)=run() {eprintln!("{error}");std::process::exit(2);}
}
