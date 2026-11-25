# Associate imported chats with Mac project directories

[简体中文](project-paths.md) · **English**

Copy your project files to Mac first. For a Git project, you can clone the existing code, then separately copy uncommitted changes and files outside Git. ChatLuggage preserves the chats' project associations. Move the working files separately.

1. Run `plan` once. In the project list, find the project name and its `Project-…` directory ID. A project with multiple roots has a separate ID for each root.
2. Create `project-map.json` beside `cli.py`. Replace the ID and path in this example with your own. Use an existing absolute directory that contains any subdirectories the chats used, such as `src`.

   ```json
   {
     "Project-0123456789abcdef0123": "/Users/example/Code/MyProject"
   }
   ```

3. Run the preview again with the same migration ZIP and the mapping file. Check the project paths in the output. If your ZIP has a different filename or location, replace the quoted path with the one you used in the first preview.

   ```bash
   /bin/bash ./mac.sh plan "$HOME/Downloads/Codex-Windows-History.zip" --map project-map.json
   ```

4. After confirming, copy the full merge command from this preview. It already includes the same `--map` and the current plan ID. The tool records associations and leaves the mapped working directories untouched.

New projects without a mapping get empty placeholders under `windows-history-workspaces` in the Codex profile. Projects previously imported into a path such as `windows-history-workspaces/Project-01/Root-01` are shown in the preview as reusing a legacy placeholder. Chats originally outside their project root keep their project association, and the preview shows their placeholder paths. Already-imported chats with the same ID are kept. Changing associations in bulk after import is currently unsupported.
