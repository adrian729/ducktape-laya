# Platforms

Which operating systems, CPUs and language runtimes ducktape-laya supports, how well each is tested, and how results differ between them.

## Support tiers

A platform is supported when it is a release target in [`release/compatibility.json`](../release/compatibility.json). Each target has a tier that says how much testing backs it:

- **Tier 1** passes every release check: all three models against the original Laya and upstream llama.cpp, every binding and installed package, sanitizers, fuzzing, a three-hour soak (at least 1,000 rounds) that also serves as the one-hour stress run, performance budgets, older and newer Linux userspaces, the browser, and an upgrade-and-rollback rehearsal.
- **Tier 2** is tested on GitHub's CI runners for every release: the native tests, every binding and the installed packages (Electron included) with the English model, agreement with upstream llama.cpp and the original Laya, reproducible builds, a 5-minute stress run (at least 40 rounds), and the difference from tier-1 results. It has no soak, performance budgets, sanitizers or userspace matrix, and the multilingual and typed-decisions models are not tested on it.

The exact check list per tier is in [QUALIFICATION.md](QUALIFICATION.md#release-targets-and-tiers).

## Support matrix

| Platform | Tier | Released packages | CPU | System | Tested on |
| --- | --- | --- | --- | --- | --- |
| Linux x86-64 | 1 | 0.1.0 and later | AVX, AVX2, FMA, F16C, BMI2, SSE4.2 | glibc 2.38+, libstdc++ with `GLIBCXX_3.4.29` | Ubuntu 24.04 and 26.04 userspaces; Node 24 and 26; Electron 44; Chromium 152 for 0.1.0 and 0.2.0, Chrome 156 from 0.3.0 (browser package) |
| Linux arm64 | 2 | 0.3.0 and later | Armv8.2-A with dot product and FP16 (`asimddp`, `fphp`, `asimdhp`) | as Linux x86-64 | GitHub `ubuntu-24.04-arm`; Node 24; Electron 44 |
| Windows x64 | 2 | 0.3.0 and later | as Linux x86-64 | Microsoft Visual C++ Redistributable | GitHub `windows-2025` (Windows Server 2025); Node 24; Electron 44 |
| macOS arm64 (Apple silicon) | 2 | next release | Armv8.2-A with dot product and FP16 | macOS 14+ | GitHub `macos-15` (Xcode 16.4) |
| macOS x86-64 (Intel) | 2 | next release | as Linux x86-64 | macOS 14+ | GitHub `macos-15-intel` (Xcode 16.4) |

Platforms not listed (musl-based Linux such as Alpine, 32-bit systems, Windows on Arm, an x86-64 build run under Rosetta 2) are not supported, even where the source compiles.

**Linux.** The x86-64 features match Intel Haswell, AMD Excavator/Zen and newer; some low-power Atom, Celeron and Pentium chips lack AVX2. The arm64 baseline matches AWS Graviton2 and newer, Ampere Altra and Cortex-A76 (Raspberry Pi 5). glibc 2.38 is in Ubuntu 24.04+, Debian 13+ and Fedora 39+; only the Ubuntu releases above are tested. The npm package, wheel, jar and NuGet package bundle a glibc build. pip does not install the `manylinux` wheel on musl. npm installs the npm package there, since a package that also carries Windows cannot declare `"libc": ["glibc"]`; on musl the Node, Java and .NET loaders then report that the bundled library cannot load (status 7, a message naming glibc and musl) instead of an opaque loader error. CI checks the Node and Java messages in Alpine containers.

**Windows.** The library is `ducktape_laya.dll`, built with clang-cl (Visual Studio's LLVM). It needs the Microsoft Visual C++ Redistributable, which the packages do not bundle (almost every Windows machine has it); the bindings load the DLL by absolute path, but its Visual C++ runtime dependencies come from the standard Windows search, so install the Redistributable rather than relying on copies elsewhere. Unlike the Linux library, the DLL name carries no ABI number, so keep the DLL your program was built against; every binding checks the ABI version when it loads the library. Linking details: [PACKAGING.md](PACKAGING.md#windows).

**macOS.** Apple silicon and Intel Macs are tier-2 release targets, tested in CI on GitHub's `macos-15` and `macos-15-intel` runners; their packages need macOS 14 or newer and are in the next release, not yet in a published one ([roadmap](ROADMAP.md#macos-on-both-architectures)). Each package carries one architecture; an application for both combines the two libraries with `lipo` ([PACKAGING.md](PACKAGING.md#macos)). The Intel build needs the same x86-64 features as Linux (Intel Haswell or newer), is built for and tested on Intel hardware, and running it under Rosetta 2 is not a supported path (Rosetta does not translate AVX2). The macOS libraries use ggml's own CPU kernels: Apple's Accelerate is off, because its results depend on the macOS version and it starts threads of its own. Before the release, build from a checkout ([BUILDING.md](BUILDING.md)) and point the bindings at `libducktape_laya.dylib` with `DUCKTAPE_LAYA_LIB_DIR`. The released libraries carry only the linker's ad-hoc signature, with no Developer ID signature or notarization.

**Browser.** The browser package is experimental: 0.1.0 and 0.2.0 passed its release checks on Chromium 152 with one GPU (Intel Gen12LP integrated graphics), and 0.3.0 passed them on Chrome 156.0.8078.47 with the same GPU; other browsers are untested. Requirements: [BROWSER.md](BROWSER.md).

## CPU check at open

The native build uses the CPU features listed above. Before loading a model, opening an engine checks that the CPU has them (opening a router checks nothing: the check runs when a request first loads a model); if not, it fails with status 7 (`DTL_UNSUPPORTED_PLATFORM`) and names the first missing feature, and runtime info (`dtl_info` and each binding's `runtime_info`) lists all of them. No instruction from a missing feature runs first. On Windows on Arm, where the check cannot read the required features, opening fails with status 7.

The check covers the release build's configuration. A custom build with other compiler ISA flags can run instructions it does not check. AVX-512 and VNNI variants are refused when CMake configures. A portable x86-64 build turns off `GGML_AVX`, `GGML_AVX2`, `GGML_FMA`, `GGML_F16C`, `GGML_BMI2` and `GGML_SSE42`; it is a different numerical profile (below) and is not qualified. The x86-64 check is tested on emulated CPUs (QEMU) only; older physical CPUs and the arm64, Windows and macOS checks have no dedicated test.

## Language runtimes

| Binding | Declared minimum | Tested in CI |
| --- | --- | --- |
| Node.js | 20 | 24 and 26 (Linux x86-64), 24 (arm64, Windows) |
| Electron | — | 44, as a directory and from ASAR |
| Python | 3.10 | the Ubuntu 24.04 runner's system Python |
| Rust | 1.98 | 1.98 |
| Go | 1.22, with cgo | 1.27 (on Windows with the runner image's MinGW-w64 gcc, not pinned) |
| Java | 22 (FFM API) | 25 |
| .NET | 10 | 10 |
| C/C++ | a C or C++ compiler; CMake 3.22+ or pkg-config to link | gcc and clang on Linux, MSVC against the Windows DLL |

The older declared minimums (Node 20, Python 3.10, Go 1.22, Java 22) are not tested.

## Numerical results across platforms

Each build has a numerical profile: the backend and CPU features it computes with, reported as `numerical_profile.id` by runtime and model info. Within one profile, results are deterministic, and every binding of the same native library returns identical results for the same request text. Between profiles, probabilities differ slightly and a close decision could change, so qualify application thresholds on the platforms you deploy. Results with different `threads` values are not compared by any check.

Measured differences from Linux x86-64, English model, on the shared fixture corpus (CI candidate run for 0.3.0; macOS from a pre-check run on 2026-10-27 over 29 answers on `macos-15` and `macos-15-intel`, not yet a candidate run):

| Platform | Largest probability difference | Changed decisions |
| --- | --- | --- |
| Linux arm64 | 0.0334 | none |
| Windows x64 | 0.0865 | none |
| macOS arm64 | 0.0285 | none |
| macOS x86-64 | 0.0390 | none |

Windows reports the same profile label as Linux x86-64 and runs the same ggml kernels, but its C runtime rounds some single-precision math functions differently. That moves one answer of the original-Laya comparison past the usual 0.05 limit; it is a documented, separately capped deviation ([COMPATIBILITY.md](COMPATIBILITY.md#known-deviations)).

The browser's WebAssembly and WebGPU builds are two more profiles. Each release's browser check reports their difference from native results and any changed decision, without a tolerance; these measurements are not kept in the repository.

Accuracy against the original Laya on Linux x86-64: [PERFORMANCE.md](PERFORMANCE.md#accuracy-against-the-original-laya).
