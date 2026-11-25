# 使用指南

**简体中文** · [English](usage.en.md)

## 检查 Python

Mac 直接使用 README 中的 `/bin/bash ./mac.sh`。它会检查本机的 Codex 运行时、常见安装目录、环境管理器和 PATH 里的 Python。预览和合并要求 Python 3.11+，其实际加载的 SQLite 要达到 3.41.0，且能读取 Codex 使用的 JSON 表达式索引。用能启动的旧 `python3` 执行 `cli.py` 时也会转到这个入口。若系统 `python3` 弹出安装提示，请用 README 中的 Bash 命令。

Windows 的 `python` 能启动但 Python 或 SQLite 不符合要求时，程序会继续检查已注册的 Python、用户安装目录和 PATH。若 PowerShell 根本找不到 `python`，而电脑已装 Python 启动器，可用 `py -3 -B .\cli.py export .\Codex-Windows-History.zip`。两端都只使用已安装的解释器。Mac 可通过 `CODEX_MIGRATION_PYTHON` 指定解释器的完整路径。若终端提示找不到可用解释器，查看它显示的 Python 路径和 SQLite 版本。

## Mac 解压遇到问题

在访达中双击项目 ZIP，打开解压后同时包含 `mac.sh` 和 `cli.py` 的文件夹。聊天迁移 ZIP 请保持压缩状态，直接传给 `plan`。若下载的项目 ZIP 解压中断，请重新取得完整的项目包，或用已有 Python 的 `zipfile` 解压到新文件夹。

## 路径或文件找不到

如果 Windows 的 PowerShell 提示路径不存在，或导出时提示找不到 `cli.py`，可以先检查当前打开的文件夹。在 **PowerShell 窗口**粘贴下面这条命令并按回车：

```powershell
Test-Path .\cli.py
```

