from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import threading
from typing import Annotated
from fastapi import FastAPI,Depends,Header,HTTPException,Request
from fastapi.responses import FileResponse,StreamingResponse
from pydantic import BaseModel,ConfigDict,Field
from .common import ROOT,Context,DomainError,safe_id
from .service import TaskStore,TERMINAL,credentials


class TaskInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    prompt:str=Field(min_length=1,max_length=16000)
    constraints:dict=Field(default_factory=dict)
    budgets:dict=Field(default_factory=dict)
    source:str="usrp"
    provider:str="deepseek"
    continue_from_task_id:str|None=None
    replay_from_task_id:str|None=None
    idempotency_key:str=Field(min_length=1,max_length=128)


store=None


@asynccontextmanager
async def lifespan(app):
    global store
    store=TaskStore()
    stop=threading.Event()
    worker=threading.Thread(target=store.loop,args=(stop,),daemon=True)
    worker.start()
    yield
    stop.set()
    # Cancel only our active jobs, giving dsh and its domain worker time to release the device.
    with store.connect() as db:
        running=db.execute("SELECT id FROM tasks WHERE status IN ('running','cancelling')").fetchall()
    for row in running:
        store.cancel(row["id"])
    worker.join(timeout=10)


app=FastAPI(title="Spectrum Edge Task API",docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)


def auth(authorization:Annotated[str|None,Header()]=None):
    expected="Bearer "+credentials()["api_token"]
    if not authorization or not hmac.compare_digest(authorization,expected):
        raise HTTPException(401,"Invalid task API token")


def translate(exc):
    return HTTPException({"NOT_FOUND":404,"TASK_ACTIVE":409,"IDEMPOTENCY_CONFLICT":409,"QUEUE_FULL":429}.get(exc.code,422),
                         detail=dict(error_code=exc.code,message=str(exc)))


def public(row):
    path=Context(row["id"]).run/"events.jsonl"
    stage=None;analysis_progress=None;protocol_progress=None
    if path.exists():
        with path.open("rb") as f:
            f.seek(max(0,path.stat().st_size-16384))
            lines=f.read().decode(errors="replace").splitlines()
        for line in reversed(lines):
            try:
                event=json.loads(line)
                if stage is None:stage=event.get('stage')
                if event.get('stage')=='protocol_coverage_progress' and protocol_progress is None:
                    protocol_progress={k:event[k] for k in ('plan_id','total_units','completed_units','failed_units')}
                if event.get('stage')=='catalog_review_progress' and analysis_progress is None:
                    analysis_progress={k:event[k] for k in ('reviewed_regions','total_regions','remaining_regions')}
            except json.JSONDecodeError: continue
    effective=path.parent/'request.json'
    request=json.loads(effective.read_text()) if effective.exists() else json.loads(row['request'])
    return dict(task_id=row["id"],status=row["status"],stage=stage,analysis_progress=analysis_progress,protocol_progress=protocol_progress,created_utc=row["created"],
                request=request,
                outcome=json.loads(row["outcome"]) if row["outcome"] else None)


@app.get("/healthz")
def health():
    return dict(status="ok",runtime="official DeepSeek Harness 0.2.1-rc.2",headless=True)


@app.get("/v1/capabilities",dependencies=[Depends(auth)])
def capabilities():
    return dict(runtime=json.loads((ROOT/"runtime/selected-dsh.json").read_text()),
        analysis_catalog=json.loads((ROOT/'configs/analysis_catalog.json').read_text()),
        configured_models=["deepseek"]+(["local"] if (ROOT/"configs/local-model.patch.yml").exists() else []),
        protocol_plugins={"ble_1m_legacy_advertising":"experimental-tested",
                          "wifi_gr20_siso":"experimental20MHz-legacy-HT-nonaggregate-VHT-MCS0..7-offline-tested",
                          "wifi_crafter":"DSSS-CCK-and-HT20-AMPDU-offline-tested",
                          "ieee802154_oqpsk_250k":"experimental-offline-IQ-tested",
                          "wifi_legacy20mhz_ofdm":"experimental-tested","bluetooth_classic_access_code":"candidate-analysis-tested",
                          "bluetooth_classic_full_decoder":"not_validated",
                          "lte_downlink_pbch_mib":"experimental-FDD-TDD-positive-negative-tested",
                          "nr5g_pbch":"experimental-FR1-15k-A-30k-B-C-SSB-PBCH-MIB"},
        continuation=dict(supported=True,request_field='continue_from_task_id',new_rf_capture=False),
        recording_replay=dict(supported=True,request_field='replay_from_task_id',analysis_scope_editable=True,
            inherits_analysis=False,new_rf_capture=False),
        tools=["analyze_drone_hopping","list_drone_video_profiles","analyze_drone_video_link","localize_verified_frames","review_activity_batch","query_band_priors","inspect_capabilities","capture_iq","make_synthetic_capture",
               "inspect_spectrum","coarse_segment_spectrum","segment_spectrum","compare_segmentations","select_segmentation","consolidate_signal_regions","fuse_refined_regions",
               "extract_subband","profile_signal","match_signal_hypotheses","list_signal_groups",
               "verify_protocol","analyze_bluetooth_classic","assess_signal_scene","refine_signal_region","compare_local_refinements","analyze_iq_quality","analyze_burst_timing","plan_unknown_investigations","record_investigation_decision",
               "analyze_sweep_structure","summarize_spectral_activity","export_sigmf_recording","build_report"],
        headless=True,receive_only=True,device_job_concurrency=1,
        note="Static capabilities; inspect_capabilities tool performs actual hardware inspection")


