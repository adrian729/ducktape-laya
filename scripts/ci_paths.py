#!/usr/bin/env python3
"""What each CI and local verification job covers, as data (docs/RELEASE_CHECKS.md, Continuous integration).

One module answers "which jobs does this change make stale" for the workflows' `changes` job
(scripts/ci-changes.py), for scripts/verify.py (local jobs and the CI dispatch) and for
scripts/check-policy.py (which policy checks a change selects):

- CATEGORIES: input globs per category (fnmatch: `*` also matches `/`). A path may be in several.
  A path in no category marks every category stale, and so does a path of the `ci` category.
- POLICY_CHECKS: the input globs of each policy check. The commands live only in
  scripts/check-policy.py. A glob `deleted:*` matches a deleted path (a link to it may break).
- VALIDATE_CHECKS: every scripts/validate.py run label and shard check, with its categories.
- JOBS: every CI job by its exact `name:`, with its categories, profiles, workflow, per-profile
  `needs` and `artifact_needs` (upstream jobs whose same-run artifacts it reads) and
  `rebuild_with` (upstream jobs whose rerun forces this job to rerun; only the packaging chain).
- SHARDS: the real-model and candidate shard jobs per platform, their checks and side outputs.
- LOCAL_JOBS: what scripts/verify.py runs on this host.
- BUDGETS, JOB_BUDGETS: p90 wall-time budgets in seconds (the last 10 runs of a workflow or job).

Run it to check the tables (`python3 scripts/ci_paths.py`); tests/ci-changes.py does too.
"""
import fnmatch
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANGUAGES = ('rust', 'node', 'python', 'java', 'dotnet', 'go')
PROFILES = ('push', 'real_model', 'candidate', 'qualify', 'warm', 'experiment')
WORKFLOWS = ('native.yml', 'platforms.yml')