- 显示 `True`：当前文件夹里有 `cli.py`，可以继续[导出](../README.md#2-在-windows-导出聊天)。
- 显示 `False`：在文件资源管理器里打开能同时看到 `README` 和 `cli` 的那一层，重新复制顶部的文件夹路径。回到 PowerShell，把 `cd "实际文件夹路径"` 里的路径替换成刚复制的路径，再按回车。

Mac 上可以在「终端」用 `ls cli.py` 检查当前文件夹。找不到时，按 [README 第 3 步](../README.md#3-在-mac-预览)把项目文件夹拖进终端。

如果程序已开始运行后报文件不存在，新版会显示失败步骤和具体路径：

- 源资料目录不存在：核对程序打印的源目录；自定义位置用 `--source` 指定。
- 缺少会话文件：确认源目录中的 `sessions`、`archived_sessions` 没被移动。程序会停止，不会把缺少正文的会话当作迁移成功。
- 解压或输出位置较深时，旧版可能因 Windows 长路径限制而找不到临时副本。rc.16 修复了深目录中的导出快照和打包。请解压完整新版后再试。

## 输出 ZIP 已存在

导出不覆盖已有文件。如需重新导出，在命令末尾换一个新名称，例如 `Codex-Windows-History-2.zip`。传到 Mac 后，预览命令里的文件名也要改成同一个名称。

导出中断时，等待原进程退出后可以用同一个输出名称重试。若目录中留下 `.codex-export-` 开头、`.partial` 结尾的文件，它是未完成的临时包。传输时选终端报告成功的 ZIP。

## 导出提示文件正在使用

`Windows source file is in use` 后面会显示无法安全读取的文件名，例如 `state_5.sqlite`。本次没有生成 ZIP。保存工作，正常退出正在使用这份 Codex 资料的应用或命令行，然后在原来的 PowerShell 重试。若已退出 Codex，检查是否还开着这份资料的数据库查看器或正在同步该目录。导出工具不会结束任何进程。

旧版提示 `Quit the Windows Codex app and CLI` 或 `Windows Codex is still running` 时，请使用 rc.10 或更新的版本。新版直接检查导出的文件，不再按进程名判断。

## 指定资料目录和附件

- Windows `export` 默认使用 `CODEX_HOME`，未设置时读取 `%USERPROFILE%\.codex`。需要指定其他目录时，在导出命令后加 `--source "D:\Path\To\Profile"`。输出 ZIP 必须在源资料目录之外且尚未存在。
- Mac `plan`、`apply` 和 `recover` 默认使用 `CODEX_HOME`，未设置时使用 `~/.codex`。预览时会打印实际目标目录。如果使用自定义资料目录，或先在 Mac 的资料副本上验证，给预览传 `--target "/path/to/profile"`；预览生成的合并命令会自动保留该参数。两份数据库 `state_5.sqlite` 和 `thread_history_1.sqlite` 都必须存在。
- Windows 导出想带上支持的附件、计划和生成图像，可加 `--include-assets`。工具不会重写历史正文里的 Windows 路径，也不复制外部附件。

部分导入只带上所选聊天关联的数据及会话文件。随包导出且无法归属到具体聊天的独立附件，只在全选时处理。

## 先用独立副本试运行

在 Mac 完全退出 Codex 后，新建一个空文件夹，例如「下载」中的 `Codex-History-Test`。从 Mac 的资料目录复制以下文件到新文件夹：

- `state_5.sqlite`、`thread_history_1.sqlite`，以及它们现有的 `-wal`、`-shm` 或 `-journal` 文件。
- `session_index.jsonl` 和 `.codex-global-state.json`（如果存在）。按 `⌘⇧.` 可在访达显示隐藏文件。

只验证合并时，不必复制原有会话文件、登录文件或应用配置。这份测试副本不会被普通 Codex 窗口读取。运行 `plan` 时加上 `--target "$HOME/Downloads/Codex-History-Test"`，再使用预览输出的合并命令。决定合并到日常使用的资料时，改回正确的资料目录，重新 `plan`，使用新输出的命令。

## 检查迁移包和兼容性

在 Mac 的终端输入 `shasum -a 256` 和一个空格，把实际用于迁移的 ZIP 拖进终端再按回车。例如原名保存在「下载」时，可运行 `shasum -a 256 "$HOME/Downloads/Codex-Windows-History.zip"`。将结果与对应的 Windows 导出报告中的 `sha256` 比较。值不同时，核对文件名和位置后重新复制迁移包。

工具检查迁移所需的表、字段、主键、关联表及触发器，按字段名称合并到 Mac 现有结构。已验证 62/8→62/8、62/8→64/9、64/9→64/9。新增普通索引或可填充字段可以通过结构检查，版本编号增加后也可继续预览。新增聊天关联表、触发器变化、目标缺少含数据的源字段、或者必填字段无法填充时，会显示具体原因并停止。

较新的包导入较旧的目标时，所选数据必须能够完整表示。例如包内含有 `session_model_ids` 记录，而目标没有该表时，预览会指出该表并停止。只更新数据库版本编号不能解决这个问题。

导出会把迁入会话的沙箱记录设为只读，审批记录设为从不，并清理 Windows 项目的机器相关元数据。Mac 打开会话后，以当前客户端显示的实际权限和工作目录为准。

`source_checksums_match: false` 表示两端 SQLx 迁移记录摘要有差异。结构检查通过时，合并保留目标的表定义和迁移记录。ZIP 的 SHA-256 用于单独核对文件传输。

导出只包含 Windows 状态库能索引到的会话，未选中的文件会在结果中说明。合并后再次执行 `plan`，已导入的同 ID 会话会被保留；没有变化时显示“无需合并”。

交互式终端的 `plan` 默认显示选择菜单。自动化脚本可用 `--all` 或 `--pick 1,3-5` 指定范围。编号按包内会话文件记录的开始时间从新到旧排列，同一时间按会话 ID 排序。`--json` 或非交互输入默认全选，预览编号与迁移包、目标和所选范围绑定。部分导入时，缺席的父聊天不会被一并导入；需要保留父子关系时请一起勾选。

开发者需要完整的 JSON 报告时，可在任一命令末尾加 `--json`。其中 `source_checksums_match` 比较数据库迁移记录，`unindexed_session_files` 记录未选中的会话文件数，`files_to_change` 记录本次待写入的文件数。

## 合并中断后恢复

如果合并出错，先看终端结果。尚未写入目标时可检查原因后重新预览；如果提示有未完成的安装，保持应用退出，在项目目录运行：

```bash
/bin/bash ./mac.sh recover
```

使用自定义 `--target` 时，恢复也要传入同样的路径。工具会核对备份和现有文件，发现其他进程已经改动过资料时会停止并保留备份。恢复成功后重新 `plan`。`recover` 只恢复未完成的安装，不能撤销已经成功的合并。
