# 贡献者指南：Mac 自动验收

用于在 macOS 上用虚构数据验收迁移核心。把 `windows-fictional.zip` 和 `windows-fictional.sha256` 放在仓库外的同一个文件夹。交接包在 `verification/mac-test/` 里已备好这两个文件。也可以按[复现说明](validation.md#如何复现)在 Windows 生成一份。验收器使用系统临时目录中的隔离资料。

在含 `cli.py` 的项目根目录使用 Python 3.11+ 和该解释器加载的 SQLite 3.41.0+。将 `--fixture-dir` 的值设为虚构资料所在的实际目录。使用交接包中的资料时，命令如下。

```bash
python3 -B tools/accept_mac.py --fixture-dir ../verification/mac-test
```

脚本默认在安装的 ChatGPT/Codex 应用包里查找 CLI；必要时可用 `--codex` 指定本机可执行文件。运行前会自动比较虚构迁移包的 SHA-256。

最后一行 JSON 是验收结果。`status: passed` 需要同时满足：本地数据库兼容、演练没写目标、模拟中断后能恢复、两条虚构聊天能由本机 Codex 读回、项目和路径正确、重复运行没有待写入项，且 macOS 文件占用检查能识别故意打开的测试文件。`application_process_guard` 和 `desktop_sidebar` 标为 `not_run` 是预期结果。

## 环境与失败排查

- 系统 Python 无法启动时，使用本机已有且加载 SQLite 3.41.0+ 的 Python 3.11+。完整路径需实际核对，不固定用户名或安装位置。
- 系统 `unzip` 处理中文文件名失败时，见[Mac 解压说明](usage.md#mac-解压遇到问题)。解压成功后再运行验收。
- 脚本要求测试目录位于 macOS 系统临时区。不要把验收进程的 `TMPDIR` 指向项目工作目录；外层设置可能让 `getconf DARWIN_USER_TEMP_DIR` 返回不受支持的位置并在 `preflight` 停止。先记录原设置，再在独立验收进程使用系统临时区；不要移除脚本的目录保护。

rc.10 已收到一次 Mac 实机通过报告，源码未作修改。环境故障及验证边界见[本次 Mac 证据](validation.md#早期版本)。这份验收不会退出当前 Codex，也不检查普通桌面侧栏；重复导入只重新预览，确认待写文件、待建目录、新增会话均为 0，不再实际执行第二次 `apply`。