# --------------------------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------------------------
_NATIVE = [
    'src/*', 'include/*', 'cmake/*', 'patches/*.patch', 'CMakeLists.txt', 'CMakePresets.json', 'release/compatibility.json',
    'third_party', 'third_party/*', '.gitmodules', 'tests/*.cpp', 'tests/*.c', 'tests/*.hpp', 'tests/fixtures/*',
    'tests/original-reference/routing/*', 'tests/consumers/c.c', 'tests/consumers/cpp.cpp', 'bindings/node/*.cpp',
    'examples/*.c', 'examples/*.cpp', 'examples/*.h', 'examples/*.json', 'examples/presets/*', 'scripts/validate.py',
    'scripts/release_support.py', 'scripts/check-binary.py', 'tests/cpu-guard.py', 'tests/examples-smoke.py',
    'tests/reconfigure.py', 'tests/compile-commands.py',
    # Also read by every generic validate.py run: the ABI export check, the release policy
    # (check-binary.py's glibc floor, check_record) and the pinned model manifests.
    'scripts/check-abi.py', 'release/qualification.json', 'models/*.json',
]
_BINDING_SHARED = ['include/ducktape_laya.h']
_BINDINGS = {
    'rust': ['bindings/rust/*', 'tests/consumers/rust/*'],
    'node': ['bindings/node/*', 'tests/consumers/node.cjs', 'tests/typescript/*'],
    'python': ['bindings/python/*', 'tests/consumers/python/*'],
    'java': ['bindings/java/*', 'tests/consumers/java/*'],
    'dotnet': ['bindings/dotnet/*', 'tests/consumers/dotnet/*'],
    'go': ['bindings/go/*', 'tests/consumers/go/*'],
}
CATEGORIES = {
    'docs': ['docs/*', 'assets/*', 'README.md', 'CHANGELOG.md', 'SECURITY.md', 'AGENTS.md', 'CLAUDE.md', 'examples/README.md',
             'patches/*.md'],
    'native': _NATIVE,
    'abi': ['include/*', 'tests/abi-layout.py', 'tests/consumers/c.c', 'tests/consumers/cpp.cpp'],
    'packaging_identity': ['cmake/*', 'CMakeLists.txt', 'tests/packaging-identity.py', 'tests/runtime-sources.py',
                           'scripts/release_support.py', 'scripts/artifact_support.py'],
    **{f'binding_{lang}': globs + _BINDING_SHARED for lang, globs in _BINDINGS.items()},
    'packaging': ['scripts/package-native.py', 'scripts/artifact_support.py', 'scripts/java_package.py', 'scripts/python_wheel.py',
                  'scripts/sbom.py', 'scripts/assemble-packages.py', 'scripts/check-binary.py', 'tests/package-smoke.py',
                  'tests/package_support.py', 'tests/installed-packages.py', 'tests/installed-native.py', 'tests/musl-packages.sh',
                  'tests/electron/*', 'cmake/*', 'CMakeLists.txt', 'release/*.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
                  'licenses/*', 'models/*.json', 'bindings/*/package.json', 'bindings/*/Cargo.toml', 'bindings/*/pyproject.toml',
                  'bindings/*/*.csproj', 'bindings/*/README.md', 'scripts/download-model.py'],
    'sanitizers': _NATIVE + ['scripts/check-sanitizers.py'],
    'fuzz': ['src/*', 'include/*', 'cmake/*', 'patches/*.patch', 'CMakeLists.txt', 'CMakePresets.json', 'third_party*',
             'tests/input-libfuzzer.cpp', 'tests/fuzz-corpus.py', 'tests/fixtures/*', 'scripts/check-fuzz.py'],
    'windows': _NATIVE + ['bindings/node/win_delay_load_hook.cpp', 'scripts/fetch-node-sdk.py', 'scripts/node-sdk-sha256.json'],
    'arm64': _NATIVE,
    'clang': _NATIVE,
    'macos': _NATIVE + ['src/exports.macos'],
    # M4: the source inputs too; a clang-cl anonymous-namespace name in a moved TU breaks Windows reproducibility.
    'reproducibility': ['src/*', 'include/*', 'bindings/node/*.cpp', 'cmake/*', 'CMakeLists.txt', 'patches/*.patch', 'third_party*',
                        '.gitmodules', 'release/compatibility.json', 'scripts/check-reproducibility.py', 'scripts/release_support.py',
                        'scripts/fetch-node-sdk.py', 'scripts/node-sdk-sha256.json'],
    # The real-model native checks (ctest, the original and upstream references, the cross-host record).
    'real_model_native': _NATIVE + ['tests/original-reference*', 'tests/compare-native-reference.py', 'tests/reference_requests.py',
                                    'tests/upstream-reference.cpp', 'tests/original-reference.py', 'tests/cross-host.py', 'models/*.json'],
    'real_model': _NATIVE + ['bindings/*.cjs', 'bindings/*.mjs', 'bindings/*.cpp', 'bindings/*.rs', 'bindings/*.toml', 'bindings/*.lock',
                             'bindings/*.py', 'bindings/*.go', 'bindings/*.mod', 'bindings/*.java', 'bindings/*.xml', 'bindings/*.sh',
                             'bindings/*.cs', 'bindings/*.csproj', 'bindings/*.slnx', 'tests/consumers/*', 'tests/parity.py',
                             'tests/original-reference*', 'tests/compare-native-reference.py', 'tests/reference_requests.py',
                             'tests/upstream-reference.cpp', 'tests/original-reference.py', 'tests/cross-host.py', 'models/*.json'],
    # Host only: no CI job; scripts/verify.py runs the browser builds with --emsdk.
    'browser': ['bindings/browser/*', 'tests/browser*', 'tests/*worker*.mjs', 'tests/browser-automation/*', 'scripts/build-browser.py',
                'scripts/package-browser.py', 'release/browser-toolchain.json'] + _NATIVE,
    # Host-only release gates (scripts/qualify-host.py), run at release qualification and in the candidate run,
    # which is never skipped; no push or real-model job reads them.
    'host': ['scripts/qualify-host.py', 'tests/qualify-host.py', 'scripts/check-stress.py', 'scripts/check-upgrade.py',
             'scripts/performance-budgets.py', 'release/budgets/*', 'tests/benchmark.py', 'tests/rollback.py', 'tests/linux-userspace.py',
             'tests/linux-userspaces.json', 'tests/download-userspaces.py', 'tests/cross-host.py', 'scripts/convert-model.py',
             'scripts/verify-model.py', 'tests/compare-profiles.py', 'tests/compare-browser.py', 'tests/compare-browser-reference.py',
             'scripts/setup.py', 'scripts/check-release.py'],
    # Local tooling with its own policy check and no CI job of its own.
    'tooling': ['scripts/verify.py', 'tests/verify.py', 'scripts/check-policy.py', 'scripts/ci-cache-prune.py',
                '.gitignore', '.gitattributes', '.ignore', 'demo'],
    # M2: the classifier and the workflows; every job of every profile is stale.
    'ci': ['.github/*', 'scripts/ci_paths.py', 'scripts/ci-changes.py', 'tests/ci-changes.py'],
}
CATEGORIES['browser'] = list(dict.fromkeys(CATEGORIES['browser']))

# --------------------------------------------------------------------------------------------
# Policy checks: input globs only (commands: scripts/check-policy.py)
# --------------------------------------------------------------------------------------------
_RELEASE_SUPPORT = ['scripts/release_support.py']
_DOCS = ['*.md', 'docs/*']
# What scripts/validate.py loads or runs by path: a check that loads validate.py depends on them.
_VALIDATE = ['scripts/validate.py', 'scripts/check-policy.py', 'scripts/ci_paths.py', 'scripts/check-abi.py', 'scripts/check-binary.py',
             'scripts/check-reproducibility.py', 'scripts/package-native.py', 'scripts/download-model.py', 'scripts/artifact_support.py',
             'scripts/java_package.py', 'scripts/python_wheel.py', 'scripts/sbom.py', 'scripts/release_targets.py']
