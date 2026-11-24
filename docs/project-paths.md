# 将迁入会话关联到 Mac 项目目录

先把项目文件复制到 Mac。对使用 Git 的项目，可以在 Mac 克隆已有代码，再另外复制未提交的改动和不在 Git 里的文件。工具保留聊天的项目归属，不负责搬工作文件。

1. 先运行一次 `plan`，在输出的项目列表中找到项目名称及对应的 `Project-…` 目录代号。同一个项目有多个根目录时，每个根目录有自己的代号。
2. 在 `cli.py` 同目录建立 `project-map.json`。将示例中的代号和路径换成实际值。路径必须是已有的绝对目录，其中也应有会话使用到的子目录，例如 `src`。

   ```json
   {
     "Project-0123456789abcdef0123": "/Users/example/Code/MyProject"
   }
   ```

3. 用同一份迁移 ZIP 加上映射重新预览，核对输出中的项目路径。文件名或存放位置与示例不同时，用之前预览的实际 ZIP 路径替换引号内的路径。

   ```bash
   /bin/bash ./mac.sh plan "$HOME/Downloads/Codex-Windows-History.zip" --map project-map.json
   ```

4. 确认后复制这次预览输出的完整合并命令。命令已带上同一个 `--map` 和本次预览编号。工具只建立关联，不会向映射到的工作目录写文件。

未映射的新项目路径会落到 Codex 资料目录的 `windows-history-workspaces` 下，里面是空的占位目录。已经用 `windows-history-workspaces/Project-01/Root-01` 这类路径迁入的项目会在预览时标为“沿用旧版占位目录”。会话原来不在项目根目录下时，仍保留项目归属，并可在预览报告中看到它的占位路径。已导入的同 ID 会话不会被覆盖；目前不提供导入后批量更换关联目录。
