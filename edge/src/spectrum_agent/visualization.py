"""Explicit visualization tool: existing web waterfall, no detection or decoding."""
import json
from pathlib import Path
from .common import DomainError


def plot(ctx,capture_id):
    rec=ctx.resolve(capture_id)
    if rec.get('kind')!='capture':
        raise DomainError('INVALID_ARTIFACT','Plot requires a saved capture')
    from .ui_gateway import waterfall
    metadata=json.loads(Path(rec['metadata_path']).read_text())
    value=waterfall(ctx,dict(artifact_id=capture_id,metadata=metadata))
    if value is None:raise DomainError('INSUFFICIENT_IQ','Recording too short for STFT')
    aid=ctx.json_artifact('spectrogram',dict(capture_id=capture_id,visual=value,
        segmentation_performed=False,recognition_performed=False))
    return dict(spectrogram_id=aid,capture_id=capture_id,rows=value['rows'],columns=value['columns'],
        web_visual_url='/edge/tasks/'+ctx.task_id+'/visual',
        note='Full-record STFT pooled into the existing web waterfall; no detection or protocol identification.')