POLICY_CHECKS = {
    'result-comparison': ['tests/result-comparison.py', 'scripts/compare_results.py', 'tests/compare-browser.py', 'tests/compare-profiles.py',
                          'tests/compare-browser-reference.py'] + _RELEASE_SUPPORT,
    # versioned_doc_problems reads the docs that name the release.
    'qualification-policy': ['tests/qualification.py', 'release/*', 'models/*.json'] + _RELEASE_SUPPORT + _DOCS,
    # Any Markdown, the stable paths it checks, and any deleted path (a link to it may break).
    'docs-links': ['tests/docs-links.py', 'include/ducktape_laya.h', 'deleted:*'] + _DOCS,
    'docs-links-self-test': ['tests/docs-links.py'],
    'release-notes': ['tests/release-notes.py', 'scripts/release-notes.py', 'CHANGELOG.md', 'release/*'] + _RELEASE_SUPPORT + _DOCS,
    # Also checks that every tracked path is classified and that each policy check's inputs cover what it imports.
    'ci-changes': ['tests/ci-changes.py', 'scripts/ci-changes.py', 'scripts/ci_paths.py', 'scripts/check-policy.py', 'scripts/*.py',
                   'tests/*.py', 'deleted:*'],
    'abi-layouts': ['tests/abi-layout.py', 'include/*', 'bindings/rust/*'],
    'typescript-consumers': ['tests/typescript/*', 'bindings/node/*', 'bindings/browser/*', 'examples/demo.cjs', 'examples/demo.cts'],
    'packaging-identities': ['tests/packaging-identity.py', 'cmake/*', 'CMakeLists.txt', 'bindings/*/pom.xml', 'bindings/*/pyproject.toml',
                             'bindings/node/*', 'bindings/browser/*', 'scripts/artifact_support.py', 'scripts/package-native.py',
                             'scripts/package-browser.py', 'scripts/build-browser.py', 'scripts/java_package.py', 'scripts/python_wheel.py',
                             'scripts/sbom.py', 'scripts/check-binary.py', 'licenses/*', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
                             'release/*'] + _RELEASE_SUPPORT,
    'package-assembly': ['tests/package-assembly.py', 'scripts/assemble-packages.py', 'scripts/package-native.py', 'scripts/artifact_support.py',
                         'scripts/java_package.py', 'scripts/python_wheel.py', 'scripts/sbom.py', 'bindings/*/package.json',
                         'bindings/*/pom.xml', 'bindings/*/pyproject.toml', 'bindings/*/*.csproj', 'bindings/*/Cargo.toml',
                         'release/*'] + _RELEASE_SUPPORT,
    'fetch-ci-evidence': ['tests/fetch-ci-evidence.py', 'scripts/fetch-ci-evidence.py', 'scripts/artifact_support.py',
                          'scripts/release_targets.py', 'release/*'] + _RELEASE_SUPPORT,
    'release-targets': ['tests/release-targets.py', 'tests/release_world.py', 'scripts/check-candidate.py', 'scripts/release_targets.py',
                        'scripts/artifact_support.py', 'scripts/assemble-packages.py', 'scripts/fetch-ci-evidence.py',
                        'scripts/compare_results.py', 'scripts/java_package.py', 'scripts/python_wheel.py', 'scripts/package-native.py',
                        'scripts/sbom.py', 'scripts/performance-budgets.py', 'tests/reference_requests.py', 'bindings/*/package.json', 'include/ducktape_laya.h', 'release/*',
                        'models/*.json'] + _VALIDATE + _RELEASE_SUPPORT + _DOCS,
    'release-assets': ['tests/release-assets.py', 'tests/release_world.py', 'scripts/release-assets.py', 'scripts/check-release-assets.sh',
                       'scripts/check-release-run.py', 'scripts/fetch-ci-evidence.py', 'scripts/pypi-missing-wheels.py',
                       'scripts/registry-state.py', 'scripts/release-gates.jq', 'scripts/verify-release-assets.sh',
                       'scripts/tag-commit.sh', 'scripts/assemble-packages.py', 'scripts/artifact_support.py',
                       'scripts/release_targets.py', 'scripts/check-candidate.py', 'scripts/java_package.py', 'scripts/python_wheel.py',
                       'scripts/package-native.py', 'scripts/sbom.py', '.github/workflows/publish-release.yml',
                       '.github/workflows/attest-release.yml', 'bindings/*/package.json', 'release/*'] + _RELEASE_SUPPORT + _DOCS,
    'browser-backend-identities': ['tests/browser-backend.mjs', 'bindings/browser/*'],
    'browser-lifecycle': ['tests/browser-lifecycle.mjs', 'bindings/browser/*'],
    'browser-binding': ['bindings/browser/*'],
    'model-download': ['tests/download-model.py', 'scripts/download-model.py', 'models/*.json'],
    'runtime-sources': ['tests/runtime-sources.py', 'cmake/*', 'CMakeLists.txt', 'release/compatibility.json'] + _RELEASE_SUPPORT,
    'merge-validation': ['tests/merge-validation.py', 'tests/merge-validation-files.json', 'scripts/merge-validation.py',
                         'scripts/release_targets.py', 'scripts/validate.py', 'scripts/artifact_support.py', 'release/*'] + _RELEASE_SUPPORT,
    'validate': ['tests/validate.py', 'release/*'] + _VALIDATE + _RELEASE_SUPPORT,
    'check-sanitizers': ['tests/check-sanitizers.py', 'scripts/check-sanitizers.py'] + _RELEASE_SUPPORT,
    'verify': ['tests/verify.py', 'scripts/verify.py', 'scripts/ci_paths.py', 'scripts/ci-changes.py'],
    'qualify-host': ['tests/qualify-host.py', 'scripts/qualify-host.py', 'scripts/check-candidate.py', 'release/*', 'models/*.json',
                     # what check-candidate.py imports (the test reads its reuse rules)
                     'scripts/release_support.py', 'scripts/artifact_support.py', 'scripts/release_targets.py',
                     'scripts/performance-budgets.py'],
    'ci-contracts': ['tests/ci-contracts.py', '.github/*', 'scripts/ci-cache-prune.py'] + _VALIDATE + _RELEASE_SUPPORT,
}
# The checks bindings-<P> runs in the candidate (`validate.py --checks policy-platform`); check-policy.py --platform-only.
PLATFORM_POLICY_CHECKS = ('abi-layouts', 'packaging-identities', 'browser-backend-identities', 'typescript-consumers')
# A policy input is a known path: the `policy` category is their union (pseudo globs left out).
CATEGORIES['policy'] = sorted({glob for globs in POLICY_CHECKS.values() for glob in globs if not glob.startswith('deleted:')})

