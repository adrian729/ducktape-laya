# Linux packaging

Native delivery currently targets Linux x86-64. The default CPU build requires
AVX2/FMA/F16C/BMI2/SSE4.2. A release must declare its CPU profile, system-library
requirements and supported userspaces; a build on one host does not establish a
minimum distribution baseline. See [COMPATIBILITY.md](COMPATIBILITY.md).

## Native installation

For an interactive development install, use `python3 scripts/setup.py` from
the source checkout. It prompts for a prefix and model directory, optionally
stages a local Node.js package, and prints project integration commands when
finished. Pass `--yes` for unattended defaults or `--yes --dry-run` to inspect
the commands. The manual native installation steps are:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DDUCKTAPE_BUILD_EXAMPLES=OFF
cmake --build build --parallel 3
cmake --install build --prefix "$PWD/build/staging"
```

After supplying the model, the optional installed-consumer check is
`python3 tests/installed-native.py build/staging /path/to/Laya-Q8_0.gguf`.

The installed tree includes versioned shared libraries, C/C++ headers, licenses,
model/runtime identity records, relocatable CMake targets and pkg-config metadata.
It also includes `share/ducktape-laya/download-model.py`. To provision a model
for an application, explicitly run the helper with the application's resource
directory, then pass the resulting file path to `dtl_open` or `ducktape::engine::open`:

```sh
python3 build/staging/share/ducktape-laya/download-model.py /path/to/app/resources
```

The helper reads the adjacent manifest and verifies the file before exposing it.
The model remains application-owned; CMake installation and the native/npm/Rust
packages do not include the weights or run a download during installation. The
npm tarball also carries `download-model.py` beside `model-manifest.json` for an
explicit setup step. Python is only needed to run this optional helper.

Consumer CMake projects use:

```cmake
find_package(ducktape_laya 0.2 CONFIG REQUIRED)
target_link_libraries(my_application PRIVATE ducktape::laya)
```

Set `CMAKE_PREFIX_PATH` to the installation prefix. Other build systems may use
`pkg-config --cflags --libs ducktape-laya` with the installed `lib/pkgconfig` in
`PKG_CONFIG_PATH`. Applications must arrange the normal shared-library loader
path or bundle the library beside their own executable with an appropriate
relative RPATH. No inference server or Python is required at runtime.
For the example prefix above, the manual shell setup is:

```sh
export CMAKE_PREFIX_PATH="$PWD/build/staging${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export PKG_CONFIG_PATH="$PWD/build/staging/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="$PWD/build/staging/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

No `PATH` change is needed. Pass the application's absolute GGUF path when
opening the engine; `LAYA_MODEL` is only a convenience for demos and tests.

`release/compatibility.json` owns the package version and approved runtime pin.
`python3 scripts/check-release.py` checks the runtime checkout/gitlink, model
compatibility and language package versions. `scripts/verify-model.py` verifies
local weights against an explicitly selectable immutable manifest. These checks
perform no downloads. The current model manifest identifies the exact GGUF;
its original source-checkpoint revision remains unknown rather than inferred.

Package publication, signing and broad production readiness remain separate
release gates. Public source availability does not imply a published package release.

## npm, Rust and Python artifacts

```sh
# Build with DUCKTAPE_BUILD_NODE=ON and NODE_INCLUDE_DIR first.
python3 scripts/package-linux.py --build build --output build/packages-0.2.0
python3 tests/installed-packages.py build/packages-0.2.0 /path/to/approved.gguf
```

The output contains a native installation tarball, an npm tarball with its own
Node-API addon/shared library/worker/types, two normalized `.crate` files, a
`manylinux_2_38` Python wheel that bundles the shared library, and an inventory with SHA-256 checksums and source/runtime identity. Existing output
directories are rejected. No script publishes anything or downloads a model.
To use the local npm tarball in an application without the wizard:

```sh
npm install --save /path/to/ducktape-laya-0.2.0.tgz
```

Then `const { Laya } = require('ducktape-laya')` and
`await Laya.open('/absolute/path/to/Laya-Q8_0.gguf')`. For Rust, install the
native library first, add the path crate under `bindings/rust/ducktape-laya`
to your `Cargo.toml`, and point `DUCKTAPE_LAYA_LIB_DIR` at the installed `lib`
directory at build time. The Rust README documents the same library ownership.

Both native and browser packaging require completed build records. CMake records
a path-independent SHA-256 manifest of the project's native sources, headers,
binding sources, build configuration, runtime patches and packaged metadata.
Changes to these inputs trigger CMake regeneration and relinking. Each native
target records its own binary hash: rebuilding only the shared library cannot
make an old Node addon pass validation. Browser records bind both asset hashes
to the source manifest and actual WASM/WebGPU backend; swapped builds are rejected.
The browser worker also verifies the runtime's backend before loading any model.

Run `cmake --build <build-directory>` after changing these inputs, including when
upgrading an older build directory without these records. Packagers reject missing
records, changed inputs and changed binaries. Fingerprints cover project build
inputs and shipped bindings; upstream llama.cpp retains its separately checked
runtime pin and audited patch identity. Build flags/toolchains remain recorded
separately. Git commit and dirty-state fields in inventory schema 2 are named
`packaging_source_commit` and `packaging_source_tracked_dirty`: they identify the
packaging checkout, while `build_source` identifies the verified build inputs.
Documentation-only commits therefore do not falsely become binary build provenance.

