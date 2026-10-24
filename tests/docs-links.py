#!/usr/bin/env python3
"""Check links and anchors in the repository's Markdown files.

Every relative link must name an existing file (and, for a Markdown file, an
existing heading or HTML anchor). Links to this repository on GitHub
(`https://github.com/adrian729/ducktape-laya/blob/main/PATH#ANCHOR` and
`https://github.com/adrian729/ducktape-laya#ANCHOR`) are checked the same way
against the checkout: published package READMEs use them, so the paths and
anchors they name must stay (docs/RELEASING.md, "Stable documentation paths"),
and STABLE below lists them so they are checked even when no file links to them.
Headings are slugged as GitHub does: inline code keeps its text, only the
backticks go. Model-free and offline; external links are not fetched.

  docs-links.py              check the repository
  docs-links.py --self-test  check the checker on fixture files
"""
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = 'https://github.com/adrian729/ducktape-laya'
SKIP_DIRS = {'third_party', 'node_modules', '.git', '.research'}
LINK = re.compile(r'(?<!!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)|!\[[^\]]*\]\(\s*<?([^)\s>]+)>?\s*\)')
HTML_ANCHOR = re.compile(r'<a\s+(?:id|name)="([^"]+)"', re.I)
HTML_SRC = re.compile(r'(?:src|srcset|href)="([^"#:]+)"')
# docs/RELEASING.md, "Stable documentation paths": published package pages link to these,
# so they must exist even when nothing in the repository links to them.
STABLE = ['README.md#requirements', 'docs/API.md', 'docs/BROWSER.md', 'docs/COMPATIBILITY.md', 'docs/MODELS.md',
          'docs/PACKAGING.md#package-artifacts', 'docs/PACKAGING.md#npm-rust-and-python-artifacts', 'docs/PLATFORMS.md',
          'docs/PERFORMANCE.md', 'docs/BUILDING.md#build-the-library', 'docs/BUILDING.md#build-the-packages',
          'include/ducktape_laya.h']


def markdown_files():
    try:
        names = subprocess.run(['git', 'ls-files', '-z', '*.md'], cwd=ROOT, capture_output=True, check=True).stdout
        files = [ROOT / name for name in names.decode().split('\0') if name]
    except (OSError, subprocess.CalledProcessError):
        files = [path for path in ROOT.rglob('*.md')
                 if not SKIP_DIRS & set(path.relative_to(ROOT).parts) and not path.relative_to(ROOT).parts[0].startswith('build')]
    return sorted(path for path in files if path.is_file() and not SKIP_DIRS & set(path.relative_to(ROOT).parts))


