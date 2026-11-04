from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from spectrum_agent.common import ROOT
task='wifi_modern_accept_0a4539c7a4'
target=ROOT/'docs/testing/wifi-modern-direct-tool-trace.zip'
with ZipFile(target,'w',ZIP_DEFLATED) as archive:
    for name in ('request.json','outcome.json','report.json','events.jsonl','timing.json','timing.md','continuation.json'):
        p=ROOT/'runs'/task/name
        if p.exists():archive.write(p,name)
    for name in ('wifi-modern-acceptance.txt','wifi-modern-loopback.json',
                 'wifi-modern-saved-iq.json','wifi-modern-report-acceptance.json'):
        archive.write(ROOT/'docs/testing'/name,name)
print(target)
