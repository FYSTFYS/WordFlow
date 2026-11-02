#!/usr/bin/env python3
"""Create a portable source distribution without local credentials or outputs."""
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parent
out = root / 'dist' / 'word-workbench.zip'
out.parent.mkdir(exist_ok=True)
paths = [root / n for n in ('README.md', 'requirements.txt', 'workbench_server.py', 'frequency_worker.py', 'bootstrap.py', 'start_workbench.sh', 'start_workbench.bat', 'build_package.py', 'main.py', 'epub_to_txt.py', 'txt_to_excel.py', 'ankicheck.py', 'list_lemmas.py', 'merge_epubs.py', 'ankimaker/ankimaker.py', 'ankimaker/run_utils.py', 'ankimaker/README.md')]
for folder in ('web', 'vocabulary_exclude', 'wordlist'):
    paths.extend(p for p in (root / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
config = json.loads((root / 'ankimaker/anki_config.json').read_text(encoding='utf-8'))
for section in ('llm','image'):
    if isinstance(config.get(section),dict): config[section]['api_key'] = ''
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for p in paths: z.write(p, str(p.relative_to(root)))
    z.writestr('ankimaker/anki_config.json', json.dumps(config, ensure_ascii=False, indent=2))
    z.writestr('xlsxdir/', '')
print(out)