def strip_code(text, inline=True):
    """Blank out fenced code blocks and (with inline) inline code, keeping line numbers."""
    lines, fence = [], None
    for line in text.split('\n'):
        marker = re.match(r'\s*(`{3,}|~{3,})', line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = None
            lines.append('')
        elif marker:
            fence = marker.group(1)
            lines.append('')
        else:
            lines.append(re.sub(r'(`+)(?:(?!\1).)+?\1', '', line) if inline else line)
    return lines


def slug(heading):
    text = re.sub(r'<[^>]+>', '', heading).strip().lower()
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = text.replace('`', '')
    text = re.sub(r'[^\w\- ]', '', text)
    return text.replace(' ', '-')


_anchors = {}


def anchors(path):
    if path not in _anchors:
        found, counts = set(), {}
        for line in strip_code(path.read_text(encoding='utf-8'), inline=False):
            heading = re.match(r'#{1,6}\s+(.*?)\s*#*\s*$', line)
            if heading:
                base = slug(heading.group(1))
                n = counts.get(base, 0)
                counts[base] = n + 1
                found.add(base if n == 0 else f'{base}-{n}')
            found.update(HTML_ANCHOR.findall(re.sub(r'(`+)(?:(?!\1).)+?\1', '', line)))
        _anchors[path] = found
    return _anchors[path]


def check_target(source, target, problems, where):
    if target.startswith(REPO):
        rest = target[len(REPO):]
        if rest.startswith('#') or rest in ('', '/'):
            path, anchor = ROOT / 'README.md', rest[1:] if rest.startswith('#') else ''
        elif rest.startswith('/blob/main/'):
            part, _, anchor = rest[len('/blob/main/'):].partition('#')
            path = ROOT / part
        else:
            return  # releases, tags, issues, other refs
    elif re.match(r'[a-z][a-z0-9+.-]*:', target, re.I) or target.startswith('//'):
        return  # external
    else:
        part, _, anchor = target.partition('#')
        path = source if not part else (source.parent / part)
    path = path.resolve()
    if not path.exists():
        problems.append(f'{where}: missing target {target}')
        return
    if anchor and path.suffix == '.md' and anchor not in anchors(path):
        problems.append(f'{where}: missing anchor #{anchor} in {path.relative_to(ROOT)}')


def check(root=None):
    """(problems, links checked) for the repository, or for the Markdown files under root (self-test)."""
    global ROOT
    if root is not None:
        ROOT = root.resolve()
        _anchors.clear()
    problems, count = [], 0
    for stable in STABLE:
        check_target(ROOT / 'README.md', f'{REPO}/blob/main/{stable}', problems, f'STABLE {stable}')
    sources = markdown_files() if root is None else sorted(root.rglob('*.md'))
    for source in sources:
        for number, line in enumerate(strip_code(source.read_text(encoding='utf-8')), 1):
            where = f'{source.relative_to(ROOT)}:{number}'
            targets = [a or b for a, b in LINK.findall(line)] + HTML_SRC.findall(line)
            for target in targets:
                count += 1
                check_target(source, target, problems, where)
    return problems, count


def self_test():
    """Fixture files: a heading with inline code, duplicate headings, a stable anchor that is missing."""
    saved, failures = ROOT, []
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / 'docs').mkdir()
            (root / 'include').mkdir()
            (root / 'include/ducktape_laya.h').write_text('')
            for name in STABLE:
                path = root / name.partition('#')[0]
                if path.suffix == '.md' and not path.exists():
                    path.write_text('# Title\n')
            (root / 'README.md').write_text('# Readme\n\n## Requirements\n')
            (root / 'docs/PACKAGING.md').write_text('# Packaging\n\n## Package artifacts\n\n<a id="npm-rust-and-python-artifacts"></a>\n')
            (root / 'docs/BUILDING.md').write_text('# Building\n\n## Build the library\n\n## Build the packages\n')
            (root / 'docs/API.md').write_text('# API\n\n## The `dtl_open` call\n\n## Notes\n\n## Notes\n\n'
                                             '```\n## Not a heading\n```\n\nSee `[code](missing.md)`.\n')
            (root / 'docs/LINKS.md').write_text('[a](API.md#the-dtl_open-call) [b](API.md#notes-1) [c](API.md#notes)\n')
            problems, _ = check(root)
            if problems:
                failures.append(f'valid fixture reported {problems}')
            (root / 'docs/LINKS.md').write_text('[a](API.md#the--call) [b](API.md#notes-2) [c](API.md#not-a-heading)\n')
            problems, _ = check(root)
            if len(problems) != 3:
                failures.append(f'expected 3 missing anchors (code removed from a heading, a third duplicate, a heading in a fence), got {problems}')
            (root / 'docs/LINKS.md').write_text('')
            (root / 'docs/BUILDING.md').write_text('# Building\n\n## Build the library\n')
            problems, _ = check(root)
            if not any(p.startswith('STABLE docs/BUILDING.md#build-the-packages') for p in problems) or len(problems) != 1:
                failures.append(f'expected the missing stable anchor to be reported alone, got {problems}')
    finally:
        globals()['ROOT'] = saved
        _anchors.clear()
    for failure in failures:
        print('FAIL:', failure)
    print(f'docs-links self-test: {len(failures)} failures')
    return 1 if failures else 0


def main():
    if sys.argv[1:] == ['--self-test']:
        return self_test()
    problems, count = check()
    for problem in problems:
        print(problem)
    print(f'{count} links and {len(STABLE)} stable paths checked, {len(problems)} problems')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
