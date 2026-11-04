"""Audit-only repair of omitted inherited coverage; never run DSP or a model.

Preserve the original outcome/report,verify completed decoder artifacts and the
full hopping report,then reproject authoritative status with a public receipt.
"""
import hashlib,json,shutil,time
from spectrum_agent.common import ROOT,Context,dump,utc
from spectrum_agent.continuation import inherit_protocol_coverage
from spectrum_agent.worker import report
from spectrum_agent.drone_hopping import coverage_complete

ctx=Context('task_agile_full_292630d126a1');source='task_49fb52faa41f40f8a492211d85969bad'
def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
original_source=ROOT/'runs'/source/'report.json';source_hash=hashlib.sha256(original_source.read_bytes()).hexdigest()
before=json.loads((ctx.run/'report.json').read_text());outcome=json.loads((ctx.run/'outcome.json').read_text())
assert outcome['exit_code']==0 and outcome.get('error_code') is None
assert coverage_complete(before,before['task_intent']) and all(c['metadata']['complete'] for c in before['captures'])
for filename in ('report.json','report.md','outcome.json','analysis-stop.json'):
    archive=ctx.run/(filename+'.before-coverage-reconciliation')
    if not archive.exists():shutil.copy2(ctx.run/filename,archive)
began=time.perf_counter();ctx.event('inherited_protocol_coverage_reconciliation',phase='start',source_task_id=source,new_dsp=False)
receipts=inherit_protocol_coverage(ctx,source)
assert receipts and all(v['completed_units']==v['total_units'] and not v['failed_units'] for v in receipts)
report(ctx,stop_reason='requested_checks_complete',stop_detail='Supervisor metadata reconciliation: original1s hopping100/100 and unchanged inherited Wi-Fi decoder artifacts plus verified70/70 source protocol units are complete. No new decoding or acquisition; unknown structures remain unresolved.')
stop=json.loads((ctx.run/'analysis-stop.json').read_text());stop['owner']='supervisor_coverage_reconciliation';dump(ctx.run/'analysis-stop.json',stop)
after=json.loads((ctx.run/'report.json').read_text())
for key in ('frame_associations','wifi_header_evidence','drone_hopping_evidence'):
    assert fingerprint(before[key])==fingerprint(after[key]),key
assert after['protocol_coverage']['complete'] and coverage_complete(after,after['task_intent'])
assert source_hash==hashlib.sha256(original_source.read_bytes()).hexdigest()
receipt=dict(task_id=ctx.task_id,source_task_id=source,previous_harness_status=outcome['status'],effective_status='succeeded',
    reason='Completed source protocol plan/checkpoints were omitted while decoder frames were correctly inherited; restored and verified original plan plus checkpoint proof.',
    source_report_unchanged=True,wifi_and_hopping_evidence_unchanged=True,new_dsp=False,new_rf_capture=False,
    inherited_protocol_coverage=receipts,elapsed_ms=(time.perf_counter()-began)*1000,utc=utc())
dump(ctx.run/'coverage-reconciliation.json',receipt)
ctx.event('inherited_protocol_coverage_reconciliation',phase='end',status='succeeded',elapsed_ms=receipt['elapsed_ms'],new_dsp=False)
report(ctx)  # Include the explicit supervisor owner and reconciliation timing.
outcome['status']='succeeded';outcome['supervisor_reconciliation']=receipt;outcome['reconciled_utc']=utc()
outcome['authoritative_summary']['protocol_coverage']=after['protocol_coverage']
outcome['authoritative_summary']['drone_hopping_coverage']=[v['coverage'] for v in after['drone_hopping_evidence']]
dump(ctx.run/'outcome.json',outcome)
accept_path=ROOT/'docs/testing/agile-real-full-v2-acceptance.json';old_accept=json.loads(accept_path.read_text())
archive=accept_path.with_name('agile-real-full-v2-before-coverage-reconciliation.json')
if not archive.exists():dump(archive,old_accept)
old_accept.update(status='succeeded',initial_harness_status='partial',coverage_reconciliation=receipt)
dump(accept_path,old_accept);dump(ROOT/'docs/testing/agile-inherited-coverage-reconciliation.json',receipt)
print(json.dumps(receipt,ensure_ascii=False))
