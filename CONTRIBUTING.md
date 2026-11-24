# 开发与验证

普通使用从 [README](README.md) 开始。贡献、回归测试或发布验收请使用独立的资料副本和虚构数据，不要把真实会话、登录文件、迁移 ZIP 或备份提交到仓库。

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

## 测试与演练

在含 `cli.py` 的源码根目录运行回归测试。Mac 上请使用[已核对的 Python 解释器](docs/usage.md#检查-python)。

```powershell
python -B -m unittest -v
```

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
