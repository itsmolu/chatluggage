"""Build an allowlisted source ZIP and validate its documentation and imports."""

import argparse
import ast
import hashlib
from pathlib import Path
import re
import sys
from urllib.parse import unquote
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cli import VERSION

PACKAGE_ROOT = 'codex-history-migration'
FILES = (
    'README.md', 'README.en.md', 'CHANGELOG.md', 'CHANGELOG.en.md',
    'CONTRIBUTING.md', 'CONTRIBUTING.en.md', 'LICENSE', '.gitignore', '.gitattributes',
    'cli.py', 'chat_selection.py', 'mac.sh', 'demo.py', 'windows_export.py', 'history_migration.py', 'migration_workflow.py', 'platform_paths.py',
    'schema_compat.py',
    'tests/__init__.py', 'tests/test_migration.py', 'tests/test_native.py', 'tests/test_user_flow.py',
    'tests/test_python_entry.py', 'tests/test_selection.py',
    'tests/test_compatibility.py', 'tests/fixtures/schema-62-8.json', 'tests/fixtures/schema-64-9.json',
    'tools/build_release.py', 'tools/check_release.py', 'tools/verify_installed_schema.py',
    'tools/accept_mac.py', 'tools/full_flow_fixture.py', 'tools/verify_compatibility.py',
    'docs/usage.md', 'docs/usage.en.md', 'docs/project-paths.md', 'docs/project-paths.en.md',
    'docs/validation.md', 'docs/validation.en.md',
    'docs/mac-acceptance.md', 'docs/mac-acceptance.en.md',
    'docs/full-rehearsal.md', 'docs/full-rehearsal.en.md',
    'docs/implementation-review.md', 'docs/implementation-review.en.md',
)


def validate_sources():
    names = set(FILES)
    for name in FILES:
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing or linked release file: {name}')
        if path.suffix == '.py':
            tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module.split('.')[0] + '.py'
                    if (ROOT / module).is_file() and module not in names:
                        raise ValueError(f'Release omits a required local import: {module}')
        if path.suffix != '.md':
            continue
        for link in re.findall(r'\]\(([^\)]+)\)', path.read_text(encoding='utf-8')):
            if '://' in link:
                continue
            relative, _, anchor = unquote(link).partition('#')
            target = (path.parent / relative).resolve() if relative else path
            if not target.is_relative_to(ROOT) or target.relative_to(ROOT).as_posix() not in names:
                raise ValueError(f'Documentation link is outside the release: {name}: {link}')
            if anchor:
                headings = re.findall(r'^#+ (.+)$', target.read_text(encoding='utf-8'), flags=re.M)
                slugs = [re.sub(r'[^\w -]', '', h.lower()).replace(' ', '-') for h in headings]
                if anchor not in slugs:
                    raise ValueError(f'Missing documentation anchor: {name}: {link}')


def build(output):
    validate_sources()
    output = output.resolve()
    with ZipFile(output, 'x', compression=ZIP_DEFLATED) as zipped:
        for name in FILES:
            zipped.write(ROOT / name, f'{PACKAGE_ROOT}/{name}')
    with ZipFile(output) as zipped:
        if zipped.testzip() is not None or len(zipped.namelist()) != len(FILES):
            raise ValueError('Release ZIP failed integrity checking')
        for name in FILES:
            if zipped.read(f'{PACKAGE_ROOT}/{name}') != (ROOT / name).read_bytes():
                raise ValueError(f'Packaged bytes do not match source: {name}')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(digest + '\n', encoding='ascii')
    return output, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new source ZIP; parent directory must exist')
    args = parser.parse_args()
    output, digest = build(args.output)
    print(f'Built {VERSION}: {output}\nSHA-256: {digest}\nAllowlisted source files: {len(FILES)}')


if __name__ == '__main__':
    main()
