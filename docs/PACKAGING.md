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
This establishes packaging reproducibility, not yet independent-compilation
reproducibility. The inventory records actual CMake ISA options, compiler flags
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
