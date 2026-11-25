# 贡献指南

**简体中文** · [English](CONTRIBUTING.en.md)

欢迎参与 ChatLuggage。修复迁移问题、补充测试，或把某一步写得更清楚，都能帮助下一位读者。实际迁移聊天请从 [README](README.md) 开始。

[报告问题](#报告问题) · [提交改动](#提交改动) · [测试与演练](#测试与演练)

## 可以贡献什么

| 你想做什么 | 从哪里开始 |
| --- | --- |
| 报告导出、预览、合并或恢复的问题 | 提一个 Issue，说明环境和复现步骤。 |
| 修正指南、命令示例或链接 | 可以直接提 PR。命令有变化时，说明在哪个平台验证过。 |
| 补充边界用例 | 在 `tests/` 中用虚构资料复现。数据库结构样本放在 `tests/fixtures/`。 |
| 改迁移包格式、路径映射或写入流程 | 可以先开 Issue 讨论预期行为，以及对旧迁移包和已有会话的影响。 |

## 报告问题

先查一下是否已有同类问题。新开 Issue 时，尽量说清这三件事。[GitHub Issues](https://github.com/itsmolu/chatluggage/issues)

1. **运行环境** Windows 或 macOS 版本、Codex 版本、ChatLuggage 版本，以及实际运行的 Python 和 SQLite 版本。
2. **复现步骤** 在哪条命令或哪一步遇到问题，预期会发生什么，实际发生了什么。
3. **错误信息** 附上相关报错和脱敏后的命令。能提供虚构资料的最小复现时，优先用它帮助定位。

测试请用虚构聊天。公开讨论时可以贴脱敏后的错误信息。真实聊天、迁移 ZIP、Codex 数据库、登录文件和备份请留在本机。

## 提交改动

1. 在项目页点击 **Fork**，把项目复制到自己的 GitHub 账号。将下面的 `YOUR-ACCOUNT` 换成你的用户名，再克隆并创建分支。

   ```bash
   git clone https://github.com/YOUR-ACCOUNT/chatluggage.git
   cd chatluggage
   git switch -c docs/clarify-preview
   ```

2. 在分支上修改文件，并运行适用的[测试与演练](#测试与演练)。文档改动也可以走一遍对应步骤，核对命令和链接。
3. 提交前查看本次改动。下面以更新 `docs/usage.md` 为例。

   ```bash
   git status --short
   git add docs/usage.md
   git diff --cached
   ```

4. 确认暂存的内容后提交，再推送到自己的 fork。

   ```bash
   git commit -m "Clarify the Mac preview instructions"
   git push -u origin docs/clarify-preview
   ```

5. 在 GitHub 创建 Pull Request，把 `itsmolu/chatluggage` 的 `main` 设为目标分支，选择你刚推送的分支作为来源。PR 说明可以参考下面的格式。

   ```markdown
   ## 改动与原因
   说明解决了什么问题，涉及哪些命令或文档。有对应 Issue 时附上链接。

   ## 验证
   写明测试平台、运行的命令和结果。适用时说明用虚构资料复现的步骤。

   ## 兼容性
   说明对已有迁移 ZIP、数据库结构、命令参数或项目路径的影响。
   ```

## 代码入口与改动约定

| 文件 | 职责 |
| --- | --- |
| `mac.sh` | 在已有运行时中选择可用 Python，并转交参数给正式 CLI |
| `cli.py` | 处理 `export`、`plan`、`apply` 和 `recover` 命令 |
| `chat_selection.py` | 整理月份、展示聊天列表并收集选择范围 |
| `windows_export.py` | 校验、筛选 Windows 本地资料，写入迁移 ZIP |
| `schema_compat.py` | 按实际字段比较结构、复制选中记录和检查新增关联 |
| `history_migration.py` | 校验 ZIP 和 SQLite、生成合并内容及项目路径映射 |
| `migration_workflow.py` | 检查文件占用、绑定预览编号、备份、写入和中断恢复 |
| `platform_paths.py` | Windows 长路径文件操作和临时目录 |
| `demo.py` | 本地虚构数据演示 |
| `tests/` | 回归测试和虚构资料 |
| `tools/` | 源码打包、下载包验收和装机版 Codex 验证 |
| `docs/` | 用户排障、项目映射、验证记录和贡献者验收说明 |

改动迁移逻辑时，检查公开入口上的预览、旧包兼容和中断恢复行为。调整命令或文件位置时，同步核对 README、导入路径与 `tools/build_release.py` 的打包清单。

文档中的步骤或行为有变动时，请同步更新对应的中文 `.md` 和英文 `.en.md`，并核对两边的语言切换链接。

## 测试与演练

在含 `cli.py` 的源码根目录运行以下命令。需要 Python 3.11+，以及该解释器加载的 SQLite 3.41.0+。Mac 上可将 `python` 换成满足要求的 `python3` 或解释器的完整路径。[检查 Python](docs/usage.md#检查-python)

```powershell
python -B -c "import sys, sqlite3; print(sys.version.split()[0], sqlite3.sqlite_version)"
python -B -m unittest -v
```

测试使用虚构资料。部分用例只在对应的操作系统运行。提交 PR 时，记录测试平台、通过和跳过的情况。涉及不同系统的入口时，也请标出实际走过的系统和步骤。

Windows 可运行 `python -B tools/verify_installed_schema.py`，使用隔离的 Codex CLI 生成原生表结构，再用虚构会话验证迁移。`python -B cli.py demo` 提供单机虚构演示；演示包与正式迁移包格式不同。

发布前可参考[双机全流程验收](docs/full-rehearsal.md)、[Mac 自动验收](docs/mac-acceptance.md)和[实测记录](docs/validation.md)。

## 构建和验收下载包

运行 `python -B tools/build_release.py <新ZIP路径>`，只打包清单内的源码和文档，检查导入依赖、文档链接和 ZIP 完整性，同时生成 `.zip.sha256`。不会包含会话、备份或认证文件。

把发布 ZIP 和配套 `.zip.sha256` 保存在仓库旁的 `release/`，把验收报告和虚构迁移包放在仓库旁的 `verification/`。源码或文档更新后，从更新后的仓库重新构建和验收下载包，使用该次构建生成的 SHA-256。

Windows 上运行 `python -B tools/check_release.py <ZIP路径> --scratch <已有临时目录> --codex <已安装Codex可执行文件>`。工具解压下载包，从独立 PowerShell 执行 README 的导出命令，并用转存后的同一份迁移 ZIP 验证预览、合并、恢复和重复导入。报告分别列出包内测试通过和跳过的数量。Windows 上的 Bash 测试使用 Git Bash，缺少时会列为跳过。测试数据均为虚构，Mac 实机验收见双机指南。

`platform_paths.py` 在文件 I/O 边界添加 Windows 长路径前缀。数据库内保存的项目路径、ZIP 条目和用户输出仍使用普通路径。

## 跨版本验证

字段适配、目录策略和事务处理的开源实现对照见[实现复核](docs/implementation-review.md)。

运行 `python -B tools/verify_compatibility.py --codex <本机CLI路径> --scratch <已有临时目录>`，使用 `tests/fixtures/` 中仅含 DDL 和 SQLx 元数据的 62/8、64/9 样本重建虚构数据库。检查各组合并、目标结构保留、重复写入和新关联表。新版 CLI 读回三个 64/9 目标中的正文及项目，包括压缩会话样本，并显式指定映射目录恢复会话。62/8 目标只做数据库验证，避免被新版 CLI 自动升级。

`thread/read` 的 cwd 来自历史会话文件。验收同时检查目标数据库保存的映射目录，以及 `thread/resume` 在传入该目录后返回的实际 cwd。普通桌面打开会话时如何选择目录，需要在对应桌面版本单独确认。

扩展兼容范围时，在 `schema_compat.py` 增加明确的字段与关联规则。未知关联表会阻止迁移。检查表结构时也要覆盖主键、触发器及有数据的源字段，验证失败时源资料和目标资料均未写入。

ChatLuggage 采用 [MIT 许可证](LICENSE)。
