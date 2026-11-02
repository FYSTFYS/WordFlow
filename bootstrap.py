#!/usr/bin/env python3
"""Prepare a selected Python environment for the workbench."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
PYPI_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'


def python_path(env: Path) -> Path:
    if os.name == 'nt':
        conda = env / 'python.exe'
        if conda.is_file():
            return conda
        return env / 'Scripts' / 'python.exe'
    return env / 'bin' / 'python'


def run_checked(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def usable(python: Path, statement: str) -> bool:
    return subprocess.run([str(python), '-c', statement], cwd=ROOT,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description='创建或检查工作台的 Python 虚拟环境')
    parser.add_argument('--venv', default=str(ROOT / '.venv'), help='虚拟环境目录')
    parser.add_argument('--prepare-only', action='store_true', help='只准备环境')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    env = Path(args.venv).expanduser()
    if not env.is_absolute(): env = ROOT / env
    env = env.resolve()
    python = python_path(env)
    try:
        if not python.is_file():
            if env.exists() and (not env.is_dir() or any(env.iterdir())):
                raise RuntimeError('所选目录已存在但没有虚拟环境 Python，未对目录进行修改')
            print(f'正在创建虚拟环境：{env}', flush=True)
            venv.EnvBuilder(with_pip=True).create(env)
            python = python_path(env)
        if not (env / 'pyvenv.cfg').is_file() and not (env / 'conda-meta').is_dir():
            raise RuntimeError('所选目录不是受支持的虚拟环境（未找到 pyvenv.cfg 或 conda-meta）')
        print(f'使用虚拟环境：{env}', flush=True)
        if not usable(python, 'import spacy, lemminflect, wordfreq'):
            print('正在通过清华 PyPI 源安装项目依赖，首次运行可能需要几分钟…', flush=True)
            run_checked([str(python), '-m', 'pip', 'install', '--index-url', PYPI_INDEX, '-r', str(ROOT / 'requirements.txt')])
            if not usable(python, 'import spacy, lemminflect, wordfreq'):
                raise RuntimeError('依赖安装后仍无法导入，请检查上方错误')
        if not usable(python, "import spacy; spacy.load('en_core_web_sm')"):
            print('正在安装 spaCy 英文模型 en_core_web_sm（模型文件使用 spaCy 官方下载地址）…', flush=True)
            run_checked([str(python), '-m', 'spacy', 'download', 'en_core_web_sm', '--index-url', PYPI_INDEX])
            if not usable(python, "import spacy; spacy.load('en_core_web_sm')"):
                raise RuntimeError('英文模型安装后仍无法加载，请检查上方错误')
        print('环境准备完成。', flush=True)
        if args.prepare_only: return 0
        return subprocess.call([str(python), str(ROOT / 'workbench_server.py'), '--open-browser', '--port', str(args.port)], cwd=ROOT)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'初始化失败：{exc}\n请检查 Python、网络和上方错误后重试。', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