# --------------------------------------------------------------------------------------------
# validate.py checks (run labels and shard checks) by category; validate.FULL_CHECKS must be a subset.
# --------------------------------------------------------------------------------------------
def _additional_model_ids():
    try:
        record = json.loads((ROOT / 'release/compatibility.json').read_text())
        return [json.loads((ROOT / path).read_text())['id'] for path in record.get('additional_models', [])]
    except (OSError, ValueError, KeyError, TypeError):
        return []


VALIDATE_CHECKS = {
    'configure': ['native'], 'build': ['native'], 'native': ['native'], 'examples-smoke': ['native'], 'abi': ['native', 'abi'],
    'check-binary': ['native', 'packaging'],
    **{lang: [f'binding_{lang}'] for lang in LANGUAGES},
    'java-compile': ['binding_java'], 'java-test-compile': ['binding_java'],
    'bindings': [f'binding_{lang}' for lang in LANGUAGES] + ['real_model'],
    'bindings:a': [f'binding_{lang}' for lang in ('rust', 'node', 'python')] + ['real_model'],
    'bindings:b': [f'binding_{lang}' for lang in ('java', 'dotnet', 'go')] + ['real_model'],
    'bindings-a': ['real_model'], 'bindings-b': ['real_model'], 'bindings-smoke': ['real_model'],
    'bindings-smoke-a': ['real_model'], 'bindings-smoke-b': ['real_model'],
    'original-reference': ['real_model'], 'upstream-reference': ['real_model'],
    'package': ['packaging'], 'package-smoke': ['packaging'], 'package-assembly-real': ['packaging'], 'assembled-smoke': ['packaging'],
    'reproducibility': ['reproducibility'],
    # Shard checks (SHARDS) and the policy selection.
    'ctest': ['native'], 'references': ['real_model'], 'parity:smoke': ['real_model'], 'parity:smoke-a': ['real_model'],
    'parity:smoke-b': ['real_model'], 'parity:full-a': ['real_model'],
    'parity:full-b': ['real_model'], 'packages': ['packaging'], 'policy-platform': ['policy'], 'policy': ['policy'],
    **{label: ['policy'] for label in POLICY_CHECKS},
    **{f'policy:{label}': ['policy'] for label in POLICY_CHECKS},
    **{f'{kind}-{model}': ['real_model'] for model in _additional_model_ids()
       for kind in ('tokenizer', 'decision-head', 'control-tokenize', 'original-reference', 'upstream-reference')},
}

# --------------------------------------------------------------------------------------------
# Shards: SHARDS[mode][platform] = {'profile' (candidate only), 'jobs': {job: [checks]}, 'side_outputs': {job: [names]}}
# --------------------------------------------------------------------------------------------
BUILD_PLATFORMS = ('linux-x64', 'linux-x64-clang', 'linux-arm64', 'windows-x64')
PACKAGE_PLATFORMS = ('linux-x64', 'linux-arm64', 'windows-x64')
TIER2_PLATFORMS = ('linux-arm64', 'windows-x64')
PLATFORM_CATEGORIES = {'linux-x64': ['native'], 'linux-x64-clang': ['clang'], 'linux-arm64': ['arm64'], 'windows-x64': ['windows']}
SHARDS = {
    'real_model': {
        'linux-x64': {'jobs': {'native-linux-x64': ['ctest', 'references'], 'parity-linux-x64': ['parity:smoke'],
                               'bindings-real-linux-x64': ['bindings']}, 'side_outputs': {}},
        # Decision 4: the clang leg runs only its native checks in plain dispatches; ctest and the references in
        # parallel jobs, as on Windows (together they took 272 s, one after the other).
        'linux-x64-clang': {'jobs': {'native-linux-x64-clang': ['ctest'], 'references-linux-x64-clang': ['references']},
                            'side_outputs': {}},
        'linux-arm64': {'jobs': {'native-linux-arm64': ['ctest', 'references'], 'parity-linux-arm64': ['parity:smoke'],
                                 'bindings-real-linux-arm64': ['bindings']}, 'side_outputs': {}},
        # Windows' parity and binding suites in the halves of tests/parity.py: run together on one runner, each took
        # about 260 s after the build and set the dispatch's wall (run 40218330855: 513 s).
        'windows-x64': {'jobs': {'ctest-windows-x64': ['ctest'], 'references-windows-x64': ['references'],
                                 'parity-a-windows-x64': ['parity:smoke-a'], 'parity-b-windows-x64': ['parity:smoke-b'],
                                 'bindings-real-a-windows-x64': ['bindings:a'], 'bindings-real-b-windows-x64': ['bindings:b']},
                        'side_outputs': {}},
    },
    'candidate': {
        'linux-x64': {'profile': 'ci-full',
                      'jobs': {'native-linux-x64': ['ctest', 'references'], 'parity-a-linux-x64': ['parity:full-a'],
                               'parity-b-linux-x64': ['parity:full-b'], 'packages-linux-x64': ['packages'],
                               'bindings-linux-x64': ['bindings', 'policy-platform']},
                      'side_outputs': {'native-linux-x64': ['cross-host']}},
        'linux-x64-clang': {'profile': 'ci-compiler',
                            'jobs': {'native-linux-x64-clang': ['ctest'], 'references-linux-x64-clang': ['references'],
                                     'parity-a-linux-x64-clang': ['parity:full-a'],
                                     'parity-b-linux-x64-clang': ['parity:full-b'], 'bindings-linux-x64-clang': ['bindings']},
                            'side_outputs': {}},
        # arm64's parity is one shard (137 s today), so the platform has four shard jobs.
        'linux-arm64': {'profile': 'ci-full',
                        'jobs': {'native-linux-arm64': ['ctest', 'references'], 'parity-linux-arm64': ['parity:full-a', 'parity:full-b'],
                                 'packages-linux-arm64': ['packages'], 'bindings-linux-arm64': ['bindings', 'policy-platform']},
                        'side_outputs': {}},
        # Six real-model suites in one shard took 294 s (budget 270 s, run 40251619567): two halves, as the real-model mode's.
        'windows-x64': {'profile': 'ci-full',
                        'jobs': {'ctest-windows-x64': ['ctest'], 'references-windows-x64': ['references'],
                                 'parity-a-windows-x64': ['parity:full-a'], 'parity-b-windows-x64': ['parity:full-b'],
                                 'packages-windows-x64': ['packages'], 'bindings-a-windows-x64': ['bindings:a', 'policy-platform'],
                                 'bindings-b-windows-x64': ['bindings:b']},
                        'side_outputs': {}},
    },
}

