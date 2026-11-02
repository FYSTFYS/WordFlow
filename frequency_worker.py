#!/usr/bin/env python3
"""Run EPUB frequency analysis under the selected interpreter."""
import json
from pathlib import Path
import sys
import workbench_server as server

job_id = 'worker'
server.JOBS[job_id] = {'status': 'running', 'log': '', 'result': None}
server.run_frequency(job_id, json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
Path(sys.argv[2]).write_text(json.dumps(server.JOBS[job_id], ensure_ascii=False), encoding='utf-8')
