# 贡献者指南：双机全流程验收

**简体中文** · [English](full-rehearsal.en.md)

用于发布前确认两台设备上的正式 `export`、`plan`、`apply` 流程。准备脚本在解压目录内创建一条虚构 Windows 会话和一条虚构 Mac 会话；后续命令都明确传入测试资料路径。验收覆盖正文、项目归属、备份和重复导入。迁移实际会话请从 [README](../README.md) 开始。

## 1. 下载同一份源码 ZIP

从 GitHub 项目首页点 **Code → Download ZIP**，或从 **Releases** 下载指定版本的源码 ZIP。发布前也可用待发布的源码 ZIP 验证解压后的步骤。

在 Windows 和 Mac 各解压一份，打开里面含 `cli.py` 的目录。Mac 可在任何时候取得源码 ZIP，Windows 导出完成后，只需跨设备传输导出的迁移 ZIP。可以用 U 盘或你方便的方式；这次的迁移 ZIP 仅含虚构聊天。

两台电脑需要已安装的 Codex、Python 3.11+ 和该解释器加载的 SQLite 3.41.0+。Mac 的迁移命令使用 README 中的 `mac.sh`。验收使用测试资料和本机接口，无需新开登录窗口。

## 2. Windows：准备一条虚构会话，再用正式命令导出

从 Windows 开始菜单打开 PowerShell，用 `cd "项目文件夹的完整路径"` 进入已解压的项目目录。检查入口：

```powershell
Test-Path .\cli.py
python -B .\tools\full_flow_fixture.py prepare
```

第一行应显示 `True`，第二行的结果应有 `"status": "ready"`。然后从系统托盘退出 Windows ChatGPT/Codex，关闭使用 Codex 的其它窗口和 CLI。保留这个 PowerShell 窗口，运行：

```powershell
python -B .\cli.py export .\Rehearsal-Windows-History.zip --source .\migration-rehearsal\profile --json
```

检查 `"threads": 1`，把 `Rehearsal-Windows-History.zip` 复制到 Mac 那份解压后的目录，和 `cli.py` 放在同一层。也可以记下导出结果里的 `sha256`，到 Mac 核对。

## 3. Mac：准备原有会话，演练

打开 macOS 自带的「终端」。输入 `cd `（最后有一个空格），从访达把含 `cli.py` 的文件夹拖进终端，再按回车。先执行：

```bash
ls cli.py Rehearsal-Windows-History.zip
PY=python3
"$PY" -B -c 'import sys, sqlite3; print(sys.version.split()[0], sqlite3.sqlite_version)'
```

如果默认 `python3` 不符合要求，将 `PY` 设为已安装的 Python 解释器的绝对路径。重复检查，确认 Python 至少为 3.11、SQLite 至少为 3.41.0。这个解释器用于准备和核对测试资料，正式迁移命令会自动查找可用解释器。

准备 Mac 的一条虚构会话（请在同一个终端执行）：

```bash
"$PY" -B tools/full_flow_fixture.py prepare
```

看到 `"status": "ready"` 后，按 **⌘Q** 完全退出常用 ChatGPT/Codex，关闭其它正在运行的 Codex CLI。保持独立「终端」开着。运行：

```bash
shasum -a 256 Rehearsal-Windows-History.zip
/bin/bash ./mac.sh plan Rehearsal-Windows-History.zip --target ./migration-rehearsal/profile --json
```

SHA-256 应与 Windows 导出报告相同。演练应显示 `"local_threads": 1`、`"added_threads": 1`、`"total_threads": 2`，并列出一个测试项目的占位路径。虚构项目没有实际代码，所以用默认占位目录。记下报告里的 `plan_id`（只复制引号里的字符）。两端结构检查通过时，`source_checksums_match: false` 可以是正常的；它与传输 ZIP 的 SHA-256 是两种检查。

## 4. Mac：确认、合并、验收

保持 Codex 退出。把下方 `这里替换成plan_id` 换成刚才复制的值，保留 `--target`：

```bash
/bin/bash ./mac.sh apply Rehearsal-Windows-History.zip --target ./migration-rehearsal/profile --approve '这里替换成plan_id' --json
```

结果应显示 `"target_changed": true`、`"total_threads": 2`，并给出 `backup` 位置。运行下面两行，确认再演练不会重复导入，并让本机 Codex 从测试资料里读回两条虚构会话：

```bash
/bin/bash ./mac.sh plan Rehearsal-Windows-History.zip --target ./migration-rehearsal/profile --json
"$PY" -B tools/full_flow_fixture.py verify
```

第一条应显示 `"files_to_change": 0`，第二条应显示 `"status": "passed"`。它会核对原有会话文件未变，备份匹配，正文和项目归属能读回。这次不打开隔离的桌面窗口；侧栏目视验收不在本次范围。完成后平时用的 Codex 可以照常打开。

如果合并中断并提示需要恢复，保持应用退出，执行 `/bin/bash ./mac.sh recover --target ./migration-rehearsal/profile`。恢复后重新 `plan`。如果从头重跑，使用一份全新解压的项目目录。