# --------------------------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------------------------
JOBS = {}


def _job(name, categories, profiles, needs, workflow='native.yml', artifact_needs=None, rebuild_with=(), **extra):
    """Add or extend a job; a name in several profiles keeps one entry with per-profile needs."""
    entry = JOBS.setdefault(name, {'categories': [], 'profiles': [], 'workflow': workflow, 'needs': {}, 'artifact_needs': {},
                                   'rebuild_with': []})
    if entry['workflow'] != workflow:
        raise ValueError(f'{name}: in two workflows')
    entry['categories'] = list(dict.fromkeys(entry['categories'] + list(categories)))
    for profile in profiles:
        if profile not in entry['profiles']:
            entry['profiles'].append(profile)
        entry['needs'][profile] = list(needs)
        entry['artifact_needs'][profile] = list((artifact_needs or {}).get(profile, []))
    entry['rebuild_with'] = list(dict.fromkeys(entry['rebuild_with'] + list(rebuild_with)))
    entry.update(extra)


# `changes` runs in every workflow and every run; it is not a verification unit.
_job('changes', [], PROFILES, [], always=True, also_in=['platforms.yml'])
# Push profile.
_job('policy', ['policy'], ['push'], ['changes'])
_job('generic-linux-x64', ['native'], ['push'], ['changes'])
_job('generic-linux-x64-clang', ['clang'], ['push'], ['changes'])
_job('generic-linux-arm64', ['arm64'], ['push'], ['changes'])
# S2: abi-layouts and packaging-identities run in the Windows generic job when their category is stale.
_job('generic-windows-x64', ['windows', 'abi', 'packaging_identity'], ['push'], ['changes'])
for _lang in LANGUAGES:
    for _platform in ('linux-x64', 'windows-x64'):
        _job(f'binding-{_lang}-{_platform}', [f'binding_{_lang}'], ['push'], ['changes'])
PACKAGES = [f'packages-{p}' for p in PACKAGE_PLATFORMS]
for _platform in PACKAGE_PLATFORMS:
    # One name, two workflow jobs: no build dependency in the push profile, the build tarball in the candidate.
    _job(f'packages-{_platform}', ['packaging'], ['push'], ['changes'])
    _job(f'packages-{_platform}', ['packaging'], ['candidate'], [f'build-{_platform}'],
         artifact_needs={'candidate': [f'build-{_platform}']})
_job('assemble', ['packaging'], ['push', 'candidate'], PACKAGES, artifact_needs={'push': PACKAGES, 'candidate': PACKAGES},
     rebuild_with=PACKAGES)
for _name in [f'assembled-{p}' for p in PACKAGE_PLATFORMS] + ['musl']:
    _job(_name, ['packaging'], ['push', 'candidate'], ['assemble'], artifact_needs={'push': ['assemble'], 'candidate': ['assemble']},
         rebuild_with=['assemble'])
_job('sanitizers', ['sanitizers'], ['push'], ['changes'])
_job('fuzzer', ['fuzz'], ['push'], ['changes'])
_job('reproducibility-windows-x64', ['reproducibility'], ['push', 'candidate'], ['changes'])
_job('reproducibility-linux-arm64', ['reproducibility'], ['candidate'], ['changes'])
# Real-model and candidate profiles.
for _platform in BUILD_PLATFORMS:
    _job(f'build-{_platform}', PLATFORM_CATEGORIES[_platform], ['real_model', 'candidate'], ['changes'])
