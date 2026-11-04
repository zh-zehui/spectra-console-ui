"""Atomic compatible update of new-task interpretation and static UI,no restart."""
import os,py_compile
from pathlib import Path
root=Path('/home/sribd/spectrum_agent');stage=root/'runtime/history-ui-release'
for name in ('continuation.py','task_intent.py','service.py'):py_compile.compile(str(stage/name),doraise=True)
for name in ('continuation.py','task_intent.py','service.py'):os.replace(stage/name,root/'src/spectrum_agent'/name)
os.replace(stage/'index.mjs',root/'plugins/spectrum-tools/index.mjs')
os.replace(stage/'styles.css',root/'ui/dist/styles.css')
os.replace(stage/'app.js',root/'ui/dist/app.js')
print('History actions,automatic prompt sizing,and model-directed continuation deployed atomically; no services restarted.')
