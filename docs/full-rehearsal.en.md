# Contributor guide for a two-computer rehearsal

[简体中文](full-rehearsal.md) · **English**

Before a release, use this guide to check the public `export`, `plan`, and `apply` commands on both computers. The setup script creates one fictional Windows chat and one fictional Mac chat inside the extracted project directories. Subsequent commands explicitly point to the test profiles. The rehearsal checks chat text, project associations, backups, and repeat imports. To migrate real chats, start with the [README](../README.en.md).

## 1. Download the same source ZIP

On GitHub, select **Code → Download ZIP** on the project homepage, or get the source ZIP for a specific version from **Releases**. Before a release, you can use the candidate source ZIP.

Extract a copy on Windows and another on Mac. Open the directory containing `cli.py` on each computer. You can get the source ZIP on Mac at any time. After exporting on Windows, only the migration ZIP needs to be transferred. A USB drive or another convenient method works. This migration ZIP contains fictional chats only.

Both computers need Codex, Python 3.11 or later, and SQLite 3.41.0 or later as loaded by that interpreter. Mac migration commands use the README's `mac.sh` entry point. The rehearsal uses test profiles and local interfaces, so you do not need a new sign-in window.

## 2. Windows setup and export

Open PowerShell from the Windows Start menu. Use `cd "full path to the project folder"` to enter the extracted directory, then run these commands.

```powershell
Test-Path .\cli.py
python -B .\tools\full_flow_fixture.py prepare
```

The first command should print `True`, and the second should report `"status": "ready"`. Quit ChatGPT/Codex from the Windows system tray and close any other Codex windows and CLI sessions. Keep PowerShell open and run this command.

```powershell
python -B .\cli.py export .\Rehearsal-Windows-History.zip --source .\migration-rehearsal\profile --json
```

Confirm `"threads": 1`. Copy `Rehearsal-Windows-History.zip` to the extracted project directory on Mac, next to `cli.py`. Keep the `sha256` from the export output to verify the copy on Mac.

## 3. Mac setup and preview

Open macOS Terminal. Type `cd` followed by a space, drag the folder containing `cli.py` from Finder into Terminal, and press Enter. Run these checks.

```bash
ls cli.py Rehearsal-Windows-History.zip
PY=python3
"$PY" -B -c 'import sys, sqlite3; print(sys.version.split()[0], sqlite3.sqlite_version)'
```

If the default `python3` does not meet the requirements, set `PY` to the full path of an installed interpreter. Repeat the check for Python 3.11 or later and SQLite 3.41.0 or later. This interpreter prepares and checks the test data. The public migration commands find a suitable interpreter automatically.

Prepare a fictional Mac chat from the same Terminal window.

```bash
"$PY" -B tools/full_flow_fixture.py prepare
```

When you see `"status": "ready"`, press **⌘Q** to fully quit your regular ChatGPT/Codex app and close other running Codex CLI sessions. Keep the standalone Terminal open and run these commands.

```bash
shasum -a 256 Rehearsal-Windows-History.zip
/bin/bash ./mac.sh plan Rehearsal-Windows-History.zip --target ./migration-rehearsal/profile --json
```

The SHA-256 should match the Windows export report. The preview should show `"local_threads": 1`, `"added_threads": 1`, and `"total_threads": 2`, and list a placeholder path for a test project. The fictional project uses the default placeholder because it has no code. Copy the `plan_id` from the report without its surrounding quotes. If both schemas pass validation, `source_checksums_match: false` can be expected. The schema checksum and the archive SHA-256 check different things.

## 4. Confirm, merge, and verify on Mac

Keep Codex closed. Replace `REPLACE_WITH_PLAN_ID` below with the value you copied. Keep the `--target` argument.

```bash
/bin/bash ./mac.sh apply Rehearsal-Windows-History.zip --target ./migration-rehearsal/profile --approve 'REPLACE_WITH_PLAN_ID' --json
```

The output should include `"target_changed": true`, `"total_threads": 2`, and a `backup` path. Run these commands to check that another preview has no duplicate writes and let the local Codex installation read both fictional chats from the test profile.

```bash
/bin/bash ./mac.sh plan Rehearsal-Windows-History.zip --target ./migration-rehearsal/profile --json
"$PY" -B tools/full_flow_fixture.py verify
```

The first command should report `"files_to_change": 0`. The second should report `"status": "passed"`. It checks that the original session file stayed intact, the backup matches, and chat text and project associations can be read back. The rehearsal uses local interfaces, and visual inspection of the desktop sidebar is outside its scope. You can reopen the Codex app when done.

If a merge is interrupted and the tool requests recovery, keep the app closed and run `/bin/bash ./mac.sh recover --target ./migration-rehearsal/profile`. Run `plan` again after recovery. To start over, use a freshly extracted copy of the project.
