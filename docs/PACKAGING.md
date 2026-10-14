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
