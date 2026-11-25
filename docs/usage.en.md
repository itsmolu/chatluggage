# Usage guide

[简体中文](usage.md) · **English**

## Check Python

On Mac, run `/bin/bash ./mac.sh` as shown in the README. It searches the installed Codex runtime, common installation locations, version managers, and PATH. Preview and merge need Python 3.11 or later with SQLite 3.41.0 or later and support for the JSON expression indexes used by Codex. Running `cli.py` with a working older `python3` also transfers control to this launcher. If the system `python3` prompts you to install anything, use the Bash command in the README.

On Windows, if `python` starts but its Python or SQLite version is too old, the CLI searches registered interpreters, user installation directories, and PATH. If PowerShell cannot find `python` but the Python Launcher is installed, use `py -3 -B .\cli.py export .\Codex-Windows-History.zip`. Both platforms use installed interpreters. On Mac, `CODEX_MIGRATION_PYTHON` can point to an interpreter by its full path. If no suitable interpreter is found, check the Python paths and SQLite versions shown.

## Troubleshoot ZIP extraction on Mac

Double-click the project ZIP in Finder and open the extracted folder that contains both `mac.sh` and `cli.py`. Keep the chat migration ZIP compressed and pass it directly to `plan`. If extraction of the project ZIP stops, download a complete copy again or use an installed Python's `zipfile` module to extract it to a new folder.

## A path or file cannot be found

If PowerShell reports a missing path or cannot find `cli.py` during export, paste this command into **PowerShell** and press Enter.

```powershell
Test-Path .\cli.py
```

- If it shows `True`, the folder contains `cli.py` and you can continue [exporting](../README.en.md#2-export-chats-on-windows).
- If it shows `False`, open the folder containing both `README` and `cli` in File Explorer, and copy the full path from its address bar. Paste that path between the double quotes in the PowerShell `cd` command and press Enter.

In Mac Terminal, you can use `ls cli.py` to check the current folder. If the file is missing, drag the correct project folder into Terminal as shown in [README step 3](../README.en.md#3-preview-on-mac).

If the program starts and then reports a missing file, the output identifies the step and path that failed.

- **Missing source profile directory** Check the printed source path. Use `--source` for a custom location.
- **Missing session file** Verify the `sessions` and `archived_sessions` folders in the source are still in place. The tool stops if a session is missing its text.
- **Deep extraction or output directory** Older versions could fail to access temporary copies under Windows long-path limits. rc.16 fixes deep-directory snapshots and packaging. Try again with a complete, freshly extracted copy of the newer version.

## The output ZIP already exists

Export preserves existing files. To export again, choose a new destination name, such as `Codex-Windows-History-2.zip`. Use that same filename in your Mac preview command after copying it over.

If export is interrupted, wait for the previous process to exit and retry with the same output name. Files starting with `.codex-export-` and ending with `.partial` are unfinished temporary archives. Copy the ZIP identified by the terminal's success message.

## Export reports a file in use

`Windows source file is in use` identifies the file that cannot be read safely, such as `state_5.sqlite`. No ZIP was created. Save your work, quit the app or CLI using that profile, and retry in the original PowerShell window. If Codex is already closed, check whether a database viewer has the profile open or a sync tool is using the folder. The exporter leaves processes running.

For older messages such as `Quit the Windows Codex app and CLI` or `Windows Codex is still running`, use rc.10 or later. The newer check looks at whether source files are in use.

## Choose a profile directory and include assets

- Windows `export` uses `CODEX_HOME` by default, falling back to `%USERPROFILE%\.codex`. For another directory, append `--source "D:\Path\To\Profile"`. The output ZIP must be outside the source directory and its path must be unused.
- Mac `plan`, `apply`, and `recover` use `CODEX_HOME` by default, falling back to `~/.codex`. The preview prints the actual target path. With a custom profile or a copy of the Mac profile, add `--target "/path/to/profile"` to `plan`. The generated merge command keeps that argument. Both `state_5.sqlite` and `thread_history_1.sqlite` must be present.
- Add `--include-assets` to the Windows export to include supported attachments, plans, and generated images. Historical Windows paths in chat text stay as written. External attachments need to be handled separately.

A partial import carries only the data and session files associated with the selected chats. Standalone assets that cannot be attributed to a particular chat are handled only when all chats are selected.

## Try it on a separate copy

After quitting Codex completely on Mac, create an empty folder such as `Codex-History-Test` in Downloads. Copy the following from the Mac profile into that folder.

- `state_5.sqlite`, `thread_history_1.sqlite`, and their existing `-wal`, `-shm`, or `-journal` files.
- `session_index.jsonl` and `.codex-global-state.json`, if they exist. In Finder, press **⌘⇧.** to show hidden files.

For a merge check, original session files, sign-in files, and app configuration can stay in the live profile. Your usual Codex windows do not read the test copy. Add `--target "$HOME/Downloads/Codex-History-Test"` to `plan` and use the merge command it prints. When you move to your everyday profile, rerun `plan` with the correct target and use the new command.

## Verify the migration ZIP and compatibility

In Mac Terminal, type `shasum -a 256` followed by a space. Drag the actual migration ZIP into the window and press Enter. If it has its original name and is in Downloads, run `shasum -a 256 "$HOME/Downloads/Codex-Windows-History.zip"`. Compare it with the `sha256` from the corresponding Windows export. If they differ, check the filename and location and copy the ZIP again.

The tool checks tables, fields, primary keys, association tables, and triggers needed for migration. It merges into the existing Mac schema by field name. Verified combinations include 62/8→62/8, 62/8→64/9, and 64/9→64/9. Additional ordinary indexes or fillable fields can pass validation, and higher version numbers can still be previewed. The tool stops with an explanation if it finds a new chat association table, a trigger change, a missing target field with source data, or a required field it cannot populate.

When importing a newer ZIP into an older target, the target must support all selected data. For example, if the ZIP has `session_model_ids` records and the target lacks that table, the preview identifies the missing table and stops. Changing the database version number alone cannot resolve this.

Export sets the imported chats' recorded sandbox mode to read-only and their recorded approval policy to never, and removes machine-specific Windows project metadata. When opening a chat on Mac, check the permissions and working directory shown by your current Codex client.

`source_checksums_match: false` indicates a difference in SQLx migration-record checksums. If schema validation passes, the merge preserves the target table definitions and migration records. The migration ZIP's SHA-256 separately verifies file transfer.

Export only covers sessions indexed in the Windows state database. The result reports excluded files. Running `plan` again after merging retains existing sessions with the same ID. If there is nothing to write, it shows **无需合并** (nothing to merge).

In an interactive terminal, `plan` shows a selection menu by default. Scripts can use `--all` or `--pick 1,3-5`. The archive session start time determines number order, newest first, with ties sorted by session ID. With `--json` or non-interactive input, all chats are selected by default. The plan ID binds the ZIP, target, and selection. A partial import does not bring along an unselected parent chat. Select both chats to preserve that relationship.

Append `--json` to any command for the full JSON report. `source_checksums_match` compares database migration records, `unindexed_session_files` counts excluded session files, and `files_to_change` counts files to be written.

## Recover after an interrupted merge

Check the terminal output after a merge error. If no target files were written, investigate and run the preview again. If the tool reports an unfinished installation, keep the app closed and run this command from the project directory.

```bash
/bin/bash ./mac.sh recover
```

If you used a custom `--target`, pass the same path to recovery. The tool compares the backup with the current files. If another process has changed the data, it stops and keeps the backup. Run `plan` after successful recovery. `recover` restores interrupted installations only.
