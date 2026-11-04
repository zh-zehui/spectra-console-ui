from .common import ROOT, DomainError, dump, finite_number, request_constraints
import hashlib
import json


def query(ctx, center_hz, span_hz, region=None):
    finite_number(center_hz, "center_hz", 1e6, 6e9)
    finite_number(span_hz, "span_hz", 1e3, 160e6)
    constraints=request_constraints(ctx)
    site_context=constraints.get('capture_context')
    if region is None and isinstance(site_context,dict):region=site_context.get('region')
    if any(k in constraints and abs(constraints[k]-v)>1 for k,v in (("center_hz",center_hz),("span_hz",span_hz))):
        raise DomainError("TASK_CONSTRAINT","Prior lookup must use the requested analysis range")
    source = ROOT / "configs" / "band_priors.json"
    raw = source.read_bytes()
    kb = json.loads(raw)
    sources=['configs/band_priors.json']
    extension=ROOT/'configs/band_extensions.json'
    if extension.exists():
        extra=extension.read_bytes()
        additions=json.loads(extra)
        kb['entries']+=additions['entries'];kb['version']=additions['version']
        raw=raw+b'\n'+extra
        sources.append('configs/band_extensions.json')
    low, high = center_hz-span_hz/2, center_hz+span_hz/2
    candidates = []
    for item in kb["entries"]:
        if item["low_hz"] < high and item["high_hz"] > low:
            candidates.append(dict(item, partial_in_band=low>item["low_hz"] or high<item["high_hz"]))
            if item['id'] in ('wifi_24','wifi_58'):
                from .protocol_batch import wifi_centers
                candidates[-1]['technical20MHz_observation_candidates_hz']=wifi_centers(max(low,item['low_hz']),min(high,item['high_hz']))
                candidates[-1]['observation_note']='Technical channel raster for20MHz receiver placement,not evidence of presence or a regional permission. Wide40/80MHz centers must not be used as20MHz decoder centers without observed justification.'
                candidates[-1]['recommended20MHz_observation']=dict(bandwidth_hz=18e6,output_rate_hz=20e6,window_s=.01,context_s=.002)
                candidates[-1]['decoder_status']='Installed complementary20MHz data receivers: wifi_legacy_ofdm,wifi_gr20_siso,wifi_crafter_all. HT40/VHT80 SIG recognition via wifi_gr20_siso; full wide MAC unsupported.'
                if item['id']=='wifi_24':
                    candidates[-1]['complementary_receiver_note']='For2.4GHz use wifi_legacy_ofdm as the validated OFDM compatibility baseline; wifi_gr20_siso adds complementary packet/header acquisition. Add wifi_crafter_all for DSSS/CCK and HT20. wifi_crafter_dsss is a focused alternative,not a substitute for OFDM; avoid redundantly running both crafter profiles without a specific reason. Real-IQ regression showed two legacy-valid packets missed by modern+crafter receivers.'
                candidates[-1]['verification']='Choose installed profile(s) explicitly. Preserve SIG CRC and MAC FCS as separate evidence. VHT80 duplicate20MHz headers need observations across candidate slices for center corroboration.'
            if item['id']=='cellular_tdd_2300_2400':
                from .nr import n40_raster,RASTER_SOURCE
                candidates[-1]['n40_ssb_raster']=dict(entries=n40_raster(low,high),ssb_scs_khz=30,ssb_pattern='C',
                    passband_hz=8800000,output_rate_hz=15360000,source=RASTER_SOURCE,
                    note='Technical synchronization raster inside the recording, not evidence of presence or current regional allocation.')
    from .protocol_batch import obligations
    snapshot = dict(minimum_installed_candidate_coverage=obligations(ctx,low,high),version=kb["version"], sha256=hashlib.sha256(raw).hexdigest(),
                    source_files=sources,hash_definition='SHA256 of source file bytes joined in source_files order with one newline',
                    center_hz=center_hz, span_hz=span_hz, range_hz=[low, high],
                    region=region,site_context=site_context, candidates=candidates, observed=False,
                    warnings=["Frequency overlap is prior knowledge, not evidence of presence",
                              "Region unspecified; regional applicability is unverified"] if not region else
                             ["Regional applicability requires separate verified records"])
    aid = ctx.json_artifact("priors", snapshot)
    return dict(prior_snapshot_id=aid, **snapshot)
