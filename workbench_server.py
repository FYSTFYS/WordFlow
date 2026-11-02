#!/usr/bin/env python3
"""Loopback-only service for the static vocabulary workbench."""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import stat
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
import webbrowser
import zipfile
import xml.etree.ElementTree as ET
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from bootstrap import python_path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web'
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
ENV_SETUP_LOCK = threading.Lock()
PROCESSES: dict[str, subprocess.Popen] = {}
MAX_BODY = 150 * 1024 * 1024
PROJECT_DIRS = {'vocabulary_exclude', 'wordlist', 'xlsxdir'}
PROJECT_WRITABLE = {'vocabulary_exclude', 'xlsxdir'}
PROJECT_LOCK = threading.RLock()


def update(job_id, **values):
    with LOCK:
        job = JOBS[job_id]
        if job.get('cancel_requested'):
            if values.get('status') in ('done', 'failed', 'cancelled'):
                values.update(status='cancelled', log='任务已中止；已写入 Anki 的内容和已生成的文件会保留。', result=None)
            elif job.get('status') == 'stopping':
                values.pop('status', None)
        job.update(values)


class CancelledJob(Exception):
    pass


def start_managed(job_id, command, **kwargs):
    with LOCK:
        job = JOBS[job_id]
        if job.get('cancel_requested'):
            raise CancelledJob('任务已中止')
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs['start_new_session'] = True
        proc = subprocess.Popen(command, **kwargs)
        PROCESSES[job_id] = proc
        return proc


def forget_process(job_id, proc):
    with LOCK:
        if PROCESSES.get(job_id) is proc:
            PROCESSES.pop(job_id, None)


