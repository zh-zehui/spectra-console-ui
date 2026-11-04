#!/usr/bin/env bash
set -euo pipefail
cd /home/sribd/spectrum_agent
.venv/bin/python - <<'PY'
import subprocess,time,urllib.request
from pathlib import Path
for name,url,script in [('api','http://127.0.0.1:8765/healthz','scripts/start-api.sh'),('ui','http://127.0.0.1:8780/edge/health','scripts/start-ui.sh')]:
    try:
        with urllib.request.urlopen(url,timeout=2) as response:
            ready=response.status==200
    except Exception: ready=False
    if not ready:
        child=subprocess.Popen(['bash',script],stdout=open('runs/'+name+'.stdout','ab'),stderr=open('runs/'+name+'.stderr','ab'),start_new_session=True)
        Path('runs/'+name+'.pid').write_text(str(child.pid))
        for _ in range(30):
            time.sleep(.5)
            try:
                with urllib.request.urlopen(url,timeout=2) as response:
                    if response.status==200: break
            except Exception: pass
        else: raise RuntimeError(name+' service did not become ready')
    print(name+' ready')
PY
