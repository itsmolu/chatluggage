# 字段适配方案的实现复核

**简体中文** · [English](implementation-review.en.md)

本次对照固定提交的源码与本项目的行为，用虚构数据复现发现的边界。各项目的迁移范围不同，测试结果按各自实现解释。

## 对照的实现

| 实现 | 核对的位置 | 对本项目的处理 |
| --- | --- | --- |
| Codex Migrate | 用 PRAGMA 检查必需字段，按名称构建写入语句 | 保留字段名称映射，并额外检查主键、类型、缺失字段中是否含有数据和实际插入数量。[源码](https://github.com/ChenglongLi777/codex-migrate/blob/37f5300c15447921baf3b98d8ef09d07a3a60008/src/sqlite_adapter.rs) |
| Codex Migrate | 改写 session_meta、turn_context 的 cwd，并登记到 App Server | 本项目保留原会话字节和历史偏移，数据库记录映射路径。恢复接口在显式给出映射目录时已验证，普通桌面默认目录选择待实机确认。[源码](https://github.com/ChenglongLi777/codex-migrate/blob/37f5300c15447921baf3b98d8ef09d07a3a60008/src/rollout.rs) |
| Codex Migrate | 记录新建文件、备份和中断回退 | 复核现有备份与持久化恢复日志，新增跨版本中断恢复回归。[源码](https://github.com/ChenglongLi777/codex-migrate/blob/37f5300c15447921baf3b98d8ef09d07a3a60008/src/transaction.rs) |
| CC Switch | 检查实际字段、SQLite 备份，以及压缩会话的处理 | 本项目继续在稳定副本处理 WAL，增加有效 Zstandard 会话的本机 Codex 解压、正文读回验证。[源码](https://github.com/farion1231/cc-switch/blob/768eec9ca94db629f086e8c01ca91e2f63bb273d/src-tauri/src/codex_history_migration.rs) |

## 发现并修复的问题

- session_model_ids 以会话根为作用域，主会话和工作子会话共享标识。仅按新导入的聊天 ID 筛选会漏掉共享数据。现在从选中聊天追溯 attached_thread_parent_id 和 thread_spawn_edges，将必要命名空间一并处理。多个子会话复用一致记录，冲突会阻止写入。[Codex 的作用域实现](https://github.com/openai/codex/blob/cf3ff1530d821ec2d26452da4971d8e3e7d18d25/codex-rs/state/src/runtime/model_ids.rs)
- SQLite 触发器可以忽略插入。现在核对实际插入行数，避免将缺少记录的操作报告为成功。
- 原压缩文件测试只验证字节复制。现在生成有效 Zstandard 帧，由本机 Codex 实际解压，并验证完整轮次、正文和项目归属。
- 公共 Windows CLI 增加真实 64/9 结构与压缩会话的导出测试，覆盖默认源目录选择和 JSON 报告。

## 验证边界

Windows 下载包测试和装机版本地接口验证使用虚构数据。Mac 普通桌面侧栏、默认恢复目录和全应用退出保护仍需要对应平台的集成验证。资料布局当前要求两份数据库位于所指定的资料目录根部，分离的 sqlite_home 布局需要额外适配。历史正文和原始会话文件中的旧路径会被保留。

复现命令和各版本的结果见[验证记录](validation.md)与[贡献指南](../CONTRIBUTING.md)。