for _mode, _profile in (('real_model', 'real_model'), ('candidate', 'candidate')):
    for _platform, _shards in SHARDS[_mode].items():
        for _name, _checks in _shards['jobs'].items():
            if _name in PACKAGES:
                continue  # added above with its two profiles
            if _checks in (['bindings'], ['bindings:a'], ['bindings:b']):
                _categories = [f'binding_{lang}' for lang in LANGUAGES]  # the real-model binding suites, when a binding is stale
            elif len(_checks) == 1 and _checks[0].startswith('bindings:'):
                _categories = [c for c in VALIDATE_CHECKS[_checks[0]] if c.startswith('binding_')]  # its half's suites
            elif set(_checks) <= {'ctest', 'references'}:
                _categories = ['real_model_native'] + PLATFORM_CATEGORIES[_platform]
            else:
                _categories = ['real_model'] + PLATFORM_CATEGORIES[_platform]
            _job(_name, _categories, [_profile], [f'build-{_platform}'], artifact_needs={_profile: [f'build-{_platform}']})
for _platform in TIER2_PLATFORMS:
    _job(f'stress-{_platform}', ['native'] + PLATFORM_CATEGORIES[_platform] + ['host'], ['candidate'], [f'build-{_platform}'],
         artifact_needs={'candidate': [f'build-{_platform}']})
for _platform in BUILD_PLATFORMS:
    _upstream = [f'build-{_platform}'] + list(SHARDS['candidate'][_platform]['jobs'])
    if _platform in TIER2_PLATFORMS:
        _upstream += [f'stress-{_platform}', f'reproducibility-{_platform}']
    _job(f'evidence-{_platform}', ['real_model'] + PLATFORM_CATEGORIES[_platform], ['candidate'], _upstream,
         artifact_needs={'candidate': _upstream})
_job('tooling', ['policy'], ['candidate'], ['changes'])
# Qualify profile (mode=qualify, §3.6).
_job('qualify-sanitizers-address', ['sanitizers'], ['qualify'], ['changes'])
_job('qualify-sanitizers-thread', ['sanitizers'], ['qualify'], ['changes'])
_job('qualify-sanitizers', ['sanitizers'], ['qualify'], ['qualify-sanitizers-address', 'qualify-sanitizers-thread'],
     artifact_needs={'qualify': ['qualify-sanitizers-address', 'qualify-sanitizers-thread']})
_job('qualify-fuzz', ['fuzz'], ['qualify'], ['changes'])
_job('qualify-upgrade', ['real_model', 'host'], ['qualify'], ['changes'])
# Warm profile (mode=warm-cache and the nightly): build-only jobs per cache configuration, then the prune.
WARM_JOBS = ['warm-linux-x64', 'warm-linux-x64-clang', 'warm-linux-arm64', 'warm-windows-x64', 'warm-asan', 'warm-tsan', 'warm-fuzz']
for _name in WARM_JOBS:
    _job(_name, [], ['warm'], ['changes'], always=True)
_job('cache-prune', [], ['warm'], WARM_JOBS, always=True)
# Experiment profile (V1 only, R8).
_job('bench-windows-x64', [], ['experiment'], ['changes'], always=True)
# platforms.yml (macOS; never part of a candidate run).
_job('generic-macos-arm64', ['macos'], ['push'], ['changes'], workflow='platforms.yml')
for _lang in LANGUAGES:
    _job(f'binding-{_lang}-macos-arm64', [f'binding_{_lang}'], ['push'], ['changes'], workflow='platforms.yml')


def jobs_of(workflow, profile):
    """Names of the jobs of one workflow in one profile, `changes` excluded."""
    return [name for name, job in JOBS.items() if name != 'changes' and job['workflow'] == workflow and profile in job['profiles']]


BRANCH_PUSH_JOBS = {'native.yml': ['policy', 'generic-linux-x64'], 'platforms.yml': []}

# --------------------------------------------------------------------------------------------
# Local jobs (scripts/verify.py; outputs in build-verify/<job>/, browser builds in build-browser-<backend>)
# --------------------------------------------------------------------------------------------
LOCAL_JOBS = {
    'local-bindings': {'categories': [f'binding_{lang}' for lang in LANGUAGES]},
    'local-packaging': {'categories': ['packaging']},
    'local-sanitizers': {'categories': ['sanitizers']},
    'local-fuzz': {'categories': ['fuzz']},
    'local-real-model': {'categories': ['real_model'], 'requires': 'real_model'},
    'local-reproducibility': {'categories': ['reproducibility']},
    'local-browser-wasm': {'categories': ['browser'], 'requires': 'emsdk'},
    'local-browser-webgpu': {'categories': ['browser'], 'requires': 'emsdk'},
}

