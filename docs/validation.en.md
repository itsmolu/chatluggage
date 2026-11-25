# Validation history

[简体中文](validation.md) · **English**

## rc.16 download ZIP verification

The Windows release check runs the README's PowerShell export command from an extracted source ZIP. It uses the transferred migration ZIP to check preview, project paths, chat text, recovery from a controlled interruption, and repeat imports. Packaged tests cover switching interpreters when SQLite is too old, forced interruption during ZIP writing, a destination file appearing during publication, and exports from deep directories.

The release check JSON lists `packaged_tests`, `packaged_tests_skipped`, and cross-version readback results. The 62/8 target is verified at the database level. The three 64/9 targets are also checked through the local Codex interface. The Mac application quit guard, regular desktop sidebar, and default working directory remain to be checked on a Mac.

## rc.15 implementation review

After extraction from the source ZIP, the revised build passed 50 tests, the README's Windows commands, transfer verification, installed schema checks, and four merge combinations. Three 64/9 targets were checked through the local Codex interface.

See the [implementation review](implementation-review.en.md) for the source comparison. This version added checks for importing a child chat on its own when it shares model IDs, subsequent imports from the same group, conflicts, and a missing source root chat. It also added regression coverage for triggers that ignore inserts and public CLI export from a real 64/9 schema with compressed sessions.

Readback using installed Codex covered the fourth, compressed 64/9→64/9 case. Codex decoded a valid Zstandard raw-block frame. The session remained compressed during transfer, and full turns and text could be read back. Core cross-version validation still covers the three combinations below.

## rc.14 cross-version compatibility

Verified on Windows on 2026-11-22 with Python 3.13.5, SQLite 3.49.1, and Codex CLI 0.171.0-alpha.13. All test data came from fictional chats. The schemas came from two samples containing only table definitions and SQLx metadata.

| Source → target | Merge, chat text, and projects | Target schema and SQLx records | Repeat preview and merge | Local Codex readback |
| --- | --- | --- | --- | --- |
| 62/8 → 62/8 | Passed | Preserved | Zero writes | Database checks only |
| 62/8 → 64/9 | Passed | Preserved | Zero writes | Chat text, turns, and project association passed |
| 64/9 → 64/9 | Passed, including selected chats' session_model_ids | Preserved | Zero writes | Chat text, turns, and project association passed |

The built ZIP was extracted into a directory with Chinese characters and spaces. The README's Windows export command, transfer SHA-256, 46 tests, and installed schema and cross-version readback checks passed. A 62/8 ZIP exported by rc.13 also added one chat to a 64/9 target using rc.14, with zero writes on the next preview.

Additional regression cases cover new ordinary indexes, version number increases, new target columns with defaults, reordered columns, partial chat selection, missing target columns containing source data, required fields without defaults, unknown association tables, trigger changes, and interruption recovery. The test data also checks preservation of existing and unselected chats.

Local Codex `thread/read` returns the original cwd from the historical session file. Verification checks the mapped path in the target database separately and passes it explicitly to `thread/resume` to check the actual working directory. The regular desktop app's choice of directory remains to be checked on Mac.

rc.14 has no Mac field report. The Windows core and local interface checks leave the Mac application quit guard and regular desktop sidebar for platform-specific verification.

## Earlier versions

- The rc.13 Windows download package passed 38 tests. New 64/9 data from the installed Codex version was rejected by a fixed version check. rc.14 replaced that check with schema validation.
- On 2026-11-22 a user reported completing the full rc.12 Mac migration workflow.
- rc.10 verified SHA-256, backups, recovery from a controlled interruption, files in use, and chat text and project readback in an isolated Mac profile. The environment was Python 3.12.14, SQLite 3.53.1, Codex CLI 0.169.0-alpha.11.1, and desktop version 26.1111.21308.

## How to reproduce

- Run the regression tests, the download ZIP checks, or `tools/verify_compatibility.py` from the [contributing guide](../CONTRIBUTING.en.md).
- Use one fictional chat on Windows and one on Mac to exercise export, preview, merge, and readback. [Two-computer rehearsal](full-rehearsal.en.md)
- To run the Mac core verification on its own, run the following commands in the Windows project directory. The script looks for the installed Codex CLI. Use `--codex` to specify its path if needed. Write the checksum only after the fixture ZIP is generated.

  ```powershell
  New-Item -ItemType Directory -Force ..\verification\mac-test
  python -B tools/accept_mac.py --build-fixture ..\verification\mac-test\windows-fictional.zip
  (Get-FileHash -LiteralPath ..\verification\mac-test\windows-fictional.zip -Algorithm SHA256).Hash.ToLowerInvariant() | Set-Content -LiteralPath ..\verification\mac-test\windows-fictional.sha256 -Encoding ascii
  ```

  Copy the `mac-test/` folder outside the Mac repository and pass its path when following [automated Mac acceptance](mac-acceptance.en.md).
