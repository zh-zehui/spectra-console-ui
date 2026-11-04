"""Local client. No key is printed or put in shell arguments."""
import argparse
import json
from pathlib import Path
import time
import uuid
import httpx

p=argparse.ArgumentParser()
p.add_argument("prompt")
p.add_argument("--center",type=float,default=2.4e9)
p.add_argument("--span",type=float,default=100e6)
p.add_argument("--duration",type=float,default=.1)
p.add_argument("--synthetic",action="store_true")
p.add_argument("--wait",action="store_true")
a=p.parse_args()
credentials=json.loads((Path.home()/".config/spectrum-agent/credentials.json").read_text())
headers={"Authorization":"Bearer "+credentials["api_token"]}
request=dict(prompt=a.prompt,constraints=dict(center_hz=a.center,span_hz=a.span,observation_s=a.duration),
             source="synthetic" if a.synthetic else "usrp",idempotency_key=uuid.uuid4().hex)
with httpx.Client(base_url="http://127.0.0.1:8765",headers=headers,timeout=10) as client:
    response=client.post("/v1/tasks",json=request);response.raise_for_status()
    task=response.json()["task_id"];print("task_id:",task,flush=True)
    if a.wait:
        while True:
            response=client.get("/v1/tasks/"+task);response.raise_for_status()
            value=response.json()
            if value["status"] in ("succeeded","partial","failed","cancelled"):
                print(json.dumps(client.get("/v1/tasks/"+task+"/result").json(),ensure_ascii=False,indent=2))
                break
            time.sleep(1)