# --------------------------------------------------------------------------------------------
# Budgets (seconds, p90 of the last 10 runs; first values are the plan's estimates, R8)
# --------------------------------------------------------------------------------------------
BUDGETS = {
    'branch-push': 100,           # a native edit: changes + generic-linux-x64
    'docs-only': 45,              # changes + policy (docs-links, release-notes)
    'verify-ci-model-free': 300,  # verify.py --ci, every stale model-free job
    'real-model-dispatch': 480,   # a plain real_model=true dispatch
    'candidate': 660,             # mode=candidate
}
JOB_BUDGETS = {
    'changes': 25, 'policy': 60, 'tooling': 120,
    'generic-linux-x64': 80, 'generic-linux-x64-clang': 80, 'generic-linux-arm64': 90, 'generic-windows-x64': 130,
    'generic-macos-arm64': 120,
    **{f'binding-{lang}-{p}': 90 for lang in LANGUAGES for p in ('linux-x64', 'windows-x64')},
    # Windows setup (Defender, checkout, Node SDK, VS environment, the language's SDK) and the CMake
    # configure take about 75 s before any suite; warm run 40218330855 measured the suites on top of it:
    'binding-java-windows-x64': 115,    # 104 s warm (suite 47 s)
    'binding-rust-windows-x64': 115,    # 103 s warm (suite 44 s, toolchain install 5 s)
    'binding-go-windows-x64': 135,      # 121 s warm (suite 56 s, Go tool cache and setup 10 s)
    'binding-dotnet-windows-x64': 150,  # 137 s warm (suite 77 s; dotnet test alone 26 s)
    # 90 s could not be met: 55 s of setup and the configure and build (89 compiler-cache hits, 34 s) come before a 2 s suite.
    # Warm runs 70, 96, 96, 101 and 115 s (p90 115); the 159 s and 187 s runs recompiled changed sources and are cold-cache runs.
    'binding-python-windows-x64': 115,
    # The node job is the only Windows binding job that sets up Node and fetches its SDK, like its siblings' 75 s of setup, then a 45 s
    # suite: 90 s (the blanket value) held once; runs 40257329206 and 40259163744 took 100 s and 104 s. 110 until ten runs exist.
    'binding-node-windows-x64': 110,
    **{f'binding-{lang}-macos-arm64': 120 for lang in LANGUAGES},
    **{f'packages-{p}': 120 for p in PACKAGE_PLATFORMS}, 'assemble': 60,
    # 150 was the one warm run 40218330855 (128 s). The budget was raised to 180 from the p90 of the ten runs before the packages ran
    # concurrently (139 to 221 s), which measured the serial packaging this job no longer does. Concurrent packaging (27 s serial) has
    # one run so far, 40257329206: 148 s (about 85 s of setup: four SDK setups and the Visual Studio environment; 34 s of configure
    # and build, all 89 compiles compiler-cache hits; the packaging). Back at 150 until three runs of it exist, then their p90.
    'packages-windows-x64': 150,
    **{f'assembled-{p}': 180 for p in PACKAGE_PLATFORMS}, 'musl': 180,
    'sanitizers': 150, 'fuzzer': 120, 'reproducibility-windows-x64': 240, 'reproducibility-linux-arm64': 300,
    **{f'build-{p}': 210 for p in BUILD_PLATFORMS},
    **{name: 270 for mode in SHARDS.values() for shards in mode.values() for name in shards['jobs'] if name not in PACKAGES},
    # The parity launches run one after the other, 4 threads each on the runner's 4 cores, so they cannot overlap; their wall times
    # move together with the runner's CPU speed (111 to 188 s of launches in three runs of the same code). The 7 runs took
    # 138 to 271 s, p90 271 s: 275 replaces the blanket 270.
    'parity-a-windows-x64': 275,
    **{f'stress-{p}': 420 for p in TIER2_PLATFORMS}, **{f'evidence-{p}': 60 for p in BUILD_PLATFORMS},
    # mode=qualify (run 40243496491, fuzz_seconds=300; the longer defaults add to the campaign alone):
    'qualify-sanitizers-address': 800,  # 790 s (the sanitize preset build and the suites, ccache launcher)
    'qualify-sanitizers-thread': 900,   # 895 s (the sanitize-thread preset and the pinned Rust nightly)
    'qualify-sanitizers': 30,           # 12 s (merges the two reports)
    'qualify-fuzz': 3600 + 345,         # 643 s at fuzz_seconds=300, so 343 s of build and corpus setup around the default 3,600 s campaign
    'qualify-upgrade': 800,             # 755 s (gcc, every binding toolchain, QEMU for the CPU guard)
    # mode=warm-cache (run 40203145188, the only one so far; refresh from the p90 once there are ten):
    'warm-linux-x64': 60, 'warm-linux-arm64': 45,          # 35 s and 25 s (build phase 11 s on Linux x64: the compiler cache was warm)
    'warm-linux-x64-clang': 200, 'warm-windows-x64': 240,  # 180 s and 228 s
    'warm-asan': 330, 'warm-tsan': 270, 'warm-fuzz': 340,  # 322 s, 263 s and 333 s
    'cache-prune': 30,                                     # 12 s (gh api list and delete)
    # mode=experiment, two clang-cl builds of the library and then the benchmark step (~1,050 s of the 1,259 s of run 40243819478):
    'bench-windows-x64': 1320,  # the p90 of the runs with the benchmark step (1,320 s run 40219296140, 1,259 s run 40243819478); 169 s in run 40203151892 had builds only
    'local-bindings': 300, 'local-packaging': 300, 'local-sanitizers': 900, 'local-fuzz': 300, 'local-real-model': 1200,
    'local-reproducibility': 1200, 'local-browser-wasm': 1200, 'local-browser-webgpu': 1200,
}

