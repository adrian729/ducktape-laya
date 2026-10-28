# Linking and deployment

How to link the native library into an application and ship the library and model with it. Installing a language package is covered in the [README](../README.md#install) and each package README; building from source in [BUILDING.md](BUILDING.md).

Nothing in this project downloads at run time: the library, the language packages and the model are files your application installs, and the application passes the model's path when it opens an engine. Python is never needed at run time.

## Package artifacts

<a id="npm-rust-and-python-artifacts"></a>
Each release publishes these files on [GitHub Releases](https://github.com/adrian729/ducktape-laya/releases) (0.1.0 and 0.2.0: Linux x86-64 only; from 0.3.0 also Linux arm64 and Windows x64), and the registries serve the same npm, Python, Maven and NuGet files:

| File | Contents |
| --- | --- |
| `ducktape-laya-VERSION-linux-x64.tar.gz`, `-linux-arm64.tar.gz`, `-windows-x64.zip` | The native installation: headers, the shared library (on Windows `bin/ducktape_laya.dll` and the import library `lib/ducktape_laya.lib`), CMake config, pkg-config file, the Node addon, the model manifest, `download-model.py`, licences |
| `ducktape-laya-VERSION.tgz` | npm package: the Node binding with each platform's addon and library in `native/<platform>-<arch>/` (`linux-x64`, `linux-arm64`, `win32-x64`) |
| `ducktape-laya-browser-VERSION.tgz` | npm browser package: loader, worker, WASM and WebGPU assets ([BROWSER.md](BROWSER.md)) |
| `ducktape_laya-VERSION-py3-none-TAG.whl` | Python wheel with the library in `ducktape_laya/_native/`; `TAG` is `manylinux_2_38_x86_64`, `manylinux_2_38_aarch64` or `win_amd64` |
| `ducktape-laya-VERSION.jar` (with `-sources.jar`, `-javadoc.jar`, `.pom`) | Maven jar with each platform's library in `native/<platform>/` |
| `DucktapeLaya.VERSION.nupkg` | NuGet package with each platform's library in `runtimes/<rid>/native/` (`linux-x64`, `linux-arm64`, `win-x64`) |
| `ducktape-laya-sys-VERSION.crate`, `ducktape-laya-VERSION.crate` | Rust source crates; they link the native library from the archive |
| `native-inventory.json`, `npm-browser-inventory.json`, `*-sbom.cdx.json`, `gates.json`, `SHA256SUMS` | Inventories (every file and every bundled native file with its SHA-256, build and toolchain identity, required glibc symbol versions), CycloneDX SBOMs, the qualification report and checksums |

Every file except `gates.json` and `SHA256SUMS` is attested, and the attestation records the hash of `gates.json`; [SECURITY.md](../SECURITY.md#verify-a-download) shows how to verify a file. The Go module is source only and links the native archive's library. The npm package, jar and NuGet package each carry every platform and pick the running one; on an unsupported platform they fail to load with an error that names the platform.

## C and C++

From the native archive (or an installation from source, [BUILDING.md](BUILDING.md#build-the-library)), set `CMAKE_PREFIX_PATH` to the extracted directory and link the target:

```cmake
find_package(ducktape_laya 0.3 CONFIG REQUIRED)
target_link_libraries(my_application PRIVATE ducktape::laya)
```

The CMake package accepts only the same minor version, so a 0.2.0 installation does not satisfy `0.3`, although its ABI is the same. Other build systems use `pkg-config --cflags --libs ducktape-laya` with the archive's `lib/pkgconfig` in `PKG_CONFIG_PATH`.

```cpp
#include <ducktape_laya.hpp>

auto engine = ducktape::engine::open("/absolute/path/to/Laya-Q8_0.gguf");
std::string result_json = engine.execute(request_json);
```

The C header is [ducktape_laya.h](../include/ducktape_laya.h), the C++ wrapper [ducktape_laya.hpp](../include/ducktape_laya.hpp); [examples/decide.c](../examples/decide.c) is a complete C program. Relative model paths resolve against the process's working directory, so pass an absolute path.

At run time the system loader must find the library: install it in a standard location, set `LD_LIBRARY_PATH` while developing, or ship it beside your executable with a relative RPATH such as `$ORIGIN`. For a quick shell setup with the archive extracted at `PREFIX`:

```sh
export CMAKE_PREFIX_PATH="PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export PKG_CONFIG_PATH="PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

The installed library declares its glibc and libstdc++ symbol versions in the inventory (`required_runtime_symbol_versions`); use them when choosing the oldest distribution you deploy to.

## Windows

The zip's `bin/ducktape_laya.dll` needs the Microsoft Visual C++ Redistributable (the C++ runtime is linked dynamically, `/MD`); it is not bundled. C and C++ applications link `lib/ducktape_laya.lib` through the same CMake package and ship the DLL next to their executable or put `bin` on `PATH`: Windows has no run path. MinGW-w64 toolchains (and cgo) can use `lib/pkgconfig/ducktape-laya.pc`, whose `-lducktape_laya` finds the import library. The zip's Node addon (`lib/ducktape-laya/node`) loads only with `bin` on `PATH`; use the npm package instead, which keeps the DLL next to the addon. Rust links the import library: set `DUCKTAPE_LAYA_LIB_DIR` to the zip's `lib` and put `bin` on `PATH` at run time.

## macOS

The macOS archives (`macos-arm64` and `macos-x64`, built on Apple silicon and Intel runners, macOS 14 or newer) are thin: one architecture each, and every package (npm `darwin-arm64`/`darwin-x64`, the wheels, the NuGet `osx-arm64`/`osx-x64` folders, the jar's `native/macos-arm64`/`native/macos-x64`) carries the library of its own architecture. The library's install name is `@rpath/libducktape_laya.1.dylib`, so link and ship it beside the executable with an `@loader_path` run path. An application that must run on both architectures (a universal app) merges the two libraries with `lipo` and links against the result:

```sh
lipo -create ducktape-laya-V-macos-arm64/lib/libducktape_laya.1.dylib ducktape-laya-V-macos-x64/lib/libducktape_laya.1.dylib -output universal/lib/libducktape_laya.1.dylib
lipo -info universal/lib/libducktape_laya.1.dylib   # Architectures in the fat file: x86_64 arm64
```

Merge the two archives' copies of the versioned file, keep the `libducktape_laya.dylib` link next to it (`ln -sf libducktape_laya.1.dylib universal/lib/libducktape_laya.dylib`), take `include/` and `lib/cmake` from either archive, and build the application with `-DCMAKE_OSX_ARCHITECTURES="arm64;x86_64"`. The merged file is not one of the released, attested bytes: sign your application (or at least ad-hoc sign the merged library with `codesign -s - --force`) after merging, since `lipo` invalidates the arm64 slice's signature. The released libraries carry only the linker's ad-hoc signature; there is no Developer ID signature or notarization.

## Bindings that load the library at run time

- **Node.js** selects the addon in `native/<platform>-<arch>/` for the running platform, and the addon loads the library next to it: on Linux through a `DT_RPATH` of `$ORIGIN`, which the loader searches before `LD_LIBRARY_PATH`, on Windows from the addon's directory. The addon refuses a library of another package version. From a checkout, set `DUCKTAPE_LAYA_LIBRARY` to the addon file or `DUCKTAPE_LAYA_LIB_DIR` to its directory (both absolute).
- **Python, Java and .NET** load the library bundled in their package; `DUCKTAPE_LAYA_LIBRARY` (the library file) or `DUCKTAPE_LAYA_LIB_DIR` (its directory), both absolute, selects a specific library instead and is then authoritative. Rust and Go link the library when the program is built and have no run-time override.
- A library of the same name in the current directory or on `PATH` or `LD_LIBRARY_PATH` does not replace the bundled one; CI checks this for .NET on Linux, macOS and Windows and for Node on Linux and macOS. On macOS no binding loads a bare name (dyld would search `DYLD_LIBRARY_PATH`, the run paths and the current directory); Python, .NET and Java load absolute paths only (the bundled library, `DUCKTAPE_LAYA_LIB_DIR`, then `/opt/homebrew/lib` and `/usr/local/lib`). dyld still searches `DYLD_LIBRARY_PATH` by file name even for a library loaded by full path, so after loading, each of them asks `dladdr` which file provides `dtl_abi_version` and refuses (status 7, naming `DYLD_LIBRARY_PATH`) a file other than the one it asked for; Java also extracts the bundled library under a name that carries its SHA-256 prefix, and the Node addon requires the library to sit in its own directory or two levels above it. The macOS CI checks put a same-name decoy in `DYLD_LIBRARY_PATH` and in the current directory: each binding must refuse it or never load it. Each package README gives the exact search order.
- **Rust and Go** link the native archive's library at build time (`DUCKTAPE_LAYA_LIB_DIR` or pkg-config for Rust, cgo flags or pkg-config for Go), so the program needs it at run time like any C program.

Every binding, and the C++ wrapper, checks that the library implements C ABI 2 and refuses any other. A library that is missing, cannot be loaded or implements another ABI is status 7 in every binding that loads it at run time ([errors](API.md#ownership-admission-and-errors)).

On Windows the bindings load `ducktape_laya.dll` by absolute path, but the DLL's own dependencies, the Visual C++ runtime DLLs, are found by the standard Windows search when they are not installed system-wide; install the Microsoft Visual C++ Redistributable, which the packages require, so they come from the system directory.

## Electron

Open the engine in the main process; no window or renderer integration is needed. Keep the model file and the whole `node_modules/ducktape-laya` package outside the ASAR archive, for example with electron-builder's `asarUnpack: ["**/node_modules/ducktape-laya/**"]`: the package starts its worker script from its own directory, and the native addon and library must be real files. The addon resolver maps an `app.asar` path to `app.asar.unpacked`. Electron 44 is tested both as a plain directory and from ASAR; test the Electron version you ship.

## Shipping the model

The model is not inside any package and is never downloaded implicitly. Ship the pinned file in your installer or resources, or download it in an explicit setup step with `download-model.py` (in the npm package and the native archive), which verifies the hash before putting the file in place:

```sh
python3 node_modules/ducktape-laya/download-model.py /path/to/app/resources
```

Then pass the file's absolute path to `open`. The models, their hashes and licences are in [MODELS.md](MODELS.md).

## Local packages

To try a locally built npm package (from [BUILDING.md](BUILDING.md#build-the-packages)) in an application: `npm install /path/to/ducktape-laya-VERSION.tgz`. A wheel installs with `pip install /path/to/ducktape_laya-VERSION-py3-none-TAG.whl`. For Rust, depend on the crate by path and set `DUCKTAPE_LAYA_LIB_DIR` to the built library's directory; the Python, Go, Java, .NET and Rust package READMEs have a "From a checkout" section.
