#!/usr/bin/env python3
"""Model-free checks of scripts/release-notes.py, which renders the GitHub release text.

On a fixture CHANGELOG.md and release record: relative links (a path, a path with
an anchor, a bare anchor, a reference definition) become blob/vX.Y.Z URLs,
absolute URLs and links inside inline or fenced code stay as written; the install
table names the version and every release target's native archive; a missing
section or a version other than the record's fails. The repository's own
CHANGELOG.md "Unreleased" section (or its current version's) renders too.
"""
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/release-notes.py'
BLOB = 'https://github.com/adrian729/ducktape-laya/blob/v1.2.3'

CHANGELOG = '''# Changelog

Intro.

## Unreleased

- Preview entry.

## 1.2.3 (2027-01-02)

Notes in [compatibility](docs/COMPATIBILITY.md#migrating-from-120) and [the API](./docs/API.md).

- See [above](#unreleased), [external](https://example.com/a.md) and `[code](docs/X.md)` and [ref].

```text
[fenced](docs/Y.md)
```

[ref]: docs/REF.md

## 1.2.2

- Older.
'''


def run(root, *args):
    return subprocess.run([sys.executable, SCRIPT, '--root', root, *args], capture_output=True, text=True)


def main():
    failures = []
    def check(condition, what):
        if not condition: failures.append(what)
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        (root / 'release').mkdir()
        (root / 'CHANGELOG.md').write_text(CHANGELOG)
        targets = [{'platform': 'linux-x64', 'tier': 1}, {'platform': 'linux-arm64', 'tier': 2}, {'platform': 'windows-x64', 'tier': 2}]
        (root / 'release/compatibility.json').write_text(json.dumps({'package_version': '1.2.3', 'targets': targets}))
        result = run(root, '--version', '1.2.3')
        check(result.returncode == 0, f'renders 1.2.3: {result.stderr}')
        text = result.stdout
        for expected in (f'[compatibility]({BLOB}/docs/COMPATIBILITY.md#migrating-from-120)', f'[the API]({BLOB}/docs/API.md)',
                         f'[above]({BLOB}/CHANGELOG.md#unreleased)', '[external](https://example.com/a.md)',
                         '`[code](docs/X.md)`', '[fenced](docs/Y.md)', f'[ref]: {BLOB}/docs/REF.md',
                         '`io.github.adrian729:ducktape-laya:1.2.3`', 'bindings/go@v1.2.3`',
                         '`ducktape-laya-1.2.3-linux-x64.tar.gz`, `ducktape-laya-1.2.3-linux-arm64.tar.gz`, `ducktape-laya-1.2.3-windows-x64.zip`',
                         'Linux x86-64 (tier 1', 'Linux arm64 and Windows x64 (tier 2', '--cert-identity https://github.com/adrian729/ducktape-laya/.github/workflows/attest-release.yml@refs/heads/main',
                         'except `SHA256SUMS` and `gates.json`'):
            check(expected in text, f'output lacks {expected}')
        check('## 1.2.3' not in text and 'Older.' not in text and 'Preview entry.' not in text, 'output holds only the 1.2.3 section body')
        result = run(root, '--section', 'Unreleased')
        check(result.returncode == 0 and 'Preview entry.' in result.stdout and 'Notes in' not in result.stdout, f'renders Unreleased: {result.stderr}')
        result = run(root, '--version', '1.2.4')
        check(result.returncode != 0 and 'not 1.2.4' in result.stderr, 'refuses a version other than the record\'s')
        result = run(root, '--section', '9.9.9')
        check(result.returncode != 0 and 'no "## 9.9.9" section' in result.stderr, 'refuses a missing section')
        output = root / 'notes.md'
        result = run(root, '--output', output)
        check(result.returncode == 0 and output.read_text() == run(root).stdout, '--output writes the same text')
    # The repository's own text: Unreleased between releases, the version's section in the release commit.
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    version = json.loads((ROOT / 'release/compatibility.json').read_text())['package_version']
    args = ['--section', 'Unreleased'] if '\n## Unreleased\n' in changelog else ['--version', version]
    result = run(ROOT, *args)
    check(result.returncode == 0 and '](docs/' not in result.stdout and '](#' not in result.stdout,
          f'renders the repository CHANGELOG with absolute links: {result.stderr}')
    for failure in failures:
        print('FAIL:', failure)
    print(f'release notes: {len(failures)} failures')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