# --------------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------------
EVERYTHING = '*everything*'  # a classification result: the classifier, a workflow or an unknown path changed


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def categories_of(path):
    return {name for name, globs in CATEGORIES.items() if matches(path, globs)}


def touched_categories(paths):
    """The categories a list of changed paths touches; {EVERYTHING} for a `ci` path or a path in no category.
    None (unknown changes) is EVERYTHING too."""
    if paths is None:
        return {EVERYTHING}
    touched = set()
    for path in paths:
        found = categories_of(path)
        if not found or 'ci' in found:
            return {EVERYTHING}
        touched |= found
    return touched


# Categories whose only consumer is the `policy` job's checks (docs links, versioned docs): a path that is in `docs`, and in no category
# beyond these, is an input to no build, test or packaging job. A main push whose difference from a fully verified tree is such paths
# runs only the stale policy checks (scripts/ci-changes.py), as a branch push would. Anything else (an unclassified path, a workflow or
# the classifier, a docs file that a job also reads, such as a package README) keeps the full profile.
INERT_CATEGORIES = frozenset({'docs', 'policy'})


def is_inert(path):
    """Whether a changed path is documentation that no job but the policy checks reads (fail-safe: unknown paths are not)."""
    found = categories_of(path)
    return 'docs' in found and found <= INERT_CATEGORIES


def job_stale_by(categories, touched):
    """Whether a job of these categories is stale for these touched categories."""
    return EVERYTHING in touched or bool(set(categories) & touched)


def policy_checks_touched(paths, deleted=()):
    """The policy checks whose inputs a change touches; every check for unknown or `ci` changes."""
    if paths is None or EVERYTHING in touched_categories(paths):
        return list(POLICY_CHECKS)
    pseudo = [f'deleted:{path}' for path in deleted]
    return [check for check, globs in POLICY_CHECKS.items() if any(matches(path, globs) for path in list(paths) + pseudo)]


def self_check():
    """Problems in the tables (an empty list when consistent)."""
    problems = []
    referenced = {c for job in JOBS.values() for c in job['categories']}
    referenced |= {c for job in LOCAL_JOBS.values() for c in job['categories']}
    referenced |= {c for cats in VALIDATE_CHECKS.values() for c in cats}
    problems += [f'unknown category {c}' for c in sorted(referenced - set(CATEGORIES))]
    for name, job in JOBS.items():
        if job['workflow'] not in WORKFLOWS:
            problems.append(f'{name}: unknown workflow {job["workflow"]}')
        for profile in job['profiles']:
            if profile not in PROFILES:
                problems.append(f'{name}: unknown profile {profile}')
            for edge in ('needs', 'artifact_needs'):
                for other in job[edge].get(profile, []):
                    if other not in JOBS:
                        problems.append(f'{name}: {edge}[{profile}] names unknown job {other}')
                    elif profile not in JOBS[other]['profiles'] and other != 'changes':
                        problems.append(f'{name}: {edge}[{profile}] names {other}, which is not in profile {profile}')
            if set(job['artifact_needs'].get(profile, [])) - set(job['needs'].get(profile, [])):
                problems.append(f'{name}: artifact_needs[{profile}] not among its needs')
        for other in job['rebuild_with']:
            if other not in JOBS:
                problems.append(f'{name}: rebuild_with names unknown job {other}')
        if not job['categories'] and not job.get('always'):
            problems.append(f'{name}: no categories')
    for mode, platforms in SHARDS.items():
        for platform, shards in platforms.items():
            names = list(shards['jobs'])
            checks = [c for name in names for c in shards['jobs'][name]]
            if len(checks) != len(set(checks)):
                problems.append(f'SHARDS[{mode}][{platform}]: overlapping checks')
            problems += [f'SHARDS[{mode}][{platform}]: unknown check {c}' for c in checks if c not in VALIDATE_CHECKS]
            problems += [f'SHARDS[{mode}][{platform}]: side output of unknown job {n}' for n in shards['side_outputs'] if n not in names]
            problems += [f'SHARDS[{mode}][{platform}]: {n} is not a {mode} job' for n in names
                         if n not in JOBS or (mode if mode == 'candidate' else 'real_model') not in JOBS[n]['profiles']]
            if (mode == 'candidate') != ('profile' in shards):
                problems.append(f'SHARDS[{mode}][{platform}]: profile only in the candidate mode')
    for check in POLICY_CHECKS:
        if not POLICY_CHECKS[check]:
            problems.append(f'POLICY_CHECKS[{check}]: no inputs')
    problems += [f'PLATFORM_POLICY_CHECKS: unknown {c}' for c in PLATFORM_POLICY_CHECKS if c not in POLICY_CHECKS]
    problems += [f'JOB_BUDGETS: unknown job {name}' for name in JOB_BUDGETS if name not in JOBS and name not in LOCAL_JOBS]
    return problems


if __name__ == '__main__':
    found = self_check()
    for problem in found:
        print(f'ci_paths: {problem}', file=sys.stderr)
    print(f'ci_paths: {len(CATEGORIES)} categories, {len(POLICY_CHECKS)} policy checks, {len(JOBS)} jobs, {len(LOCAL_JOBS)} local jobs'
          + ('' if not found else f'; {len(found)} problems'))
    raise SystemExit(1 if found else 0)
