"""Evaluate a saved report against the local review dataset; no device/model access."""
import argparse
import json
from pathlib import Path
from spectrum_agent.common import dump
from spectrum_agent.segmentation_eval import evaluate_report

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--report',required=True)
parser.add_argument('--labels',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
result=evaluate_report(json.loads(Path(args.report).read_text(encoding='utf-8')),
                       json.loads(Path(args.labels).read_text(encoding='utf-8')))
dump(args.output,result)
print(json.dumps(result,ensure_ascii=False,indent=2))
