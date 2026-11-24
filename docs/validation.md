# 验证记录

## rc.16 下载包验收

Windows 发布检查从解压后的项目运行 README 的 PowerShell 导出命令，用转存后的同一份聊天 ZIP 验证预览、项目路径、正文、受控中断恢复和重复导入。包内回归覆盖旧 SQLite 的解释器切换、写 ZIP 时强制中止、发布时目标文件已存在，以及深目录导出。

发布检查 JSON 分别记录 `packaged_tests`、`packaged_tests_skipped` 和各组跨版本读回。62/8 目标只检查数据库，三个 64/9 目标还验证本机 Codex 接口。Mac 的应用退出保护和普通窗口侧栏、默认工作目录待实机确认。

## rc.15 实现复核

修正版从源码 ZIP 解压后通过 50 项测试，README 的 Windows 命令、传输校验、装机版结构检查和四组合并通过。三组 64/9 目标检查了本机 Codex 接口。

同类实现的源码对照和具体差异见[实现复核](implementation-review.md)。本版补充共享模型标识的子会话单独导入、同组后续导入、冲突拒绝和缺少源主会话的检查。另增加触发器忽略插入的回归，以及公共 CLI 对真实 64/9 压缩源的导出验证。

装机版读回覆盖第四组 64/9→64/9 压缩会话。Zstandard 样本使用符合格式的 raw block 帧，由本机 Codex 解码。文件保持压缩状态传输，正文与完整轮次读回成功。跨版本核心验证继续覆盖下表三组。

## rc.14 跨版本兼容

2026-11-22 在 Windows 使用 Python 3.13.5、SQLite 3.49.1 和 Codex CLI 0.171.0-alpha.13 验证。测试数据全部由虚构会话生成，结构来自两份只含建表语句和 SQLx 元数据的样本。

| 来源 → 目标 | 合并、正文和项目数据 | 目标结构与 SQLx 记录 | 重复预览与重复合并 | 本机 Codex 读回 |
| --- | --- | --- | --- | --- |
| 62/8 → 62/8 | 通过 | 保留 | 零写入 | 只做数据库检查 |
| 62/8 → 64/9 | 通过 | 保留 | 零写入 | 正文、轮次和项目归属通过 |
| 64/9 → 64/9 | 通过，包含所选会话的 session_model_ids | 保留 | 零写入 | 正文、轮次和项目归属通过 |

从构建后的 ZIP 在中文及空格目录解压，执行 README 的 Windows 导出命令、SHA-256 转存核对及 46 项测试，装机版结构与跨版本读回验收通过。另用 rc.13 的导出器生成 62/8 旧格式 ZIP，再由 rc.14 导入 64/9 目标，新增一条、重复预览零写入。

附加回归覆盖新增普通索引、版本号增加、目标新增带默认值字段、字段顺序变化、只选部分会话、源字段含数据时目标缺失、无默认值的必填字段、未知关联表、触发器变化，以及跨版本写入中断后的恢复。回归数据同时检查原有会话和未选中会话的保留情况。

本机 Codex 的 `thread/read` 会返回历史会话文件中的原始 cwd。验收另行检查目标数据库的映射路径，并通过显式带上映射路径的 `thread/resume` 验证实际工作目录。普通桌面如何选择该目录仍待 Mac 实机确认。

rc.14 尚无 Mac 实机报告。Windows 上的核心和本地接口验收不涵盖 Mac 正式退出保护与普通桌面侧栏。

## 早期版本

- rc.13 在 Windows 下载包中通过 38 项测试。本机新版 Codex 创建的 64/9 资料在固定版本检查处停止，这次限制已由 rc.14 的结构检查替代。
- rc.12 在 2026-11-22 收到使用者完整跑过 Mac 迁移流程的反馈。
- rc.10 在 Mac 隔离资料中验收过 SHA-256、备份、受控中断恢复、文件占用、正文和项目读回。环境为 Python 3.12.14、SQLite 3.53.1、Codex CLI 0.169.0-alpha.11.1、桌面版 26.1111.21308。

## 如何复现

- 从[贡献指南](../CONTRIBUTING.md)运行回归测试、下载包检查或 `tools/verify_compatibility.py`。
- 用 Windows 和 Mac 各一条虚构聊天走完正式导出、预览、合并及读回步骤，见[双机验收](full-rehearsal.md)。
- 单独运行 Mac 核心验收时，在 Windows 项目目录运行下面的命令。脚本会查找已安装的 Codex CLI，也可以加 `--codex` 指定可执行文件的路径。虚构迁移包生成成功后，再写入 SHA-256。

  ```powershell
  New-Item -ItemType Directory -Force ..\verification\mac-test
  python -B tools/accept_mac.py --build-fixture ..\verification\mac-test\windows-fictional.zip
  (Get-FileHash -LiteralPath ..\verification\mac-test\windows-fictional.zip -Algorithm SHA256).Hash.ToLowerInvariant() | Set-Content -LiteralPath ..\verification\mac-test\windows-fictional.sha256 -Encoding ascii
  ```

  将 `mac-test/` 文件夹复制到 Mac 仓库外，再按 [Mac 自动验收](mac-acceptance.md)传入它的路径。
