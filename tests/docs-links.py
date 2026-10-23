#!/usr/bin/env python3
"""Check links and anchors in the repository's Markdown files.

Every relative link must name an existing file (and, for a Markdown file, an
existing heading or HTML anchor). Links to this repository on GitHub
(`https://github.com/adrian729/ducktape-laya/blob/main/PATH#ANCHOR` and
`https://github.com/adrian729/ducktape-laya#ANCHOR`) are checked the same way
against the checkout: published package READMEs use them, so the paths and
anchors they name must stay (docs/RELEASING.md, "Stable documentation paths").
Model-free and offline; external links are not fetched.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = 'https://github.com/adrian729/ducktape-laya'
SKIP_DIRS = {'third_party', 'node_modules', '.git', '.research'}
LINK = re.compile(r'(?<!!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)|!\[[^\]]*\]\(\s*<?([^)\s>]+)>?\s*\)')
HTML_ANCHOR = re.compile(r'<a\s+(?:id|name)="([^"]+)"', re.I)
HTML_SRC = re.compile(r'(?:src|srcset|href)="([^"#:]+)"')


def markdown_files():
    try:
        names = subprocess.run(['git', 'ls-files', '-z', '*.md'], cwd=ROOT, capture_output=True, check=True).stdout
        files = [ROOT / name for name in names.decode().split('\0') if name]
    except (OSError, subprocess.CalledProcessError):
        files = [path for path in ROOT.rglob('*.md')
                 if not SKIP_DIRS & set(path.relative_to(ROOT).parts) and not path.relative_to(ROOT).parts[0].startswith('build')]
    return sorted(path for path in files if path.is_file() and not SKIP_DIRS & set(path.relative_to(ROOT).parts))


def strip_code(text):
    """Blank out fenced code blocks and inline code, keeping line numbers."""
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
            lines.append(re.sub(r'(`+)(?:(?!\1).)+?\1', '', line))
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
        for line in strip_code(path.read_text(encoding='utf-8')):
            heading = re.match(r'#{1,6}\s+(.*?)\s*#*\s*$', line)
            if heading:
                base = slug(heading.group(1))
                n = counts.get(base, 0)
                counts[base] = n + 1
                found.add(base if n == 0 else f'{base}-{n}')
            found.update(HTML_ANCHOR.findall(line))
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


def main():
    problems, count = [], 0
    for source in markdown_files():
        for number, line in enumerate(strip_code(source.read_text(encoding='utf-8')), 1):
            where = f'{source.relative_to(ROOT)}:{number}'
            targets = [a or b for a, b in LINK.findall(line)] + HTML_SRC.findall(line)
            for target in targets:
                count += 1
                check_target(source, target, problems, where)
    for problem in problems:
        print(problem)
    print(f'{count} links checked, {len(problems)} problems')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