def cancel_job(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise ValueError('任务不存在，服务可能已重启')
        if job.get('kind') not in ('frequency', 'anki'):
            raise ValueError('此任务不支持中止')
        if job.get('status') not in ('running', 'stopping'):
            raise ValueError('任务已经结束')
        job.update(cancel_requested=True, status='stopping', log='正在中止任务…')
        proc = PROCESSES.get(job_id)
        if proc and proc.poll() is None:
            try:
                if os.name == 'nt':
                    result = subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if result.returncode and proc.poll() is None:
                        raise OSError('无法终止完整进程树')
                else:
                    os.killpg(proc.pid, signal.SIGTERM)
                    def force_stop():
                        with LOCK:
                            if PROCESSES.get(job_id) is proc and proc.poll() is None:
                                try: os.killpg(proc.pid, signal.SIGKILL)
                                except ProcessLookupError: pass
                    timer = threading.Timer(3, force_stop)
                    timer.daemon = True
                    timer.start()
            except ProcessLookupError:
                pass
            except OSError as exc:
                job.update(cancel_requested=False, status='running', log='中止失败：'+str(exc))
                raise ValueError('中止失败：'+str(exc)) from exc




def project_path(raw, *, writable=False):
    """Resolve only project data paths, rejecting traversal and escaping symlinks."""
    if not isinstance(raw, str) or not raw or '\\' in raw or '\0' in raw:
        raise ValueError('项目路径无效')
    parts = raw.split('/')
    if any(not part or part in ('.', '..') or ':' in part for part in parts):
        raise ValueError('项目路径无效')
    if parts[0] not in (PROJECT_WRITABLE if writable else PROJECT_DIRS):
        raise PermissionError('该项目路径不允许访问')
    root = ROOT.resolve()
    base = (root / parts[0]).resolve()
    target = root.joinpath(*parts).resolve()
    if not base.is_relative_to(root) or not target.is_relative_to(base):
        raise PermissionError('项目路径不能指向项目目录之外')
    return target


def project_init():
    with PROJECT_LOCK:
        for name in sorted(PROJECT_DIRS):
            path = project_path(name)
            path.mkdir(exist_ok=True)
            if not path.is_dir(): raise ValueError(f'{name} 不是目录')
    return {'name':ROOT.name, 'path':str(ROOT.resolve())}


def project_entry(raw):
    path = project_path(raw)
    if path.is_dir(): return {'kind':'directory', 'name':path.name}
    if path.is_file(): return {'kind':'file', 'name':path.name}
    raise FileNotFoundError('文件或目录不存在')


def project_list(raw):
    path = project_path(raw)
    if not path.is_dir(): raise FileNotFoundError('目录不存在')
    entries=[]
    for child in sorted(path.iterdir(),key=lambda p:p.name.casefold()):
        resolved = project_path(raw+'/'+child.name)
        if resolved.is_dir(): kind='directory'
        elif resolved.is_file(): kind='file'
        else: continue
        entries.append({'name':child.name,'kind':kind})
    return {'entries':entries}


def project_read(raw):
    path = project_path(raw)
    if not path.is_file(): raise FileNotFoundError('文件不存在')
    if path.stat().st_size > 100 * 1024 * 1024: raise ValueError('文件超过 100 MB')
    with PROJECT_LOCK: data = path.read_bytes()
    return {'data':base64.b64encode(data).decode('ascii'), 'sha256':hashlib.sha256(data).hexdigest()}


def project_create(raw):
    path = project_path(raw, writable=True)
    if not path.parent.is_dir(): raise FileNotFoundError('父目录不存在')
    with PROJECT_LOCK:
        try:
            with path.open('xb'): pass
        except FileExistsError:
            if not path.is_file(): raise ValueError('目标不是普通文件')
    return {'kind':'file'}


def project_write(raw, content, expected):
    path = project_path(raw, writable=True)
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('缺少文件版本，请重新读取后保存')
    if not isinstance(content, str): raise ValueError('文件内容无效')
    try: data = base64.b64decode(content, validate=True)
    except Exception as exc: raise ValueError('文件内容编码无效') from exc
    if len(data) > 100 * 1024 * 1024: raise ValueError('文件超过 100 MB')
    with PROJECT_LOCK:
        if not path.is_file(): raise FileNotFoundError('文件不存在')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise FileExistsError('文件已在外部修改，请重新打开项目目录')
        fd, temp_name = tempfile.mkstemp(prefix='.workbench-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as output: output.write(data)
            os.chmod(temp_name, stat.S_IMODE(path.stat().st_mode))
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise FileExistsError('文件已在外部修改，请重新打开项目目录')
            os.replace(temp_name, path)
        finally:
            try: os.unlink(temp_name)
            except FileNotFoundError: pass
    return {'sha256':hashlib.sha256(data).hexdigest()}

def configured_environment():
    settings = ROOT / 'workbench_env.json'
    if settings.exists():
        data = json.loads(settings.read_text(encoding='utf-8'))
        value = data.get('path')
        if not isinstance(value, str) or not value.strip():
            raise ValueError('虚拟环境配置文件无效，请重新设置')
        path = Path(value).expanduser()
    else:
        path = ROOT / '.venv'
    if not path.is_absolute(): path = ROOT / path
    return path.resolve()


def environment_info():
    path = configured_environment()
    python = python_path(path)
    return {'path': str(path), 'python': str(python), 'exists': python.is_file() and ((path / 'pyvenv.cfg').is_file() or (path / 'conda-meta').is_dir()), 'project_default': str(ROOT / '.venv')}



def choose_environment_directory(mode):
    """Choose a directory on the machine running this loopback service."""
    if mode not in ('existing', 'parent'):
        raise ValueError('目录选择方式无效')
    if os.name == 'nt':
        command = ("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
                   "Add-Type -AssemblyName System.Windows.Forms; "
                   "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                   "$d.Description = 'Select a folder for the Python environment'; "
                   "$d.SelectedPath = (Get-Location).Path; "
                   "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
                   "[Console]::Out.Write($d.SelectedPath) }; $d.Dispose()")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-Command', command],
                                cwd=ROOT, capture_output=True, encoding='utf-8', errors='replace')
        if result.returncode:
            raise RuntimeError('无法打开 Windows 目录选择窗口，请手动输入路径')
        selected = result.stdout.strip()
    else:
        try:
            import tkinter as tk
            from tkinter import filedialog
            window = tk.Tk()
            try:
                window.withdraw()
                window.attributes('-topmost', True)
                selected = filedialog.askdirectory(parent=window, initialdir=str(ROOT), title='选择虚拟环境目录', mustexist=True)
            finally:
                window.destroy()
        except Exception as exc:
            raise RuntimeError('无法打开目录选择窗口，请手动输入路径') from exc
    if not selected:
        return None
    path = Path(selected).resolve()
    if not path.is_dir():
        raise ValueError('所选目录不存在')
    if mode == 'parent':
        path = path / '.venv'
    return str(path)

def save_environment(value):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError('请输入虚拟环境目录')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute(): path = ROOT / path
    path = path.resolve()
    # Persist only a path; no package installation happens when saving.
    temp = ROOT / ('workbench_env.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps({'path':str(path)}, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, ROOT / 'workbench_env.json')
    finally:
        temp.unlink(missing_ok=True)
    return environment_info()


def run_environment_setup(job_id, env_path):
    if not ENV_SETUP_LOCK.acquire(blocking=False):
        update(job_id, status='failed', log='已有环境初始化任务正在运行，请稍后再试')
        return
    try:
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        cmd = [sys.executable, '-u', str(ROOT / 'bootstrap.py'), '--prepare-only', '--venv', str(env_path)]
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
        output = ''
        for line in proc.stdout:
            output = (output + line)[-60000:]
            update(job_id, log=output)
        rc = proc.wait()
        update(job_id, status='done' if rc == 0 else 'failed', log=output or f'进程退出：{rc}', result={'path':str(env_path), 'exit_code':rc})
    except Exception as exc:
        update(job_id, status='failed', log=f'{type(exc).__name__}: {exc}')
    finally:
        ENV_SETUP_LOCK.release()


def run_frequency_in_environment(job_id, data):
    try:
        with tempfile.TemporaryDirectory(prefix='word-frequency-job-') as tmp:
            inp, out = Path(tmp) / 'input.json', Path(tmp) / 'output.json'
            inp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            proc = start_managed(job_id, [data['_python'], str(ROOT / 'frequency_worker.py'), str(inp), str(out)],
                                 cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
            try:
                stdout, stderr = proc.communicate()
            finally:
                forget_process(job_id, proc)
            if proc.returncode or not out.is_file():
                raise RuntimeError((stderr or stdout).strip() or f'进程退出：{proc.returncode}')
            result = json.loads(out.read_text(encoding='utf-8'))
            path_warnings = [line for line in stderr.splitlines() if line.startswith('Warning: EPUB path ')]
            if path_warnings and result.get('result'):
                existing = result['result'].get('warning', '')
                result['result']['warning'] = ' '.join([existing, *path_warnings]).strip()
            update(job_id, **result)
    except Exception as exc:
        update(job_id, status='failed', log=f'{type(exc).__name__}: {exc}')

def run_frequency(job_id, data):
    try:
        files = data.get('files') or []
        if not files or len(files) > 100:
            raise ValueError('请选择 1 至 100 个 EPUB 文件')
        start, end = float(data.get('start', 0)), float(data.get('end', 100))
        if not 0 <= start < end <= 100:
            raise ValueError('阅读范围必须满足 0 ≤ 开始 < 结束 ≤ 100')
        workers = int(data.get('workers') or 1)
        if not 1 <= workers <= 32:
            raise ValueError('线程数须在 1 到 32 之间')
        with tempfile.TemporaryDirectory(prefix='words-epub-') as tmp:
            tmpdir = Path(tmp)
            vocab_dir, list_dir = tmpdir / 'vocabulary_exclude', tmpdir / 'wordlist'
            for directory, records in ((vocab_dir, data.get('excludes', [])), (list_dir, data.get('wordlists', []))):
                directory.mkdir()
                for record in records:
                    name = str(record['name'])
                    relative = Path(name)
                    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
                        raise ValueError('词表文件路径无效')
                    dest = directory / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(str(record['text']), encoding='utf-8')
            epub_paths = []
            for i, item in enumerate(files):
                name = Path(item['name']).name
                if not name.lower().endswith('.epub'):
                    raise ValueError('只支持 EPUB 文件')
                p = tmpdir / f'{i}.epub'
                p.write_bytes(base64.b64decode(item['data'], validate=True))
                epub_paths.append(p)
            # Keep Python's existing lemmatization and exclusion/tagging semantics.
            from epub_to_txt import convert_epub_to_text
            from txt_to_excel import convert_txt_to_excel
            texts = []
            for i, epub in enumerate(epub_paths):
                update(job_id, log=f'正在提取 EPUB {i+1}/{len(epub_paths)}：{files[i]["name"]}')
                txt = tmpdir / f'{i}.txt'
                convert_epub_to_text(epub, txt, start, end, workers)
                texts.append(txt)
            output = tmpdir / 'result.xlsx'
            update(job_id, log='正在进行词形还原、词频统计和标记；首次加载模型可能需要一些时间。')
            anki_url = str(data.get('anki_url') or 'http://127.0.0.1:8765')
            check_anki = bool(data.get('anki_check', True))
            warning = ''
            if check_anki:
                from ankicheck import AnkiChecker
                if not AnkiChecker(url=anki_url).available():
                    check_anki = False
                    warning = 'AnkiConnect 不可用，已跳过已有单词检查。'
            count = convert_txt_to_excel(texts, output, workers, vocabulary_dir=vocab_dir, wordlist_dir=list_dir, anki_check=check_anki, anki_url=anki_url, anki_fail_open=False)
            encoded = base64.b64encode(output.read_bytes()).decode('ascii')
            stem = Path(files[0]['name']).stem
            name = stem + ('_combined' if len(files) > 1 else '') + '.xlsx'
            update(job_id, status='done', log=f'统计完成：{count} 个词。{warning}', result={'name': name, 'data': encoded, 'count': count, 'warning': warning})
    except Exception as exc:
        update(job_id, status='failed', log=f'{type(exc).__name__}: {exc}')



def run_lemmas(job_id, data):
    """Run the original CLI on uploaded files and return its report and lists."""
    try:
        files = data.get('files') or []
        if not files or len(files) > 100:
            raise ValueError('请选择 1 至 100 个 EPUB 或 TXT 文件')
        start, end = float(data.get('start', 0)), float(data.get('end', 100))
        if not 0 <= start < end <= 100:
            raise ValueError('阅读范围必须满足 0 ≤ 开始 < 结束 ≤ 100')
        workers = int(data.get('workers') or 1)
        if not 1 <= workers <= 32:
            raise ValueError('线程数须在 1 到 32 之间')
        from list_lemmas import parse_freq_thresholds
        thresholds = parse_freq_thresholds(str(data.get('freq') or '10,5'))
        sort = data.get('sort', 'alpha')
        if sort not in ('alpha', 'count'):
            raise ValueError('排序方式无效')
        with tempfile.TemporaryDirectory(prefix='words-lemmas-') as tmp:
            temp = Path(tmp)
            vocab = temp / 'vocabulary_exclude'
            vocab.mkdir()
            for record in data.get('excludes', []):
                relative = Path(str(record['name']))
                if relative.is_absolute() or '..' in relative.parts or not relative.parts:
                    raise ValueError('排除文件路径无效')
                dest = vocab / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(str(record['text']), encoding='utf-8')
            paths = []
            for i, item in enumerate(files):
                name = str(item['name'])
                if Path(name).name != name or '\\' in name or Path(name).suffix.lower() not in ('.epub', '.txt'):
                    raise ValueError('文件名无效或格式不是 EPUB/TXT')
                folder = temp / str(i)
                folder.mkdir()
                path = folder / name
                path.write_bytes(base64.b64decode(item['data'], validate=True))
                paths.append(str(path))
            out = temp / 'output'
            cmd = [data.get('_python', sys.executable), '-u', str(ROOT / 'list_lemmas.py'), *paths,
                   '--out-dir', str(out), '--vocabulary-dir', str(vocab),
                   '--freq', ','.join(map(str, thresholds)), '--sort', sort,
                   '--start', str(start), '--end', str(end), '--workers', str(workers),
                   '--model', str(data.get('model') or 'en_core_web_sm'),
                   '--anki-url', str(data.get('anki_url') or 'http://127.0.0.1:8765')]
            if not data.get('use_exclude', True): cmd.append('--no-exclude')
            if not data.get('anki_check', True): cmd.append('--no-anki-words')
            update(job_id, log='正在统计每个文件的词元和频率区间…')
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            result = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, encoding='utf-8', errors='replace')
            if result.returncode:
                error = (result.stderr or result.stdout).strip()
                lines = error.splitlines()
                if lines and (lines[-1].startswith('ValueError: EPUB spine references missing document:') or
                              lines[-1].startswith('ValueError: EPUB spine references ambiguous document:')):
                    progress = next((line for line in reversed(lines) if line.startswith('Processing ')), '')
                    error = (progress + ' — ' if progress else '') + lines[-1].removeprefix('ValueError: ')
                raise RuntimeError(error or f'进程退出：{result.returncode}')
            # The CLI reports the temporary output path; the browser chooses the real destination.
            report = result.stdout.replace(f'\nLemma lists written under: {out}\n', '')
            lists = [{'name': path.name, 'text': path.read_text(encoding='utf-8')} for path in sorted(out.glob('*.txt'))]
            update(job_id, status='done', log='统计完成', result={'report': report, 'lists': lists, 'messages': result.stderr})
    except Exception as exc:
        update(job_id, status='failed', log=f'{type(exc).__name__}: {exc}')


def anki_config_defaults():
    """Return editable, effective defaults without returning API keys."""
    config = json.loads((ROOT / 'ankimaker' / 'anki_config.json').read_text(encoding='utf-8'))
    if not isinstance(config, dict): raise ValueError('Anki 配置必须是 JSON 对象')
    llm, image = config.get('llm') or {}, config.get('image') or {}
    if not isinstance(llm, dict) or not isinstance(image, dict): raise ValueError('大模型和图片配置必须是 JSON 对象')
    provider = str(llm.get('provider') or os.getenv('LLM_PROVIDER', 'doubao')).lower()
    model_env, fallback_model, url_env, fallback_url = (('OPENAI_MODEL', 'gpt-4.1-mini', 'OPENAI_API_URL', 'https://api.openai.com/v1/responses') if provider == 'openai' else ('DOUBAO_MODEL', 'doubao-seed-2-0-lite-260428', 'DOUBAO_API_URL', 'https://ark.cn-beijing.volces.com/api/v3/responses'))
    duplicate = config.get('duplicate_check', {})
    if duplicate is not False and not isinstance(duplicate, dict): raise ValueError('重复检查配置无效')
    field_map = config.get('field_map') or {}
    if not isinstance(field_map, dict): raise ValueError('字段映射必须是 JSON 对象')
    def configured(value):
        return isinstance(value,str) and bool(value.strip()) and not value.strip().startswith('YOUR_')
    def number(value, default):
        try: return int(value)
        except (ValueError,TypeError): return default
    llm_env = ('LLM_API_KEY', 'OPENAI_API_KEY' if provider == 'openai' else 'ARK_API_KEY')
    return {
        'deck': str(config.get('deck') or os.getenv('ANKI_DECK', 'English::AI Words')),
        'note_type': str(config.get('note_type') or os.getenv('ANKI_NOTE_TYPE', 'AI English Word')),
        'provider': provider,
        'model': str(llm.get('model') or os.getenv(model_env, fallback_model)),
        'api_url': str(llm.get('api_url') or os.getenv(url_env, fallback_url)),
        'prompt': str(llm.get('prompt_template') or ''),
        'anki_url': os.getenv('ANKI_CONNECT_URL', 'http://127.0.0.1:8765'),
        'tags': 'ai-word', 'threads': 0, 'sleep': 0.2, 'no_images': False, 'update_existing': False,
        'use_existing_note_type': bool(config.get('use_existing_note_type', False)),
        'duplicate_enabled': duplicate is not False,
        'duplicate_field': str(duplicate.get('field', 'expression') if isinstance(duplicate, dict) else 'expression'),
        'field_map': field_map,
        'image_source': str(image.get('source') or 'generated'),
        'image_provider': str(image.get('provider') or 'pixabay').lower(),
        'image_per_page': max(3, number(image.get('per_page'), 3)),
        'image_limit': max(1, min(number(image.get('per_word_limit'), 3), 3)),
        'image_model': os.getenv('OPENAI_IMAGE_MODEL', 'gpt-image-1'),
        'image_size': '1024x1024', 'image_quality': 'low',
        'llm_key_configured': configured(llm.get('api_key')) or any(os.getenv(k) for k in llm_env),
        'image_key_configured': configured(image.get('api_key')) or bool(os.getenv('PIXABAY_API_KEY')),
        'image_generation_key_configured': bool(os.getenv('OPENAI_API_KEY')),
    }

def count_anki_inputs(words, entries):
    """Match the CLI's count: entries are rows, text is case-insensitively unique."""
    if entries:
        return len(entries)
    return len({word.lower() for word in re.split(r'[\s,，;；]+', words) if word})


def run_anki(job_id, data):
    keys = []
    try:
        config = json.loads((ROOT / 'ankimaker' / 'anki_config.json').read_text(encoding='utf-8'))
        config = copy.deepcopy(config)
        llm = config.setdefault('llm', {})
        image = config.setdefault('image', {})
        for target, source in [('provider','provider'), ('model','model'), ('api_url','api_url'), ('prompt_template','prompt')]:
            if source in data: llm[target] = str(data[source] or '')
        for target, source in [('source','image_source'), ('provider','image_provider'), ('per_page','image_per_page'), ('per_word_limit','image_limit')]:
            if data.get(source) not in (None, ''): image[target] = data[source]
        for key in ('deck','note_type'):
            if data.get(key): config[key] = str(data[key])
        if 'use_existing_note_type' in data:
            if not isinstance(data['use_existing_note_type'], bool): raise ValueError('笔记类型选项必须为布尔值')
            config['use_existing_note_type'] = data['use_existing_note_type']
        if 'duplicate_enabled' in data:
            if not isinstance(data['duplicate_enabled'], bool): raise ValueError('重复检查选项必须为布尔值')
            field = str(data.get('duplicate_field') or 'expression')
            if data['duplicate_enabled'] and (not field or any(ch.isspace() or ch in ':\"\\' for ch in field)): raise ValueError('重复检查字段不能包含空白、冒号、引号或反斜杠')
            config['duplicate_check'] = {'field': field} if data['duplicate_enabled'] else False
        if 'field_map' in data:
            field_map = data['field_map']
            if not isinstance(field_map, dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in field_map.items()):
                raise ValueError('字段映射必须是字段名与来源均为字符串的 JSON 对象')
            config['field_map'] = field_map
        for section, source in [(llm,'api_key'), (image,'image_key')]:
            if data.get(source): section['api_key'] = str(data[source])
            if str(section.get('api_key', '')).startswith('YOUR_'): section['api_key'] = ''
            if section.get('api_key') and not str(section['api_key']).startswith('YOUR_'): keys.append(str(section['api_key']))
        # Pre-generated entries do not need credentials, but the legacy CLI resolves them anyway.
        if data.get('entries') and (not llm.get('api_key') or str(llm['api_key']).startswith('YOUR_')): llm['api_key'] = 'unused'
        job_dir = ROOT / 'anki_jobs' / job_id
        job_dir.mkdir(parents=True, mode=0o700)
        config['outputs'] = {k: str(job_dir / n) for k,n in {'success_txt':'success.txt','success_xlsx':'success.xlsx','failed_txt':'failed.txt','failed_xlsx':'failed.xlsx','log_jsonl':'events.jsonl'}.items()}
        words = str(data.get('words') or '')
        entries = data.get('entries')
        if not words.strip() and not entries: raise ValueError('请填写单词或导入卡片 JSON')
        if entries is not None and (not isinstance(entries,list) or not entries): raise ValueError('卡片 JSON 必须是非空数组')
        total = count_anki_inputs(words, entries)
        completed = set()
        update(job_id, progress={'total':total, 'completed':0})
        with tempfile.TemporaryDirectory(prefix='words-anki-') as tmp:
            temp = Path(tmp)
            config_path = temp / 'config.json'
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            os.chmod(config_path, 0o600)
            args = [data.get('_python', sys.executable), '-u', str(ROOT / 'ankimaker' / 'ankimaker.py'), '--config', str(config_path), '--image-dir', str(job_dir / 'images'), '--preview-json', str(job_dir / 'preview.json')]
            if entries:
                entries_path = temp / 'entries.json'
                entries_path.write_text(json.dumps(entries, ensure_ascii=False), encoding='utf-8')
                args += ['--entries-json', str(entries_path)]
            else:
                word_path = temp / 'words.txt'
                word_path.write_text(words, encoding='utf-8')
                args += ['--file', str(word_path)]
            for key,flag in [('deck','--deck'),('note_type','--note-type'),('image_model','--image-model'),('image_size','--image-size'),('image_quality','--image-quality')]:
                if data.get(key): args += [flag,str(data[key])]
            threads=int(data.get('threads') or 0)
            if not 0 <= threads <= 32: raise ValueError('并发线程数须在 0 到 32 之间')
            if threads: args += ['--threads',str(threads)]
            sleep=float(data.get('sleep',0.2))
            if not 0 <= sleep <= 60: raise ValueError('每词间隔须在 0 到 60 秒之间')
            args += ['--sleep',str(sleep)]
            if 'tags' in data:
                args.append('--no-default-tag')
                for tag in str(data.get('tags') or '').split(): args += ['--tag',tag]
            if data.get('no_images'): args.append('--no-images')
            if data.get('update_existing'): args.append('--update-existing')
            if data.get('dry_run'): args.append('--dry-run')
            env = os.environ.copy()
            if data.get('anki_url'): env['ANKI_CONNECT_URL'] = str(data['anki_url'])
            if data.get('image_generation_key'): env['OPENAI_API_KEY']=str(data['image_generation_key']); keys.append(env['OPENAI_API_KEY'])
            proc = start_managed(job_id, args, cwd=job_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
            output = ''
            try:
                for line in proc.stdout:
                    for key in keys: line=line.replace(key,'[REDACTED_SECRET]')
                    output=(output+line)[-60000:]
                    match = re.match(r'^\[(\d+)/(\d+)\] (?:END|EXISTS):', line)
                    if match and int(match.group(2)) == total and 1 <= int(match.group(1)) <= total:
                        completed.add(int(match.group(1)))
                    update(job_id, log=output, progress={'total':total, 'completed':len(completed)})
            finally:
                proc.stdout.close()
            rc=proc.wait()
            forget_process(job_id, proc)
            # The legacy logger can include service errors containing credentials.
            for path in list(job_dir.glob('*.jsonl')) + list(job_dir.glob('*.txt')):
                content=path.read_text(encoding='utf-8',errors='replace')
                for key in keys: content=content.replace(key,'[REDACTED_SECRET]')
                path.write_text(content,encoding='utf-8')
            rows = []
            success = job_dir / 'success.xlsx'
            if success.exists():
                with zipfile.ZipFile(success) as archive:
                    sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
                ns = {'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                matrix = [[ ''.join(c.itertext()) for c in row.findall('m:c', ns)] for row in sheet.findall('.//m:row', ns)]
                if matrix:
                    rows = [dict(zip(matrix[0], row)) for row in matrix[1:]]
            imported = [r.get('word', '') for r in rows if r.get('action') in ('added','updated','already_exists')]
            failed_count = len((job_dir / 'failed.txt').read_text(encoding='utf-8').splitlines()) if (job_dir / 'failed.txt').exists() else 0
            update(job_id,status='done' if rc==0 else 'failed',log=output or f'进程退出：{rc}',result={'directory':str(job_dir.relative_to(ROOT)), 'exit_code':rc, 'imported':imported, 'failed_count':failed_count})
    except Exception as exc:
        message=f'{type(exc).__name__}: {exc}'
        for key in keys: message=message.replace(key,'[REDACTED_SECRET]')
        update(job_id,status='failed',log=message)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(WEB), **kwargs)
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        super().end_headers()
    def reply(self, status, obj):
        raw=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def project_error(self, exc):
        status = 404 if isinstance(exc, FileNotFoundError) else 409 if isinstance(exc, FileExistsError) else 403 if isinstance(exc, PermissionError) else 400 if isinstance(exc, (ValueError, TypeError)) else 500
        self.reply(status, {'error':str(exc)})
    def do_GET(self):
        parsed=urlsplit(self.path);path=parsed.path
        if path.startswith('/api/project/'):
            if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}': return self.reply(403,{'error':'只允许本机工作台调用'})
            try:
                raw=parse_qs(parsed.query,keep_blank_values=True).get('path',[''])[0]
                handlers={'/api/project/info':lambda:{'name':ROOT.name,'path':str(ROOT.resolve())}, '/api/project/entry':lambda:project_entry(raw), '/api/project/list':lambda:project_list(raw), '/api/project/file':lambda:project_read(raw)}
                if path not in handlers: return self.reply(404,{'error':'接口不存在'})
                self.reply(200,handlers[path]())
            except (OSError, ValueError, TypeError) as exc: self.project_error(exc)
        elif path.startswith('/api/jobs/'):
            job_id=path.removeprefix('/api/jobs/')
            with LOCK: job=copy.deepcopy(JOBS.get(job_id))
            self.reply(200,job) if job else self.reply(404,{'error':'任务不存在'})
        elif path=='/api/health': self.reply(200,{'ok':True})
        elif path=='/api/anki-config':
            try: self.reply(200,anki_config_defaults())
            except Exception as exc: self.reply(500,{'error':'无法读取 Anki 配置：'+str(exc)})
        elif path=='/api/environment':
            try: self.reply(200,environment_info())
            except Exception as exc: self.reply(500,{'error':str(exc)})
        elif path.startswith('/api/'): self.reply(404,{'error':'接口不存在'})
        else: super().do_GET()
    def do_POST(self):
        origin=self.headers.get('Origin','')
        expected=f'http://127.0.0.1:{self.server.server_port}'
        if origin!=expected or self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}': return self.reply(403,{'error':'只允许本机工作台调用'})
        path=urlsplit(self.path).path
        if path not in ('/api/frequency','/api/anki','/api/lemmas','/api/environment','/api/environment/setup','/api/environment/choose','/api/jobs/cancel','/api/project/init','/api/project/create','/api/project/write'): return self.reply(404,{'error':'接口不存在'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>MAX_BODY: raise ValueError('请求大小超出限制')
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict): raise ValueError('请求格式错误')
            if path.startswith('/api/project/'):
                try:
                    if path == '/api/project/init': result=project_init()
                    elif path == '/api/project/create': result=project_create(data.get('path'))
                    else: result=project_write(data.get('path'),data.get('data'),data.get('sha256'))
                    self.reply(200,result)
                except (OSError, ValueError, TypeError) as exc: self.project_error(exc)
                return
            if path == '/api/jobs/cancel':
                cancel_job(str(data.get('id') or ''))
                self.reply(202, {'status':'stopping'})
                return
            if path == '/api/environment/choose':
                try:
                    self.reply(200, {'path':choose_environment_directory(data.get('mode'))})
                except (OSError, RuntimeError, ValueError) as exc:
                    self.reply(400, {'error':str(exc)})
                return
            if path == '/api/environment':
                self.reply(200, save_environment(data.get('path')))
                return
            info = environment_info()
            if path != '/api/environment/setup' and not info['exists']:
                self.reply(400, {'error':'所选虚拟环境不存在或无效，请在环境设置中检查路径并初始化'})
                return
            requested_id = data.pop('_job_id', None)
            if requested_id is not None and (not isinstance(requested_id, str) or not re.fullmatch(r'[0-9a-f]{32}', requested_id)):
                raise ValueError('任务编号无效')
            job_id = requested_id or uuid.uuid4().hex
            with LOCK:
                if job_id in JOBS:
                    raise ValueError('任务编号已存在')
                JOBS[job_id]={'status':'running','log':'任务已启动','result':None,'kind':path.rsplit('/',1)[-1]}
            if path == '/api/environment/setup':
                target, payload = run_environment_setup, Path(info['path'])
            else:
                data['_python'] = info['python']
                target, payload = {'/api/frequency':run_frequency_in_environment,'/api/anki':run_anki,'/api/lemmas':run_lemmas}[path], data
            threading.Thread(target=target,args=(job_id,payload),daemon=True).start()
            self.reply(202,{'id':job_id})
        except (ValueError, json.JSONDecodeError) as exc: self.reply(400,{'error':str(exc)})


def main():
    parser=argparse.ArgumentParser(description='启动单词工作台（仅监听本机）')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--open-browser', action='store_true', help='启动后自动打开浏览器')
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    url=f'http://127.0.0.1:{server.server_port}/'
    print(f'打开 {url}',flush=True)
    def open_browser():
        try:
            if not webbrowser.open(url, new=2):
                print('未能自动打开浏览器，请手动访问上面的地址。', flush=True)
        except Exception as exc:
            print(f'未能自动打开浏览器：{exc}；请手动访问上面的地址。', flush=True)
    if args.open_browser:
        threading.Thread(target=open_browser, daemon=True).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        with LOCK:
            running_ids = list(PROCESSES)
        for job_id in running_ids:
            try: cancel_job(job_id)
            except ValueError: pass
        server.server_close()

if __name__=='__main__': main()
