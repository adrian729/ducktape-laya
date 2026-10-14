# Linux packaging

Current validated host: Linux x86-64, Pop!_OS 24.04, Clang 22.1.8.
The current CPU build requires AVX2/FMA/F16C/BMI2/SSE4.2. Artifact generation
and host consumer checks do not yet establish a minimum distro/CPU release matrix.

## Native installation

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DDUCKTAPE_BUILD_EXAMPLES=OFF
cmake --build build --parallel 3
cmake --install build --prefix "$PWD/build/staging"
python3 tests/installed-native.py build/staging /path/to/approved.gguf
```

The installed tree includes versioned shared libraries, C/C++ headers, licenses,
model/runtime identity records, relocatable CMake targets and pkg-config metadata.
Consumer CMake projects use:

```cmake
find_package(ducktape_laya 0.1 CONFIG REQUIRED)
target_link_libraries(my_application PRIVATE ducktape::laya)
```

Set `CMAKE_PREFIX_PATH` to the installation prefix. Other build systems may use
`pkg-config --cflags --libs ducktape-laya` with the installed `lib/pkgconfig` in
`PKG_CONFIG_PATH`. Applications must arrange the normal shared-library loader
path or bundle the library beside their own executable with an appropriate
relative RPATH. No inference server or Python is required at runtime.

`release/compatibility.json` owns the package version and approved runtime pin.
`python3 scripts/check-release.py` checks the runtime checkout/gitlink, model
compatibility and language package versions. `scripts/verify-model.py` verifies
local weights against an explicitly selectable immutable manifest. These checks
perform no downloads. The current model manifest identifies the exact GGUF;
its original source-checkpoint revision remains unknown rather than inferred.

Package publication, signing and broad production readiness remain separate
release gates. This repository has not published packages as part of these checks.

## npm and Rust artifacts

```sh
# Build with DUCKTAPE_BUILD_NODE=ON and NODE_INCLUDE_DIR first.
python3 scripts/package-linux.py --build build --output build/packages-0.1.0
python3 tests/installed-packages.py build/packages-0.1.0 /path/to/approved.gguf
```

The output contains a native installation tarball, an npm tarball with its own
Node-API addon/shared library/worker/types, two normalized `.crate` files, and
an inventory with SHA-256 checksums and source/runtime identity. Existing output
directories are rejected. No script publishes anything or downloads a model.

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
node --experimental-vm-modules --test tests/browser-backend.mjs
```

The npm package selects its bundled Linux x64 addon. Source-checkout users must
supply `addonPath` or `DUCKTAPE_LAYA_ADDON`; installed users need neither. For
Electron, unpack `node_modules/ducktape-laya` in the application's packager
configuration (for example `asarUnpack: ["**/node_modules/ducktape-laya/**"]`
in electron-builder). This includes the worker and native dependencies. The addon
resolver recognizes the physical `.asar.unpacked` native directory. Linux
Electron 44.7.2 (Node 24.21.0) passed real-model inference both from an ordinary
application directory and from ASAR with this unpacking policy. No window or
renderer integration is required by the library.

Rust packages contain library source, metadata and license, excluding demos.
The sys crate locates an installed native library using `DUCKTAPE_LAYA_LIB_DIR`,
or `pkg-config` when the variable is absent. It no longer assumes a monorepo
`build/` directory. Crates express their sibling dependency by version in the
published manifest. Before publication, the artifact consumer test uses a local
Cargo patch to the extracted sys crate, never a path into this repository.

Validated locally: installed C/C++, pkg-config, npm and Rust artifact consumers
perform real-model inference outside the checkout, with npm/Rust agreeing
exactly. This is an installation test on the current host, not a clean-container
or broad platform-matrix certification. Local artifacts remain unsigned and
can include explicitly recorded dirty development sources.

Native tarballs now normalize ownership, timestamps, ordering and gzip headers.
`SOURCE_DATE_EPOCH` defaults to the source commit timestamp. Two packaging runs
from identical build/source inputs produced byte-identical native, npm and Rust
artifacts, and the resulting artifacts passed installed-consumer checks again.
That check establishes packaging reproducibility. Independent compilation is
now checked separately as described below. The inventory records actual CMake ISA options, compiler flags
and required GLIBC/GLIBCXX/CXXABI symbol versions instead of assuming them.
The current binary requires symbols through GLIBC 2.38 and GLIBCXX 3.4.29;
that is a loader requirement, not evidence of testing every compatible distro.

The reproducible Electron integration consumer lives under `tests/electron`:

```sh
npm ci --prefix tests/electron
node tests/electron/node_modules/electron/install.js
node tests/electron/run.cjs build/packages-0.1.0/ducktape-laya-0.1.0.tgz /path/to/approved.gguf
```

Only the test setup downloads Electron; applications do not acquire this test
dependency. The test installs the actual tarball into a temporary app, uses a
pinned ASAR packager, and removes the app after both runs. Directory/ASAR outputs
agreed exactly on the tested host. Other Electron versions remain unvalidated.

## Validated Linux userspaces

The installed native artifact also ran the three-question real-model fixture in
isolated Ubuntu Base 24.04.3 and 24.04.5 root filesystems, using each distribution's
own glibc/libstdc++. Results agreed exactly with the host. Bubblewrap supplied
read-only package/model mounts, an empty temporary filesystem and a disabled
network; the repository, Python, Node, Rust and inference servers were absent.
The C consumer was compiled from installed headers before entering the namespace.
This validates native runtime loading/inference in those userspaces, not building
all bindings there or testing a different kernel/physical CPU.

`tests/linux-userspace.py ARTIFACT_DIR MODEL ROOTFS... --report REPORT.json`
repeats this check against locally verified rootfs directories. The official
[Ubuntu Base images](https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/)
were verified against the published SHA-256 sums; exact identities and package
checksums are retained in [the report](evidence/linux-userspace-20261014.json).
Pop!_OS 24.04 on the current x86-64 AVX2 machine remains the full toolchain,
Node/Electron and browser validation host. Other architectures/distributions are
not advertised as tested.

Each packager now emits a checksum-covered CycloneDX 1.6 component inventory
(`sbom.cdx.json`). Native binaries are inspected through the C ABI before
packaging, so a stale version/runtime/patch identity is rejected. Browser packages
include Emscripten, musl, LLVM runtime and Dawn notices in addition to shared
runtime notices. The inventory separates incorporated components from host system
libraries, host browser/GPU drivers, excluded model weights and test-only tools.
Checksums detect changes; these local artifacts are unsigned and do not establish
a publisher identity. Public signing and the private security-reporting channel
must be configured before publication.

## Independent compilation check

Two separate source/build/install trees compiled the same captured inputs with
the same toolchain. The first comparison isolated a difference in the Node
addon: build-directory RPATH storage survived installation even after its visible
path had been replaced. `BUILD_RPATH_USE_ORIGIN` removes that input at link time.
After applying this one-line configuration correction and rebuilding/relinking
each independent tree, all 26 installed file/symlink records agreed exactly.
The current checkout's library and the packaged npm addon match those outputs.
[Evidence](evidence/reproducibility-20261014.json) retains the original snapshot
identity, precise correction, final hashes and scope. No objects were copied
between the two builds.

`scripts/check-reproducibility.py --output build-repro-new` repeats the test in
new trees. Source/build paths are normalized by default; generated GGML identity
uses the explicit runtime pin rather than accidentally finding the parent Git
repository. This validates identical inputs/toolchains, not different compilers
or untested targets. The native packager uses the installed addon, and installed
consumer tests reject absolute library search paths.
