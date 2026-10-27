#!/usr/bin/env python3
"""p90 wall-time budgets in seconds, for scripts/verify.py's timing table and tests/ci-contracts.py.

BUDGETS are whole runs, JOB_BUDGETS each CI job by its exact `name:` and each local job of
scripts/verify.py: the p90 of the last 10 runs, each with a comment giving its measured basis.
They live outside scripts/ci_paths.py because no job reads a budget: a budget change makes no
job stale (this file is `tooling`), where any change to the classifier makes every job stale.

Run it to check the tables (`python3 scripts/ci_budgets.py`); tests/ci-contracts.py does too.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ci_paths import (BUILD_PLATFORMS, JOBS, LANGUAGES, LOCAL_JOBS, PACKAGE_PLATFORMS, PACKAGES, SHARDS,  # noqa: E402
                      TIER2_PLATFORMS)

BUDGETS = {
    'branch-push': 100,           # a native edit: changes + generic-linux-x64
    'docs-only': 45,              # changes + policy (docs-links, release-notes)
    'verify-ci-model-free': 300,  # verify.py --ci, every stale model-free job
    # A plain real_model=true dispatch. 480 s measured it before macOS joined native.yml; in run 40572053784 every other job
    # was done at 428 s and the last macOS job at 935 s, after up to 245 s of waiting for a macOS runner (GitHub's macOS
    # concurrency) behind bindings-real-macos-x64's 557 s: the macOS critical path now sets the wall.
    'real-model-dispatch': 960,
    'candidate': 660,             # mode=candidate; measured before macOS joined it: refresh from its first candidate run
}
JOB_BUDGETS = {
    'changes': 25, 'policy': 60, 'tooling': 120,
    'generic-linux-x64': 80, 'generic-linux-x64-clang': 80, 'generic-linux-arm64': 90, 'generic-windows-x64': 130,
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
    **{f'packages-{p}': 120 for p in PACKAGE_PLATFORMS}, 'assemble': 60,
    # 150 was the one warm run 40218330855 (128 s). The budget was raised to 180 from the p90 of the ten runs before the packages ran
    # concurrently (139 to 221 s), which measured the serial packaging this job no longer does. Concurrent packaging (27 s serial) has
    # one run so far, 40257329206: 148 s (about 85 s of setup: four SDK setups and the Visual Studio environment; 34 s of configure
    # and build, all 89 compiles compiler-cache hits; the packaging). Since then, nine successful runs: candidate mode (only the
    # packages check on the shards' build) 132, 138, 143, 172 s; verify mode (configure, build, packaging and smoke) 148, 157, 174,
    # 202 s, and 279 s in a scope=all run whose 162 s main step was a cold compiler cache (warm: 71 to 103 s). Setup alone is 74 to
    # 113 s. 150 sat at the median, below every verify run but one; 205 is the p90 of the eight warm runs.
    'packages-windows-x64': 205,
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
    # macOS: no warm-cache run yet. A warm job builds what build-<P> builds, which took 52 s (arm64) and 132 s (x64) with a
    # warm compiler cache in run 40572053784; refresh from the first warm-cache run.
    'warm-macos-arm64': 60, 'warm-macos-x64': 150,
    'warm-asan': 330, 'warm-tsan': 270, 'warm-fuzz': 340,  # 322 s, 263 s and 333 s
    'cache-prune': 30,                                     # 12 s (gh api list and delete)
    # mode=experiment, two clang-cl builds of the library and then the benchmark step (~1,050 s of the 1,259 s of run 40243819478):
    'bench-windows-x64': 1320,  # the p90 of the runs with the benchmark step (1,320 s run 40219296140, 1,259 s run 40243819478); 169 s in run 40203151892 had builds only
    # macOS, from verify run 40572053784 (warm compiler and toolchain caches; one run each, so refresh from the p90 once there
    # are ten). Cold caches (run 40566157580, the first) took far longer: generic 111 s and 264 s, binding-macos-x64 431 s.
    'generic-macos-arm64': 60, 'generic-macos-x64': 110,     # 44 s and 91 s
    'binding-macos-arm64': 120, 'binding-macos-x64': 160,    # 107 s and 142 s, the six model-free suites in one job
    'packages-macos-arm64': 110, 'packages-macos-x64': 230,  # 94 s and 207 s (x64: 64 s of packaging, the rest toolchain setup)
    'assembled-macos-arm64': 70, 'assembled-macos-x64': 150,  # 58 s and 135 s
    # Shards: their checks dominate, not setup. native: the upstream reference alone took 127 s and 116 s.
    'native-macos-arm64': 310, 'native-macos-x64': 450,      # 285 s and 415 s
    'parity-a-macos-arm64': 350, 'parity-b-macos-arm64': 300,  # 317 s and 270 s
    'parity-a-macos-x64': 350, 'parity-b-macos-x64': 390,      # 323 s and 356 s
    # bindings-real-macos-x64: the six real-model suites run in parallel on 4 cores (165 to 363 s each). Halves would add a
    # job to a run already waiting up to 245 s for a macOS runner, without less runner time: kept whole.
    'bindings-real-macos-x64': 600,                          # 557 s (arm64: 193 s, inside the shard budget)
    # reproducibility: no run yet (candidate only); reproducibility-linux-arm64's 300 s until the first candidate run.
    'reproducibility-macos-arm64': 300, 'reproducibility-macos-x64': 300,
    'local-bindings': 300, 'local-packaging': 300, 'local-sanitizers': 900, 'local-fuzz': 300, 'local-real-model': 1200,
    'local-reproducibility': 1200, 'local-browser-wasm': 1200, 'local-browser-webgpu': 1200,
}


def self_check():
    """Problems in the tables (an empty list when consistent)."""
    return [f'JOB_BUDGETS: unknown job {name}' for name in JOB_BUDGETS if name not in JOBS and name not in LOCAL_JOBS]


if __name__ == '__main__':
    found = self_check()
    for problem in found:
        print(f'ci_budgets: {problem}', file=sys.stderr)
    print(f'ci_budgets: {len(BUDGETS)} run budgets, {len(JOB_BUDGETS)} job budgets' + ('' if not found else f'; {len(found)} problems'))
    raise SystemExit(1 if found else 0)
