# Contributor guide for automated Mac acceptance

[简体中文](mac-acceptance.md) · **English**

Use fictional data to verify the migration core on macOS. Put `windows-fictional.zip` and `windows-fictional.sha256` together in a folder outside the repository. The handoff archive provides both under `verification/mac-test/`. You can also [build the fixture on Windows](validation.en.md#how-to-reproduce). The check uses an isolated profile in the system temporary directory.

From the project root containing `cli.py`, use Python 3.11 or later with SQLite 3.41.0 or later. Set `--fixture-dir` to the actual directory with the fictional data. For the handoff fixture, run this command.

```bash
python3 -B tools/accept_mac.py --fixture-dir ../verification/mac-test
```

The script looks for the CLI inside the installed ChatGPT/Codex app bundle. You can specify a local executable with `--codex`. The script compares the fictional ZIP's SHA-256 before proceeding.

The last JSON line is the result. To report `status: passed`, the script checks schema compatibility, verifies that preview leaves the target untouched, restores a simulated interruption, reads both fictional chats back through local Codex with the expected projects and paths, confirms that a repeat run has no changes, and detects an intentionally held-open test file. It is expected to report `application_process_guard` and `desktop_sidebar` as `not_run`.

## Environment and troubleshooting

- If the system Python cannot start, use an installed Python 3.11 or later with SQLite 3.41.0 or later. Check the interpreter's actual full path on your machine.
- If system `unzip` cannot extract filenames containing Chinese characters, see [ZIP extraction on Mac](usage.en.md#troubleshoot-zip-extraction-on-mac). Complete extraction before running the check.
- Use the macOS system temporary area for this test. Pointing its process `TMPDIR` at the project can make `getconf DARWIN_USER_TEMP_DIR` report an unsupported location and stop at `preflight`. Record the original setting, then use the system temporary area for a separate verification process and keep the script's directory guard in place.

rc.10 received one report of a successful run on a Mac with the same source. See [earlier verification history](validation.en.md#earlier-versions) for the environment and verification scope. The check leaves the regular Codex app running and does not inspect its sidebar. For repeat imports it runs another preview to confirm zero pending files, directories, and new sessions.
