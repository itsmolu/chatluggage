# Changelog

[简体中文](CHANGELOG.md) · **English**

## v0.1.0-rc.16

- Share Python and SQLite checks between Windows and Mac, skipping interpreters that cannot safely read Codex history indexes.
- Write and verify the Windows ZIP in its destination directory before publishing it without replacing an existing file. Complete long-path handling for snapshots and packaging in deep directories.
- Verify preview, merge, interruption recovery, and repeat imports with the ZIP produced by the README steps. Report passed and skipped tests separately.
- Fix Windows detection of the installed Codex CLI when building fictional fixtures for Mac. Use the public Mac launcher for the two-computer rehearsal.

## v0.1.0-rc.15

- Review similar projects' schema mapping, directory rewriting, and transactions. Document code comparisons and verification scope.
- Import shared root-chat model IDs when required by child chats. Reuse consistent records within a group and report conflicts during preview.
- Check actual inserted row counts to detect inserts ignored by triggers.
- Add readback of valid Zstandard-compressed sessions through installed Codex, and public CLI export tests with a real 64/9 schema.

## v0.1.0-rc.14

- Check actual tables and fields in export, preview, and merge. Verified combinations are 62/8→62/8, 62/8→64/9, and 64/9→64/9.
- Copy by field name and handle fillable new target fields. Preserve target SQLx records, schema, indexes, and existing data.
- Import `session_model_ids` associated with selected chats. Report an explanation for source fields that cannot be represented, unknown association tables, or trigger changes.
- Keep the migration ZIP format and add real schema fixtures, cross-version tests, and readback checks using installed Codex.

## v0.1.0-rc.13

- Fix SQLite path issues when exporting from a deep Windows extraction directory and long session filenames in the demo.
- Clarify opening Terminal on Mac. Consolidate tests under `tests/` and put user and developer guidance under the README and contributing guide respectively.

## v0.1.0-rc.12

- Preview numbered placeholders created by older migrations on Mac and add new chats to the same project.
- Summarize chats and duplicates by month in `plan`. Select all chats, whole months, or individual chats. Carry the selection into the generated merge command and continue to accept existing migration ZIPs.
- Automatically look for usable Python and SQLite when launching from an older Python. The Mac launcher checks common version managers and multiple interpreters on PATH.

## v0.1.0-rc.11 (Mac Python launcher)

- Add `/bin/bash ./mac.sh` to check installed interpreter and SQLite versions, preferring a suitable bundled Codex runtime. It uses existing software and leaves system Python untouched.
- Report the detected version, interpreter path, and next step when `cli.py` runs with an unsupported Python. Use the new launcher for README preview, project mapping, and recovery.
- Keep the rc.10 migration, backup, deduplication, and recovery core. Verify the new launcher with Git Bash on Windows, with Mac device verification still pending for this launcher. The rc.10 Mac core result still applies to that version.

## v0.1.0-rc.10 (long paths and download flow)

- Record a successful Mac core verification and document Python, extraction of Chinese filenames, and system temporary directories. This documentation update leaves program code intact. Full application quit checks and the regular desktop sidebar remain unverified for this version.
- Fix Windows export failures when deep extraction paths and session filenames with two UUIDs exceeded the path limit. Support extended paths for copying, verification, ZIP writing, and temporary-file cleanup.
- Display Chinese progress, results, and next steps. Errors show the failed step and path. Use `--json` for the full report.
- Print the complete Mac merge command with the plan ID, target, and path map. Respect `CODEX_HOME` for the default profile.
- Add ZIP build and extraction verification with documentation link and import checks. Cover long filenames, missing directories and sessions, existing output, profiles in use, project mapping, and copying commands.

## v0.1.0-rc.9 (Windows export false positives)

- Check whether files in the source profile are actually in use. Ignore similarly named processes unrelated to export. If a file is busy, report its name and stop with the source untouched.

## v0.1.0-rc.8 (Windows export messages)

- Show process names and PIDs if running Codex or ChatGPT instances block export. The usage guide explains how to quit and retry.

## v0.1.0-rc.7 (simpler Windows export)

- Reduce the main Windows flow in the README to entering the project directory and running export. Move path checks into troubleshooting.

## v0.1.0-rc.6 (PowerShell paths)

- Open PowerShell from the Start menu and use `cd` to enter the project directory.

## v0.1.0-rc.5 (download and export guidance)

- Explain how to recognize the extracted project directory and walk through opening PowerShell, checking the directory, and locating the ZIP. The usage guide explains what to do if PowerShell cannot open or `cli.py` cannot be located.

## v0.1.0-rc.4 (Windows export instructions)

- Explain how to start PowerShell in File Explorer or use `cd` from an open PowerShell. Check the path with `Test-Path .\cli.py` before exporting.

## v0.1.0-rc.3 (public documentation)

- Organize the README around real migration, from download through export, transfer, preview, and merge.
- Cover project mapping, trial runs, and interruption recovery in the usage guide. Move development and release verification to the contributing guide. Keep the user-facing ZIP focused on the public migration entry points.

## v0.1.0-rc.2 (download experience candidate)

- Let Windows users launch `Try Demo.cmd` after extraction. It finds Python and creates fictional data and a report in a separate temporary directory.
- Start the README with download, extraction, and the demo, and explain where to run the public migration commands. The migration core matches the previous candidate.

## v0.1.0-rc.1 (local release candidate)

- Export local Windows Codex sessions, text, and project associations into a migration ZIP with a SHA-256 manifest.
- Rehearse on a copy first on Mac. On apply, verify the rehearsal ID, back up affected files, preserve existing chats with the same ID, and support recovery after interruption.
- Map project directories explicitly or use local placeholders. Support SQLx state schema 62 and history schema 8 and preserve the target migration records.
- Pass 15 automated Windows tests and native Codex database integration checks. Verify the Mac core with fictional chats, including in-use file protection, interruption recovery, readback, and repeated runs with no writes.
- Verification of the public CLI's application-wide quit check, desktop sidebar, and GitHub distribution remained pending. [Validation history](docs/validation.en.md)
