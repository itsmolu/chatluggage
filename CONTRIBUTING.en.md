# Contributing to ChatLuggage

[简体中文](CONTRIBUTING.md) · **English**

Welcome to ChatLuggage. Fixing a migration issue, adding a test, or clarifying a step can help the next reader. To migrate your own chats, start with the [README](README.en.md).

[Report an issue](#report-an-issue) · [Submit a change](#submit-a-change) · [Tests and rehearsals](#tests-and-rehearsals)

## Ways to contribute

| What you want to do | Where to start |
| --- | --- |
| Report a problem with export, preview, merge, or recovery | Open an issue with your environment and the steps to reproduce it. |
| Fix a guide, command example, or link | Open a pull request. If you changed a command, include the platform where you verified it. |
| Cover an edge case | Reproduce it with fictional data in `tests/`. Database schema samples belong in `tests/fixtures/`. |
| Change the migration ZIP format, path mapping, or write process | Open an issue to discuss the expected behavior and the effect on older ZIPs and existing chats. |

## Report an issue

Check for an existing report first. When you open an issue, include the following details. [GitHub Issues](https://github.com/itsmolu/chatluggage/issues)

1. **Environment** Include your Windows or macOS version, Codex version, ChatLuggage version, and the Python and SQLite versions actually in use.
2. **Steps to reproduce** Include the command or step where the problem happened, what you expected, and what happened.
3. **Error information** Attach the relevant error and a sanitized command. A minimal example with fictional data can make the problem easier to reproduce.

Use fictional chats for tests and sanitized error output in public discussions. Keep real chats, migration ZIPs, Codex databases, authentication files, and backups on your own machine.

## Submit a change

1. Select **Fork** on GitHub. Replace `YOUR-ACCOUNT` with your GitHub username, then clone your fork and create a branch.

   ```bash
   git clone https://github.com/YOUR-ACCOUNT/chatluggage.git
   cd chatluggage
   git switch -c docs/clarify-preview
   ```

2. Make your changes on the branch and run the applicable [tests and rehearsals](#tests-and-rehearsals). For documentation changes, follow the relevant steps and check both language versions, their commands, and their links.
3. Review your changes before committing. This example updates both versions of the usage guide.

   ```bash
   git status --short
   git add docs/usage.md docs/usage.en.md
   git diff --cached
   ```

4. After reviewing the staged changes, commit and push to your fork.

   ```bash
   git commit -m "Clarify the Mac preview instructions"
   git push -u origin docs/clarify-preview
   ```

5. Open a pull request on GitHub with `itsmolu/chatluggage`'s `main` as the base and the branch you pushed as the head. You can use this format.

   ```markdown
   ## What changed and why
   Explain the problem and which commands or docs are affected. Link any related issue.

   ## Verification
   List the platform, commands, and results. Include steps using fictional data if applicable.

   ## Compatibility
   Describe the impact on existing migration ZIPs, databases, command options, or project paths.
   ```

## Code entry points and conventions

| File | Responsibility |
| --- | --- |
| `mac.sh` | Selects a suitable installed Python runtime and passes arguments to the CLI |
| `cli.py` | Handles the `export`, `plan`, `apply`, and `recover` commands |
| `chat_selection.py` | Groups chats by month, lists them, and collects selections |
| `windows_export.py` | Validates and selects the Windows profile data and writes the migration ZIP |
| `schema_compat.py` | Compares actual schema fields, copies selected rows, and checks new associations |
| `history_migration.py` | Validates ZIP and SQLite data and builds the merge and project path mapping |
| `migration_workflow.py` | Checks for files in use and handles the plan ID, backup, writes, and recovery |
| `platform_paths.py` | Handles Windows long paths and temporary directories |
| `demo.py` | Runs the local demo with fictional data |
| `tests/` | Contains regression tests and fictional fixtures |
| `tools/` | Builds and checks the source ZIP and verifies migration with installed Codex |
| `docs/` | Contains usage, troubleshooting, path mapping, and contributor verification guides |

When changing migration logic, check preview, compatibility with older ZIPs, and recovery through the public entry points. If a command or file moves, check the README, import paths, and the allowlist in `tools/build_release.py`.

Keep the Chinese `.md` and English `.en.md` pages in sync when steps or behavior change. Each language switch should open the matching page.

## Tests and rehearsals

Run the commands below from the source root containing `cli.py`. Use Python 3.11 or later with SQLite 3.41.0 or later. On Mac, use a suitable `python3` or the interpreter's full path in place of `python`. [Check Python](docs/usage.en.md#check-python)

```powershell
python -B -c "import sys, sqlite3; print(sys.version.split()[0], sqlite3.sqlite_version)"
python -B -m unittest -v
```

The tests use fictional data and some are platform-specific. Report the platforms tested and any passes or skips in your PR. If the change affects platform-specific entry points, include the steps you actually ran.

On Windows, `python -B tools/verify_installed_schema.py` uses an isolated Codex CLI profile to generate native table definitions and test migration with fictional chats. `python -B cli.py demo` runs a one-computer demo with fictional data. Its archive uses a separate demo format.

Before a release, see the [two-computer rehearsal](docs/full-rehearsal.en.md), [automated Mac acceptance](docs/mac-acceptance.en.md), and [validation history](docs/validation.en.md).

## Build and verify the download ZIP

Run `python -B tools/build_release.py <new-ZIP-path>`. It packages allowlisted source and documentation, checks imports, documentation links, and ZIP integrity, and writes a `.zip.sha256` file. The allowlist excludes chats, backups, and authentication files.

Keep the release ZIP and its checksum in `release/` beside the repository. Keep validation reports and fictional migration ZIPs in `verification/` beside it. After source or documentation changes, rebuild and recheck the ZIP and distribute the checksum from that build.

On Windows, run `python -B tools/check_release.py <ZIP-path> --scratch <existing-temp-directory> --codex <installed-Codex-executable>`. It extracts the download ZIP, executes the README export command in a separate PowerShell, and copies that migration ZIP for preview, merge, recovery, and repeat-import checks. The report lists packaged-test passes and skips separately. Bash tests use Git Bash on Windows and are skipped if it is unavailable. All test data is fictional. The two-computer guide covers verification on a Mac.

`platform_paths.py` applies the Windows long-path prefix at file I/O boundaries. Project paths in SQLite, ZIP entries, and user-facing output keep their ordinary form.

## Cross-version verification

See the [implementation review](docs/implementation-review.en.md) for comparisons of schema mapping, directory handling, and transactions with other open-source implementations.

Run `python -B tools/verify_compatibility.py --codex <local-CLI-path> --scratch <existing-temp-directory>`. It builds fictional databases from the 62/8 and 64/9 fixtures in `tests/fixtures/`, which contain DDL and SQLx metadata. It checks each merge combination, target schema preservation, repeat writes, and new association tables. The newer Codex CLI reads chat text and project associations from three 64/9 targets, including a compressed session, and resumes chats with explicit mapped directories. The 62/8 target is checked at the database level to avoid automatic upgrades by the newer CLI.

`thread/read` gets its cwd from the historical session file. Verification checks both the mapped directory stored in the target database and the cwd returned when that directory is passed to `thread/resume`. Directory selection in the regular desktop app needs separate verification for each relevant desktop version.

When extending compatibility, add explicit field and association rules in `schema_compat.py`. Unknown association tables stop the migration. Cover primary keys, triggers, and source fields containing data when checking schemas. If validation fails, neither profile should receive writes.

ChatLuggage is available under the [MIT license](LICENSE).
