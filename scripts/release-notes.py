#!/usr/bin/env python3
"""Render the GitHub release text for a version (docs/RELEASING.md, step 3.3).

The text is an install table naming the version and every release target's
native archive, the platforms the packages carry, how to verify the attested
assets (what .github/workflows/attest-release.yml signs), and the version's
CHANGELOG.md section without its heading. Relative Markdown links in the
section are rewritten to https://github.com/adrian729/ducktape-laya/blob/vX.Y.Z/...
so they keep pointing at the released docs; links in code are left alone.

The version is release/compatibility.json's package_version; --version must
equal it. The section is "## X.Y.Z (date)" (or "## X.Y.Z"); --section Unreleased
previews the text before the release commit names the section.

  python3 scripts/release-notes.py --version 0.3.0 --output build/release-notes.md
"""
import argparse
import json
import pathlib
import posixpath
import re
import sys

from release_support import ROOT, TARGETS

REPO = 'https://github.com/adrian729/ducktape-laya'
PREDICATE = f'{REPO}/release-qualification/v1'
SIGNER = f'{REPO}/.github/workflows/attest-release.yml@refs/heads/main'
PLATFORM_NAMES = {'linux-x64': 'Linux x86-64', 'linux-arm64': 'Linux arm64', 'windows-x64': 'Windows x64',
                  'macos-arm64': 'macOS arm64'}
CODE_SPAN = re.compile(r'(`+)(?:(?!\1).)+?\1')
INLINE_LINK = re.compile(r'(\]\(\s*<?)([^)\s>]+)')
REFERENCE_LINK = re.compile(r'^(\s*\[[^\]]+\]:\s*<?)(\S+?)(>?(?:\s.*)?)$')


def section(changelog, heading):
    """The body of the '## heading' section: heading is 'Unreleased' or a version (with or without a date)."""
    lines, body = changelog.splitlines(), None
    pattern = re.compile(rf'## {re.escape(heading)}(?: \(\d{{4}}-\d{{2}}-\d{{2}}\))?\s*$')
    for line in lines:
        if body is None:
            if pattern.fullmatch(line):
                body = []
        elif line.startswith('## '):
            break
        else:
            body.append(line)
    if body is None:
        raise ValueError(f'CHANGELOG.md has no "## {heading}" section')
    text = '\n'.join(body).strip('\n')
    if not text.strip():
        raise ValueError(f'CHANGELOG.md section "## {heading}" is empty')
    return text


def absolute(target, tag):
    """A link target from CHANGELOG.md (at the repository root) as a URL at the tag; absolute URLs unchanged."""
    if re.match(r'[a-z][a-z0-9+.-]*:', target, re.I) or target.startswith('//'):
        return target
    path, hash_, anchor = target.partition('#')
    if not path:
        path = 'CHANGELOG.md'
    path = posixpath.normpath(path.lstrip('/'))
    if path == '..' or path.startswith('../'):
        raise ValueError(f'Link leaves the repository: {target}')
    return f'{REPO}/blob/{tag}/{path}{hash_}{anchor}'


def rewrite_links(text, tag):
    out, fence = [], None
    for line in text.split('\n'):
        marker = re.match(r'\s*(`{3,}|~{3,})', line)
        if fence or marker:
            if fence is None:
                fence = marker.group(1)
            elif marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = None
            out.append(line)
            continue
        reference = REFERENCE_LINK.match(line)
        if reference:
            out.append(reference.group(1) + absolute(reference.group(2), tag) + reference.group(3))
            continue
        pieces, start = [], 0
        for code in CODE_SPAN.finditer(line):
            pieces.append(INLINE_LINK.sub(lambda m: m.group(1) + absolute(m.group(2), tag), line[start:code.start()]))
            pieces.append(code.group(0))
            start = code.end()
        pieces.append(INLINE_LINK.sub(lambda m: m.group(1) + absolute(m.group(2), tag), line[start:]))
        out.append(''.join(pieces))
    return '\n'.join(out)


def render(root, version=None, heading=None):
    record = json.loads((root / 'release/compatibility.json').read_text())
    released = record['package_version']
    if version and version != released:
        raise ValueError(f'release/compatibility.json names {released}, not {version}')
    version, tag = released, f'v{released}'
    targets = {t.name: t for t in TARGETS}
    platforms = [(item['platform'], item['tier']) for item in record['targets']]
    archives = ', '.join(f'`ducktape-laya-{version}-{name}{targets[name].archive}`' for name, _ in platforms)
    names = lambda tier: [PLATFORM_NAMES[name] for name, t in platforms if t == tier]
    join = lambda items: items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]
    carried = f'{join(names(1))} (tier 1, every release check)'
    if names(2):
        carried += f' and {join(names(2))} (tier 2, tested in CI with the English model)'
    body = rewrite_links(section((root / 'CHANGELOG.md').read_text(encoding='utf-8'), heading or version), tag)
    return f'''## Install

| Language | Package |
| --- | --- |
| Node.js, Electron | `npm install ducktape-laya` |
| Browser (experimental) | `npm install ducktape-laya-browser` |
| Rust | `cargo add ducktape-laya` |
| Python | `pip install ducktape-laya` |
| Java / Kotlin | `io.github.adrian729:ducktape-laya:{version}` (Maven Central) |
| .NET | `dotnet add package DucktapeLaya` |
| Go | `go get github.com/adrian729/ducktape-laya/bindings/go@{tag}` |
| C / C++ | {archives} below |

The native packages carry {carried} ([platforms]({REPO}/blob/{tag}/docs/PLATFORMS.md)).

Every asset below except `SHA256SUMS` and `gates.json` is attested; `SHA256SUMS` lists the hash of every other asset, and the attestation records the hash of `gates.json`, the qualification report for this commit. Verify a file ([details]({REPO}/blob/{tag}/SECURITY.md#verify-a-download)):

```sh
gh attestation verify FILE -R adrian729/ducktape-laya \\
  --predicate-type {PREDICATE} \\
  --cert-identity {SIGNER}
```

{body}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--version', help='Release version; must be release/compatibility.json\'s package_version')
    p.add_argument('--section', help='CHANGELOG.md section to use (default: the version), e.g. Unreleased')
    p.add_argument('--root', type=pathlib.Path, default=ROOT, help=argparse.SUPPRESS)
    p.add_argument('--output', type=pathlib.Path, help='Write here instead of stdout')
    a = p.parse_args()
    try:
        text = render(a.root, a.version, a.section)
    except (ValueError, OSError, KeyError) as error:
        sys.exit(f'release-notes: {error}')
    if a.output:
        a.output.write_text(text, encoding='utf-8')
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main()
