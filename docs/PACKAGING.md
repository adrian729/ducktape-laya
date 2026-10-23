# Packaging

Released native packages: Linux x86-64 in 0.1.0 and 0.2.0; from the next release also Linux arm64 and Windows x64 (the release targets in `release/compatibility.json`). macOS arm64 is packaged and tested in CI with the same scripts but not released yet (see [Package artifacts](#package-artifacts)). The default x86-64 CPU build requires AVX2/FMA/F16C/BMI2/SSE4.2. A release must declare its CPU profile, system-library requirements and supported userspaces; a build on one host does not establish a minimum distribution baseline. See [COMPATIBILITY.md](COMPATIBILITY.md).

## Native installation

For an interactive development install, use `python3 scripts/setup.py` from the source checkout. It prompts for a prefix and model directory, optionally stages a local Node.js package, and prints project integration commands when finished. Pass `--yes` for unattended defaults or `--yes --dry-run` to inspect the commands. The manual native installation steps are:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DDUCKTAPE_BUILD_EXAMPLES=OFF
cmake --build build --parallel 3
cmake --install build --prefix "$PWD/build/staging"
```

After supplying the model, the optional installed-consumer check is `python3 tests/installed-native.py build/staging /path/to/Laya-Q8_0.gguf`.

### Build options

| CMake option | Default | Meaning |
|---|---|---|
| `DUCKTAPE_BUILD_TESTS` | `ON` | Native test executables and CTest registration |
| `DUCKTAPE_TEST_MODEL` | empty | A local GGUF; registers the real-model tests (`real-model`, `real-features`, `real-faults`, `cpu-guard` when `qemu-x86_64` is found, ...) |
| `DUCKTAPE_REAL_TEST_TIMEOUT` | `600` | Per-test timeout in seconds for the long real-model tests; raise it for instrumented builds |
| `DUCKTAPE_BUILD_EXAMPLES` | `OFF` | Developer examples: `laya-decide`, `laya-decide-cpp` and the demo forms |
| `DUCKTAPE_BUILD_NODE` | `OFF` | The Node-API addon; supply `NODE_INCLUDE_DIR`, and on Windows `NODE_LIB` (`scripts/fetch-node-sdk.py --lib` fetches both for your Node version) |
| `DUCKTAPE_BUILD_FUZZERS` | `OFF` | Linux Clang libFuzzer target `laya-input-fuzzer`; instruments the library, never for artifacts |
| `DUCKTAPE_BUILD_REFERENCE_PROBE` | `OFF` | Test-only `laya-reference` executable linking upstream server decision processing; never packaged |
| `DUCKTAPE_REPRODUCIBLE_PATHS` | `ON` | With GCC or Clang, maps source and build paths out of compiled artifacts (`-ffile-prefix-map`) |
| `DUCKTAPE_LLAMA_SOURCE` | `third_party/llama.cpp` | The llama.cpp source to build; any other directory reports its runtime commit as `unverified-custom-source` unless `DUCKTAPE_LLAMA_REVISION` is set (upgrade tooling does this) |
| `DUCKTAPE_WASM_OPTIMIZATION` | `2` | Emscripten builds only: link optimization `0`, `1`, `2`, `3`, `s` or `z`. `scripts/build-browser.py` sets `1`, the only value browser packaging accepts |
| `DUCKTAPE_WASM_MAXIMUM_MEMORY` | `4294967296` | Emscripten builds only: WebAssembly memory ceiling in bytes, a 64 KiB multiple from 64 MiB to 4 GiB. Lower values are for memory-pressure tests; packaging accepts only 4 GiB |

The GGML ISA options (`GGML_AVX2`, `GGML_FMA`, ...) select the CPU profile; see [API.md](API.md#runtime-and-model-diagnostics). AVX-512/VNNI variants are refused at configuration.

The installed tree includes versioned shared libraries, C/C++ headers, licenses, model/runtime identity records, relocatable CMake targets and pkg-config metadata. It also includes `share/ducktape-laya/download-model.py`. To provision a model for an application, explicitly run the helper with the application's resource directory, then pass the resulting file path to `dtl_open` or `ducktape::engine::open`:

```sh
python3 build/staging/share/ducktape-laya/download-model.py /path/to/app/resources
```

The helper reads the adjacent manifest and verifies the file before exposing it. The model remains application-owned; CMake installation and the native/npm/Rust packages do not include the weights or run a download during installation. The npm tarball also carries `download-model.py` beside `model-manifest.json` for an explicit setup step. Python is only needed to run this optional helper.

Consumer CMake projects use:

```cmake
find_package(ducktape_laya 0.2 CONFIG REQUIRED)
target_link_libraries(my_application PRIVATE ducktape::laya)
```

Set `CMAKE_PREFIX_PATH` to the installation prefix. Other build systems may use `pkg-config --cflags --libs ducktape-laya` with the installed `lib/pkgconfig` in `PKG_CONFIG_PATH`. Applications must arrange the normal shared-library loader path or bundle the library beside their own executable with an appropriate relative RPATH. No inference server or Python is required at runtime. For the example prefix above, the manual shell setup is:

```sh
export CMAKE_PREFIX_PATH="$PWD/build/staging${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export PKG_CONFIG_PATH="$PWD/build/staging/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="$PWD/build/staging/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

No `PATH` change is needed. Pass the application's absolute GGUF path when opening the engine; `LAYA_MODEL` is only a convenience for demos and tests.

`release/compatibility.json` owns the package version and approved runtime pin. `python3 scripts/check-release.py` checks the runtime checkout/gitlink, model compatibility and language package versions. `scripts/verify-model.py` verifies local weights against an explicitly selectable immutable manifest. Both run from a checkout with Python 3.11+, since they import `scripts/release_support.py`; the installed `download-model.py` needs neither. These checks perform no downloads. The current model manifest identifies the exact GGUF; its original source-checkpoint revision remains unknown rather than inferred.

Package publication, signing and broad production readiness remain separate release gates. Public source availability does not imply a published package release.

## Package artifacts

`scripts/package-native.py` builds every package for the platform it runs on, in one run: `linux-x64`, `linux-arm64`, `macos-arm64` or `windows-x64`. It needs a build with the Node addon (`DUCKTAPE_BUILD_NODE=ON`), `npm` (it runs `npm pack`), Cargo with the locked dependencies already fetched (it runs `cargo package --locked --offline`), a JDK (`javac`, `javadoc`), the .NET SDK (`dotnet pack`), Python 3.11+ and, on Linux, `readelf`. There is no wheel-only mode.

Only the product build is packaged: the script refuses a build directory whose cache has a `DUCKTAPE_LLAMA_SOURCE` other than the checkout's pinned runtime, a `DUCKTAPE_LLAMA_REVISION`, a `CMAKE_BUILD_TYPE` other than `Release`, or `CMAKE_C_FLAGS`/`CMAKE_CXX_FLAGS` (and their `_RELEASE` variants) other than CMake's defaults (what `scripts/validate.py` and CI configure; on Windows `/DWIN32 /D_WINDOWS` and `/EHsc`). On Linux the shipped library and addon are stripped with `strip --strip-unneeded` (the dynamic symbol table stays; the inventory records the stripped hashes under `toolchain.strip`), `check-abi.py` runs on the stripped library, and the script requires full RELRO (`GNU_RELRO` and `BIND_NOW`); on Windows it requires Control Flow Guard (`GUARD_CF`) in the DLL and the addon. CMake builds the library, ggml and llama.cpp with `-fstack-protector-strong`, `_FORTIFY_SOURCE=3` (optimised builds), `-z relro -z now` and, on Linux arm64, `-mbranch-protection=standard`; with clang-cl `/guard:cf`.

```sh
# Build with DUCKTAPE_BUILD_NODE=ON and NODE_INCLUDE_DIR first.
python3 scripts/package-native.py --build build --output build/packages-0.2.0
python3 tests/package-smoke.py build/packages-0.2.0
python3 tests/installed-packages.py build/packages-0.2.0 /path/to/approved.gguf
```

The output holds that platform's packages, each installable on its own:

| File | Contents |
| --- | --- |
| `ducktape-laya-VERSION-PLATFORM.tar.gz` (`.zip` on Windows) | The `cmake --install` tree: headers, the shared library (on Windows `bin/ducktape_laya.dll` and the import library `lib/ducktape_laya.lib`), CMake config, pkg-config file, the Node addon, model manifest, `download-model.py`, licences |
| `ducktape-laya-VERSION.tgz` | npm package with this platform's addon and the library it links (`libducktape_laya.so.1` on Linux, one copy) in `native/<platform>-<arch>/` (`linux-x64`, `linux-arm64`, `darwin-arm64`, `win32-x64`); `os` and `cpu` name this platform |
| `ducktape-laya-sys-VERSION.crate`, `ducktape-laya-VERSION.crate` | Rust source crates (the same on every platform) |
| `ducktape_laya-VERSION-py3-none-TAG.whl` | Python wheel bundling the library in `ducktape_laya/_native/`; `TAG` is `manylinux_2_38_x86_64`, `manylinux_2_38_aarch64`, `macosx_14_0_arm64` or `win_amd64`. The Python files are the same in every wheel |
| `ducktape-laya-VERSION.jar`, `-sources.jar`, `-javadoc.jar`, `.pom` | Maven files; the jar bundles the library at `native/<platform>/` (`native/windows-x64/ducktape_laya.dll`, ...) |
| `DucktapeLaya.VERSION.nupkg` | NuGet package with the library at `runtimes/<rid>/native/` (`linux-x64`, `linux-arm64`, `osx-arm64`, `win-x64`) |
| `sbom.cdx.json`, `inventory.json` | CycloneDX inventory; SHA-256 checksums, source/runtime identity, build identities, toolchain, and `payload`: every native file inside the npm, jar, NuGet and wheel packages with its SHA-256 |

Existing output directories are rejected. No script publishes anything or downloads a model. The packager checks each binary before it ships it: on Linux the GLIBC/GLIBCXX symbol versions against the wheel tag and declared floors, on macOS the deployment target (14.0, the wheel tag), on Windows that the DLL exports exactly the `dtl_*` functions and imports no sanitizer or debug runtime. The inventory records those requirements (`required_runtime_symbol_versions`, `minimum_macos`, `linked_libraries`).

### Multi-platform packages

A release ships one npm package, one jar and one NuGet package for all platforms. `scripts/assemble-packages.py` builds them from the per-platform outputs (one per runner), with only the Python standard library:

```sh
python3 scripts/assemble-packages.py --output build/release-packages \
  packages-linux-x64 packages-linux-arm64 packages-windows-x64
python3 tests/package-smoke.py build/release-packages
```

The result holds every platform's archive and wheel unchanged; one `ducktape-laya-VERSION.tgz` with each platform's `native/<platform>-<arch>/` and `os`/`cpu` listing all of them (npm checks the two lists separately, so an unlisted combination such as Intel macOS installs but fails at load with a clear error); one jar with each `native/<platform>/`; one NuGet package with each `runtimes/<rid>/native/`; the crates, sources and javadoc jars, POM and SBOM once; and an inventory with the sorted `platforms` and, per platform, its fragment inventory's hash, build identities, diagnostics, toolchain, archive, wheel and payload. Everything but each platform's native files must be identical in every input, compared by entries (compressed bytes differ between hosts' zlib), and each platform's native files must be its recorded payload; fragments of different commits, build sources or `SOURCE_DATE_EPOCH` are refused. The output depends only on the inputs' contents and the assembling host's zlib: sorted entries, fixed timestamps and permissions. Text is written with LF on every platform, the NuGet assembly is packed without a PDB, and CI uses the exact same JDK everywhere (javadoc output changes between patch releases), so the shared files come out the same on every runner. `tests/package-smoke.py` and `tests/installed-packages.py` take either kind of directory and test the files for the machine they run on.

A release candidate run (`native.yml` dispatched with `candidate=true`, see [RELEASE_CHECKS.md](RELEASE_CHECKS.md#release-targets-and-the-ci-candidate-run)) builds each target's output on a GitHub-hosted runner, assembles the release packages from those and installs them with the real model on every runner. The release assets are exactly the assembled packages.

CI (`.github/workflows/native.yml`) keeps each runner's output as the artifact `packages-<platform>` (`packages-linux-x64` from the gcc build, `packages-linux-arm64`, `packages-windows-x64`; `platforms.yml` keeps `packages-macos-arm64`), assembles the three in `packages-assembled`, and loads the assembled packages on every runner; with the real model each runner runs all installed consumers, and Windows the Electron consumer.

### Windows

The zip's `bin/ducktape_laya.dll` needs the Microsoft Visual C++ Redistributable (the C++ runtime is linked dynamically, `/MD`); it is not bundled. C and C++ applications link `lib/ducktape_laya.lib` through the CMake package (`find_package(ducktape_laya)`, as for Linux) and ship the DLL next to their executable or put `bin` on `PATH`; Windows has no run path. MinGW-w64 toolchains (and cgo) can use `lib/pkgconfig/ducktape-laya.pc`, whose `-lducktape_laya` finds the import library. The installed-package test builds the CMake consumers with Visual Studio's MSVC, against the clang-cl-built DLL. The archive's Node addon in `lib/ducktape-laya/node` loads only with `bin` on `PATH`; use the npm package, which keeps the DLL next to the addon. The jar extracts the DLL once per library content into the user's temporary directory and reuses it (a loaded DLL cannot be deleted at exit); elsewhere it extracts a per-process copy that is deleted at exit.

The DLL's bytes depend on the runner image, not only on the sources: it links the image's MSVC and Windows SDK libraries (UCRT, vcruntime), and two images built different, individually reproducible DLLs from one commit. Package inventories and reports therefore record `build_environment` (`ImageOS`, `ImageVersion`, `VCToolsVersion`, `WindowsSDKVersion`, `VSCMD_VER`), and the reproducibility check must run on the image of the build it validates; it rejects a reproduction from another environment. An ordinary Linux host sets none of these variables, and Linux x86-64 builds with the same compiler and Node headers matched across hosts (below).

To use the local npm tarball in an application without the wizard:

```sh
npm install --save /path/to/ducktape-laya-0.2.0.tgz
```

Then `const { Laya } = require('ducktape-laya')` and `await Laya.open('/absolute/path/to/Laya-Q8_0.gguf')`. For Rust, install the native library first, add the path crate under `bindings/rust/ducktape-laya` to your `Cargo.toml`, and point `DUCKTAPE_LAYA_LIB_DIR` at the installed `lib` directory at build time. The Rust README documents the same library ownership.

Both native and browser packaging require completed build records. CMake records a path-independent SHA-256 manifest of the project's native sources, headers, binding sources, build configuration, runtime patches and packaged metadata. Changes to these inputs trigger CMake regeneration and relinking. Each native target records its own binary hash: rebuilding only the shared library cannot make an old Node addon pass validation. Browser records bind both asset hashes to the source manifest and actual WASM/WebGPU backend; swapped builds are rejected. The browser worker also verifies the runtime's backend before loading any model.

Run `cmake --build <build-directory>` after changing these inputs, including when upgrading an older build directory without these records. Packagers reject missing records, changed inputs and changed binaries. Fingerprints cover project build inputs and shipped bindings; upstream llama.cpp retains its separately checked runtime pin and audited patch identity. Build flags/toolchains remain recorded separately. Git commit and dirty-state fields in inventory schema 2 are named `packaging_source_commit` and `packaging_source_tracked_dirty`: they identify the packaging checkout, while `build_source` identifies the verified build inputs. Documentation-only commits therefore do not falsely become binary build provenance.

Model-free identity regression tests run through `scripts/validate.py`, or directly:

```sh
python3 tests/packaging-identity.py
node --experimental-vm-modules --test tests/browser-backend.mjs tests/browser-lifecycle.mjs
```

The npm package selects the addon in `native/<platform>-<arch>/` for the running platform. Source-checkout users must supply `addonPath` or `DUCKTAPE_LAYA_ADDON`; installed users need neither. For Electron, unpack `node_modules/ducktape-laya` in the application's packager configuration (for example `asarUnpack: ["**/node_modules/ducktape-laya/**"]` in electron-builder). This includes the worker, which the package starts from its own directory, and the native files. The addon resolver recognizes the physical `.asar.unpacked` native directory. Test both ordinary-directory and ASAR installations using the intended Electron version. No window or renderer integration is required by the library.

Rust packages contain library source, metadata and license, excluding demos. The sys crate locates an installed native library using `DUCKTAPE_LAYA_LIB_DIR`, or `pkg-config` when the variable is absent. It no longer assumes a monorepo `build/` directory. Crates express their sibling dependency by version in the published manifest. Before publication, the artifact consumer test uses a local Cargo patch to the extracted sys crate, never a path into this repository.

Installed C/C++, pkg-config, npm, Rust, Python wheel, Maven, NuGet and Go consumers must perform real-model inference outside the checkout and agree exactly on outputs (`tests/installed-packages.py --report` records each consumer's output hash). Local development artifacts are unsigned and may include explicitly recorded dirty source inputs.

Native tarballs normalize ownership, timestamps, ordering and gzip headers; the Windows zip and the jar, wheel and NuGet packages normalize timestamps, ordering and permissions. `SOURCE_DATE_EPOCH` defaults to the packaging commit timestamp. Identical build/source inputs must produce deterministic packages; independent compilation is checked separately below. Inventories record CMake ISA options, compiler flags and required GLIBC/GLIBCXX/CXXABI symbol versions from the actual library. Use those requirements when selecting a deployment baseline.

The reproducible Electron integration consumer lives under `tests/electron`:

```sh
npm ci --prefix tests/electron
node tests/electron/node_modules/electron/install.js
node tests/electron/run.cjs build/packages-0.2.0/ducktape-laya-0.2.0.tgz /path/to/approved.gguf
```

Only the test setup downloads Electron; applications do not acquire this test dependency. The test installs the actual tarball into a temporary app, uses a pinned ASAR packager, and removes the app after both runs. Compare both outputs and validate every Electron version advertised by a release.

## Linux userspace checks

```sh
python3 tests/download-userspaces.py --output build-platforms
python3 tests/linux-userspace.py ARTIFACT_DIR MODEL \
  build-platforms/ubuntu-24.04.5/rootfs build-platforms/ubuntu-26.04.1/rootfs \
  --report build/userspaces.json
```

Provisioning is an explicit maintainer download. `tests/linux-userspaces.json` pins official Ubuntu image URLs, hashes and the image-signing fingerprint. The provisioner verifies the checksum signature with a trusted local Ubuntu keyring and the pinned archive hash before extraction; it records image provenance. Device entries are omitted and absolute symlinks become equivalent relative links inside the extracted root. No system packages are installed on the host.

The consumer runner verifies the artifact inventory and approved model hash, retains distinct records for every root (even when directory names match), and requires exact ordered result agreement. Supply locally verified root filesystems when using other images; missing provenance is explicit in the report. It uses Bubblewrap with read-only artifact/model mounts, each userspace's own runtime libraries and network access disabled. It executes an installed C consumer and compares results. This tests userspace loading/inference, not a different host kernel, physical CPU or building all bindings inside those distributions. Keep run results with CI/PR artifacts and define the release matrix from those checks.

Each packager emits a checksum-covered CycloneDX 1.6 component inventory (`sbom.cdx.json`). Native binaries are inspected through the C ABI before packaging, so a stale version/runtime/patch identity is rejected. Browser packages include Emscripten, musl, LLVM runtime and Dawn notices in addition to shared runtime notices. The inventory separates incorporated components from host system libraries, host browser/GPU drivers, excluded model weights and test-only tools. Checksums detect changes; these local artifacts are unsigned and do not establish a publisher identity. Configure signing and release hosting before package publication; vulnerability reporting is defined in [SECURITY.md](../SECURITY.md).

## Independent compilation check

`scripts/check-reproducibility.py --output build-repro-new` compiles one captured source snapshot in two independent source/build/install roots and compares all installed native/Node bytes, modes and symlink targets. Source/build paths are normalized; generated GGML identity uses the explicit runtime pin. Keep Node build/install RPATHs relative to `$ORIGIN`, and package the installed addon. Installed consumer tests reject absolute library search paths.

On Windows, clang-cl builds with `/Brepro` (no timestamps in objects; a content hash instead of the link time in the DLL, addon and import library), and CI runs the same check from a Visual Studio x64 developer prompt:

```bat
python scripts/check-reproducibility.py --output build-repro --cc "%CLANG%" --cxx "%CLANG%" --node-include "%NODE_INCLUDE_DIR%" --cmake-arg=-GNinja "--cmake-arg=-DNODE_LIB=%NODE_LIB%" "--cmake-arg=-DDTL_PATCH_EXECUTABLE=C:/Program Files/Git/usr/bin/patch.exe"
```

This check covers identical inputs and toolchains, not different compilers or architectures. Across hosts, the Linux x86-64 library and Node addon built by CI (`ubuntu-24.04`, gcc 13.3.0, Node 26.7.0 headers) are byte-identical to the release host's build of the same commit; the release procedure compares their hashes (`library_sha256`, `addon_sha256`) and stops when they differ. Keep its reports in CI/PR artifacts or ignored build directories. A reproducible build is one release requirement, not a substitute for runtime, platform or installed-consumer checks.

Native packaging rejects binaries exposing sanitizer, libFuzzer coverage or LLVM profile instrumentation symbols (on Windows and macOS also binaries that load a sanitizer or debug runtime) before loading them. Use separate diagnostic builds for testing and rebuild uninstrumented release artifacts for distribution.
