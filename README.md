# English_words_frequency


## 单词工作台

HTML 页面位于 `web/`，由只监听本机的 Python 服务执行词频统计和 Anki 任务。首次使用先安装 Python 3，并确保联网。

Windows 双击 `start_workbench.bat`；Linux / macOS 在项目目录运行 `./start_workbench.sh`。启动脚本先打开页面。在「环境设置」中选择已有虚拟环境、选择存放目录或使用默认的项目内 `.venv`，也可手动输入路径；点击「初始化 / 安装组件」检查并安装缺失的依赖和 spaCy 英文模型。选择存放目录会在其中使用 `.venv`；路径不存在时会创建环境，非空且不是有效环境的目录不会被覆盖。选择窗口不可用时可手动输入路径。Python 组件使用清华 PyPI 源；spaCy 英文模型文件由 spaCy 从其官方地址下载，不能通过清华 PyPI 源获取。首次安装可能需要几分钟；下载失败时请检查网络后重新运行脚本。之后重新点击初始化只检查环境，不会重复安装已可用的组件。所选环境保存在服务项目的 `workbench_env.json`，新提交的词频、词元和 Anki 任务都使用它；正在运行的任务保持提交时的环境。

启动脚本会自动打开浏览器；若未打开，可用 Chrome 或 Edge 访问服务输出的 `http://127.0.0.1:8766/`。保持启动脚本运行。页面默认使用代码所在的项目目录，缺少 `vocabulary_exclude`、`wordlist`、`xlsxdir` 时会创建；也可点击「切换项目目录」选择其他目录并允许浏览器读写，或点击「使用默认目录」切换回来。不要用静态文件服务启动，否则无法执行统计和制卡。浏览器可能重新请求目录权限。

1. 在「任务」从 EPUB 新建任务，名称可留空，选择文件后自动使用文件名；也可选择已有的 `xlsxdir` 工作簿，从人工标记步骤开始。选择 EPUB 文件后统计词频，结果写入任务专属 XLSX。
2. 在任务工作簿中编辑最后一列 `tag`、筛选并保存，再进入排除步骤。可搜索或新建目标排除文件，并将特定标记的词写入其中。进入制卡时使用当前表头筛选及搜索结果，不额外排除其他行。
3. 完成排除后，制卡页面显示当前输入数量及已处理进度（包含跳过和失败），并默认读取服务项目的 `ankimaker/anki_config.json` 并展示牌组、模型、提示词、图片设置、字段映射和重复检查设置；可手动修改后预览或启动导入。页面修改只作用于当前任务，不改写配置文件。点击「保存任务设置」可在台账保留非敏感选项，重新读取配置文件可恢复默认值。预览不会写入 Anki；导入完成且没有失败单词后，点击「回写状态并完成任务」，将成功单词的 `anki_added` 更新并保存到工作簿。失败单词需检查日志后重试。
4. 「任务」页面可继续、中止或删除任务记录。中止会停止正在执行的统计或制卡进程；已写入 Anki 的卡片和已生成文件会保留。删除只删除任务记录，保留工作簿和 Anki 结果。运行中需先中止，完成后才能删除记录。

任务及所选单词保存在当前项目目录的 `xlsxdir/task_records.xlsx`，任务工作簿保存在同一目录。服务运行期间刷新或重连项目可恢复执行状态；如果服务已经重启，原执行状态可能无法确认，需检查已写入的 Anki 内容并重新选择 EPUB 后再继续。Anki 任务保留部分普通配置，但 API 密钥、自定义地址、提示词和导入的完整 JSON 不写入台账，恢复时需重新填写或导入。Anki 日志与结果在服务项目的 `anki_jobs/<任务编号>/`。如任务台账被其他程序修改，页面会拒绝覆盖，需重新打开项目目录。

「词元统计」独立于任务，只负责 EPUB/TXT 词元和频率区间统计及 TXT 导出。Anki 仅过滤频率区间，不改变词元总数或导出列表；Anki 不可用时会显示警告。

排除文件新增和删除立即写入，所有文件都会参与排除。标记词表使用 `.txt` 文件名，`readme.txt` 列出的文件优先，其余按路径排序。工作簿和任务台账保存前会检查外部修改并拒绝覆盖；建议先关闭 Excel。普通 XLSX 可编辑，复杂公式、宏或数字签名文件不适用。外部更改目录后请重新选择项目目录。

页面只显示配置文件或环境变量中密钥的配置状态；输入新密钥可覆盖当前任务，留空则沿用已有值。页面中的 API 密钥只用于当前任务；任务配置放在临时目录并在任务结束后清理，不写入任务结果。大模型、图片和音频功能会访问所配置的外部服务。`ankimaker/anki_config.json` 可配置提示词、字段映射和默认值。开启更新已有笔记时使用串行导入。

运行 `python3 build_package.py`（Windows 使用 `py -3 build_package.py`）可创建 `dist/word-workbench.zip` 源码包，包含页面、Python 脚本和词表，但不含密钥、生成结果、Python 依赖或 spaCy 模型。解压后运行启动脚本，在页面中选择环境并初始化即可安装缺失的依赖。

## Prerequisites

Install the Python dependencies, then download the spaCy English model used for lemmatization:

```bash
python -m pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

## List unique lemmas per file

`list_lemmas.py` accepts EPUB files, TXT files, bare book names (resolved against `books/` and `txtdir/`), or folders. When done it prints a table per file — Book, Characters, Unique lemmas, and a Freq>=x / Freq<x column pair for every threshold x in `--freq` (default `10,5`); two footer rows follow every column — 合计 sums each column across books, 去重合计 counts the distinct lemmas across books (Characters has no deduped value). Pass `--out-dir` to also write each file's full lemma list to `<out-dir>/<stem>.txt` (one lemma per line):

```bash
# Single TXT (bare names are looked up in txtdir/)
python list_lemmas.py "The Magic Finger"

# Single EPUB (bare names are looked up in books/)
python list_lemmas.py "Death on the Nile"

# Folders scan for EPUBs by default
python list_lemmas.py books/

# Write full lemma lists to lemmasdir/<stem>.txt
python list_lemmas.py --out-dir lemmasdir books/

# Order by frequency instead of alphabet, and keep vocabulary_exclude/ words
python list_lemmas.py --out-dir lemmasdir --sort count --no-exclude "Wonder (R. J. Palacio)"
```

Folder scanning only picks up the suffixes listed in `FOLDER_SCAN_SUFFIXES` at the top of `list_lemmas.py` (default: `.epub` only); edit that list, e.g. add `".txt"`, to scan more formats.

All files are lemmatized in a single spaCy pass (parser and NER disabled — lemma results are identical), so a whole folder costs the multi-process startup only once. For tiny inputs, `--workers 1` skips worker spawning entirely and is often faster.

Words listed in `vocabulary_exclude/` are dropped by default (same rule as the Excel workflow); pass `--no-exclude` to keep them. The frequency band columns count the lemmas in each Freq>=x / Freq<x band, after dropping vocabulary_exclude/ words and the words already in Anki; pass `--freq "20,10,5"` (any comma-separated list of positive integers, duplicates removed) to choose your own thresholds. The Anki check queries the local Anki instance through AnkiConnect (same as the Excel workflow: a lemma counts as known if the lemma itself or any of its inflected forms appears in an `OnlineDictHelper` note's `expression` field); keep Anki running with the AnkiConnect plugin, or point `--anki-url` elsewhere. If AnkiConnect is unreachable the script prints a warning and removes nothing; pass `--no-anki-words` to skip the Anki check entirely. `--start/--end` restrict EPUB inputs to a reading-progress percentage range, like the other scripts.