@app.post("/v1/tasks",status_code=202,dependencies=[Depends(auth)])
def create_task(body:TaskInput):
    value=body.model_dump();idem=value.pop("idempotency_key")
    try: return public(store.create(value,idem))
    except DomainError as exc: raise translate(exc)


@app.get("/v1/tasks/{task_id}",dependencies=[Depends(auth)])
def get_task(task_id:str):
    try: return public(store.get(task_id))
    except DomainError as exc: raise translate(exc)


@app.get('/v1/tasks/{task_id}/recording',dependencies=[Depends(auth)])
def recording_info(task_id:str):
    from .continuation import inspect_recording
    from .frequency_scope import effective_metadata
    try:
        source=inspect_recording(task_id);m=source['metadata']
        return dict(task_id=task_id,capture_id=source['capture_id'],sample_count=m['sample_count'],
            duration_s=m['duration_s'],complete=m.get('complete'),
            frequency_scope=effective_metadata(Context(task_id),m)['frequency_scope'])
    except DomainError as exc:raise translate(exc)


@app.delete('/v1/tasks/{task_id}',dependencies=[Depends(auth)])
def remove_task_record(task_id:str):
    from .task_records import set_removed
    try:return set_removed(task_id,True)
    except DomainError as exc:raise translate(exc)


@app.post('/v1/tasks/{task_id}/restore',dependencies=[Depends(auth)])
def restore_task_record(task_id:str):
    from .task_records import set_removed
    try:return set_removed(task_id,False)
    except DomainError as exc:raise translate(exc)


@app.post("/v1/tasks/{task_id}/cancel",dependencies=[Depends(auth)])
def cancel_task(task_id:str):
    try: return public(store.cancel(task_id))
    except DomainError as exc: raise translate(exc)


@app.get("/v1/tasks/{task_id}/result",dependencies=[Depends(auth)])
def result(task_id:str):
    try:
        row=store.get(task_id)
        if row["status"] not in TERMINAL: raise HTTPException(409,"Task not completed")
        p=Context(task_id).run/"report.json"
        return dict(**public(row),analysis=json.loads(p.read_text()) if p.exists() else None)
    except DomainError as exc: raise translate(exc)


@app.get("/v1/tasks/{task_id}/files/{kind}",dependencies=[Depends(auth)])
def task_file(task_id:str,kind:str):
    try:
        store.get(task_id)
        name={"report":"report.md","timing":"timing.md","events":"events.jsonl","outcome":"outcome.json"}.get(kind)
        if not name: raise HTTPException(404,"Unknown file kind")
        p=Context(task_id).run/name
        if not p.exists(): raise HTTPException(404,"File not ready")
        return FileResponse(p)
    except DomainError as exc: raise translate(exc)


@app.get("/v1/tasks/{task_id}/artifacts/{artifact_id}",dependencies=[Depends(auth)])
def artifact(task_id:str,artifact_id:str):
    try:
        store.get(task_id)
        rec=Context(task_id).resolve(artifact_id)
        return FileResponse(rec["path"])
    except DomainError as exc: raise translate(exc)


@app.get("/v1/tasks/{task_id}/events",dependencies=[Depends(auth)])
async def events(task_id:str,request:Request,last_event_id:Annotated[str|None,Header()]=None):
    try: store.get(task_id)
    except DomainError as exc: raise translate(exc)
    try: offset=max(0,int(last_event_id or 0))
    except ValueError: raise HTTPException(422,"Last-Event-ID must be an integer")
    async def generate():
        nonlocal offset
        file=Context(task_id).run/"events.jsonl"
        while not await request.is_disconnected():
            # Bounded tail reads, persistent line offset and backpressure isolated from capture.
            if file.exists():
                with file.open() as source:
                    for index,line in enumerate(source,1):
                        if index>offset:
                            yield f"id: {index}\nevent: progress\ndata: {line.strip()}\n\n"
                            offset=index
            if store.get(task_id)["status"] in TERMINAL:
                yield "event: terminal\ndata: "+json.dumps(public(store.get(task_id)))+"\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(generate(),media_type="text/event-stream")
