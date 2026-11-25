# Implementation review of schema compatibility

[简体中文](implementation-review.md) · **English**

This review compared pinned source revisions with ChatLuggage's behavior and reproduced edge cases with fictional data. The projects cover different migration scopes, so interpret verification results in the context of each implementation.

## Implementations reviewed

| Implementation | What we checked | How ChatLuggage handles it |
| --- | --- | --- |
| Codex Migrate | Uses PRAGMA to check required fields and builds writes by field name | Maps by name and also checks primary keys, types, missing fields that contain source data, and the actual number of inserts. [Source](https://github.com/ChenglongLi777/codex-migrate/blob/37f5300c15447921baf3b98d8ef09d07a3a60008/src/sqlite_adapter.rs) |
| Codex Migrate | Rewrites cwd in session_meta and turn_context and registers sessions with the app server | Preserves session bytes and historical offsets, and records the mapped directory in the database. Resume with an explicit mapped directory was verified. Default directory selection in the regular desktop app remains to be checked. [Source](https://github.com/ChenglongLi777/codex-migrate/blob/37f5300c15447921baf3b98d8ef09d07a3a60008/src/rollout.rs) |
| Codex Migrate | Records created files, backups, and interruption rollbacks | Reviewed existing backups and durable recovery records, and added cross-version interruption recovery coverage. [Source](https://github.com/ChenglongLi777/codex-migrate/blob/37f5300c15447921baf3b98d8ef09d07a3a60008/src/transaction.rs) |
| CC Switch | Inspects actual fields, SQLite backup, and compressed session handling | Continues to handle WAL on stable copies and now verifies local Codex decompression and text readback for valid Zstandard sessions. [Source](https://github.com/farion1231/cc-switch/blob/768eec9ca94db629f086e8c01ca91e2f63bb273d/src-tauri/src/codex_history_migration.rs) |

## Findings and fixes

- `session_model_ids` are scoped to the root chat, and the root and worker chats share those IDs. Filtering by imported chat ID alone omitted shared data. The tool now follows `attached_thread_parent_id` and `thread_spawn_edges` from selected chats and includes the necessary namespaces. Child chats reuse consistent records, and conflicts stop writes. [Codex scope implementation](https://github.com/openai/codex/blob/cf3ff1530d821ec2d26452da4971d8e3e7d18d25/codex-rs/state/src/runtime/model_ids.rs)
- SQLite triggers can ignore an insert. The tool now checks the actual row count so a missing insert cannot be reported as success.
- The earlier compressed-file test checked only byte copying. The current test generates a valid Zstandard frame, has the local Codex installation decompress it, and checks full turns, chat text, and project associations.
- Public Windows CLI export now has coverage with a real 64/9 schema and compressed sessions, including default source directory selection and JSON reporting.

## Verification scope

Windows ZIP tests and installed Codex interface verification use fictional data. The regular Mac desktop sidebar, default resume directory, and application-wide quit guard still need platform integration verification. The current profile layout requires both databases at the root of the specified profile directory. A separate `sqlite_home` layout needs additional support. Historical chat text and old paths inside the original session files are preserved.

See the [validation history](validation.en.md) and [contributing guide](../CONTRIBUTING.en.md) for commands and results by version.
