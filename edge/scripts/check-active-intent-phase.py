"""Read-only deployment check: report whether active tasks have resolved intent."""
import json,sqlite3
from pathlib import Path
root=Path('/home/sribd/spectrum_agent');db=sqlite3.connect('file:'+str(root/'runs/tasks.sqlite')+'?mode=ro',uri=True)
active=[]
for task,status in db.execute("SELECT id,status FROM tasks WHERE status IN ('queued','running','cancelling')"):
    p=root/'runs'/task/'request.json';value=json.loads(p.read_text()) if p.exists() else {}
    active.append(dict(task_id=task,status=status,intent=value.get('task_intent',{}).get('status')))
print(json.dumps(active));db.close()
raise SystemExit(0 if all(t['intent']=='resolved' for t in active) else 1)