Model-free identity regression tests run through `scripts/validate.py`, or directly:

```sh
python3 tests/packaging-identity.py
node --experimental-vm-modules --test tests/browser-backend.mjs tests/browser-lifecycle.mjs
```

The npm package selects its bundled Linux x64 addon. Source-checkout users must
supply `addonPath` or `DUCKTAPE_LAYA_ADDON`; installed users need neither. For
Electron, unpack `node_modules/ducktape-laya` in the application's packager
configuration (for example `asarUnpack: ["**/node_modules/ducktape-laya/**"]`
in electron-builder). This includes the worker and native dependencies. The addon
resolver recognizes the physical `.asar.unpacked` native directory. Test both
ordinary-directory and ASAR installations using the intended Electron version.
No window or renderer integration is required by the library.

Rust packages contain library source, metadata and license, excluding demos.
The sys crate locates an installed native library using `DUCKTAPE_LAYA_LIB_DIR`,
or `pkg-config` when the variable is absent. It no longer assumes a monorepo
`build/` directory. Crates express their sibling dependency by version in the
published manifest. Before publication, the artifact consumer test uses a local
Cargo patch to the extracted sys crate, never a path into this repository.

Installed C/C++, pkg-config, npm, Rust and Python wheel consumers must perform real-model
inference outside the checkout and agree on outputs. Local development artifacts
are unsigned and may include explicitly recorded dirty source inputs.

Native tarballs normalize ownership, timestamps, ordering and gzip headers.
`SOURCE_DATE_EPOCH` defaults to the packaging commit timestamp. Identical
build/source inputs must produce deterministic packages; independent compilation
is checked separately below. Inventories record CMake ISA options, compiler flags
and required GLIBC/GLIBCXX/CXXABI symbol versions from the actual library.
Use those requirements when selecting a deployment baseline.

The reproducible Electron integration consumer lives under `tests/electron`:

```sh
npm ci --prefix tests/electron
node tests/electron/node_modules/electron/install.js
node tests/electron/run.cjs build/packages-0.2.0/ducktape-laya-0.2.0.tgz /path/to/approved.gguf
```

Only the test setup downloads Electron; applications do not acquire this test
dependency. The test installs the actual tarball into a temporary app, uses a
pinned ASAR packager, and removes the app after both runs. Compare both outputs
and validate every Electron version advertised by a release.

## Linux userspace checks

```sh
python3 tests/download-userspaces.py --output build-platforms
python3 tests/linux-userspace.py ARTIFACT_DIR MODEL \
  build-platforms/ubuntu-24.04.5/rootfs build-platforms/ubuntu-26.04.1/rootfs \
  --report build/userspaces.json
```

Provisioning is an explicit maintainer download. `tests/linux-userspaces.json`
pins official Ubuntu image URLs, hashes and the image-signing fingerprint. The
provisioner verifies the checksum signature with a trusted local Ubuntu keyring
and the pinned archive hash before extraction; it records image provenance.
Device entries are omitted and absolute symlinks become equivalent relative
links inside the extracted root. No system packages are installed on the host.

The consumer runner verifies the artifact inventory and approved model hash,
retains distinct records for every root (even when directory names match), and
requires exact ordered result agreement. Supply locally verified root filesystems
when using other images; missing provenance is explicit in the report. It uses Bubblewrap with
read-only artifact/model mounts, each userspace's own runtime libraries and
network access disabled. It executes an installed C consumer and compares
results. This tests userspace loading/inference, not a different host kernel,
physical CPU or building all bindings inside those distributions. Keep run
results with CI/PR artifacts and define the release matrix from those checks.

Each packager emits a checksum-covered CycloneDX 1.6 component inventory
(`sbom.cdx.json`). Native binaries are inspected through the C ABI before
packaging, so a stale version/runtime/patch identity is rejected. Browser packages
include Emscripten, musl, LLVM runtime and Dawn notices in addition to shared
runtime notices. The inventory separates incorporated components from host system
libraries, host browser/GPU drivers, excluded model weights and test-only tools.
Checksums detect changes; these local artifacts are unsigned and do not establish
a publisher identity. Configure signing and release hosting before package
publication; vulnerability reporting is defined in [SECURITY.md](../SECURITY.md).

## Independent compilation check

`scripts/check-reproducibility.py --output build-repro-new` compiles one captured
source snapshot in two independent source/build/install roots and compares all
installed native/Node bytes, modes and symlink targets. Source/build paths are
normalized; generated GGML identity uses the explicit runtime pin. Keep Node
build/install RPATHs relative to `$ORIGIN`, and package the installed addon.
Installed consumer tests reject absolute library search paths.

This check covers identical inputs and toolchains, not different compilers or
architectures. Keep its reports in CI/PR artifacts or ignored build directories.
A reproducible build is one release requirement, not a substitute for runtime,
platform or installed-consumer checks.

Native packaging rejects binaries exposing sanitizer, libFuzzer coverage or LLVM
profile instrumentation symbols before loading them. Use separate diagnostic
builds for testing and rebuild uninstrumented release artifacts for distribution.
