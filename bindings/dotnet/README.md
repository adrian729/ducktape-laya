# Ducktape Laya for .NET

Native Laya GGUF decisions backed by the shared native library (`libducktape_laya.so`, on Windows `ducktape_laya.dll`).
Requires .NET 10 on Linux x86-64 (0.2.0) or, from the next release, Linux arm64 and Windows x64, and the native library. The package targets `net10.0`, uses source-generated P/Invoke, and is trim/AOT compatible.
No model or runtime download occurs. Model files are application-owned resources and are not bundled.

## Library resolution

1. `$DUCKTAPE_LAYA_LIB_DIR/libducktape_laya.so`. When this variable is set it is authoritative: if that file cannot be loaded, a `DllNotFoundException` naming the path is thrown, and if it is incompatible, a `LayaException`; nothing else is tried.
2. Otherwise the copy shipped with the package: `runtimes/<rid>/native/` next to the application (the NuGet layout: `linux-x64`, `linux-arm64`, `osx-arm64`, `win-x64`), or the library file in the application directory (a flattened publish). It comes first, so an unrelated installed `libducktape_laya` cannot shadow it.
3. Then `libducktape_laya.so.1` and `libducktape_laya.so` (on macOS `libducktape_laya.1.dylib`, then `libducktape_laya.dylib`; on Windows `ducktape_laya.dll`) through the system loader. On Windows the search is limited to the assembly directory and the safe system directories (never the current directory or `PATH`).

A library must report `dtl_abi_version() == 2` and export `dtl_cancel_flag`. Without the override, an incompatible candidate is skipped for the next one; if none loads, the call fails with `DllNotFoundException`, and if only incompatible ones load, with `LayaException` (`UnsupportedPlatform`). Load failures are reported on every call, not just the first.

To place a native build into the package, set `-p:DucktapeLayaNativeLibrary=/path/to/libducktape_laya.so` when building or packing the library project. Without it, `dotnet pack` still succeeds but produces a package without the native library. `scripts/package-native.py` builds the package for the platform it runs on (`DucktapeLaya.VERSION.nupkg`, library included under its own file name, byte-reproducible), and `scripts/assemble-packages.py` merges the platforms' packages into one with every `runtimes/<rid>/native/`. The 0.2.0 package carries Linux x86-64 (`linux-x64`) only; from the next release the package carries `linux-x64`, `linux-arm64` and `win-x64`, and NuGet picks the running platform's runtime identifier.

## From a checkout

Released versions install with `dotnet add package DucktapeLaya` (the 0.2.0 package bundles the Linux x86-64 library; Linux arm64 and Windows x64 come with the next release). To use unreleased changes from main, build the native library (see the repository README), then reference the project from your application:

```sh
dotnet add MyApp.csproj reference /path/to/ducktape-laya/bindings/dotnet/DucktapeLaya/DucktapeLaya.csproj
export DUCKTAPE_LAYA_LIB_DIR=/path/to/ducktape-laya/build
dotnet run --project MyApp.csproj
```

Instead of the environment variable, build with `-p:DucktapeLayaNativeLibrary=/path/to/ducktape-laya/build/libducktape_laya.so` to copy the library into the output's `runtimes/linux-x64/native/`. The binding's tests run with `dotnet test bindings/dotnet/DucktapeLaya.slnx` and `DUCKTAPE_LAYA_LIB_DIR` set; `LAYA_MODEL` enables the real-model tests.

## Example

A fragment: `requestJson` is your request as a JSON string and `cancellationToken` any `CancellationToken`.

```csharp
using Ducktape.Laya;

using var engine = LayaEngine.Open("Laya-Q8_0.gguf", new LayaConfig { Threads = 2, ContextTokens = 1024 });
var options = new LayaCallOptions { Timeout = TimeSpan.FromSeconds(30), OnEvent = Console.WriteLine };
string result = engine.Execute(requestJson, options, cancellationToken);
```

`LayaConfig` (init-only) holds `Threads` (0 = automatic, otherwise 1..256), `ContextTokens` (prompt capacity), `Overflow` (default policy for prompts over the limit: `reject` (default), `truncate_end`, `truncate_start` or `scan`; a request's own `overflow` wins) and `Temperatures` (calibration overrides keyed like `"choice.3_5"`); unset members are omitted from the config JSON. `OpenRouter(routerJson, config)` opens a lazy local model registry in which the config applies to every route and a route's own `config` overrides it key by key. Requests name a route in `"model"`; with `"auto": {"english": "en", "default": "ml"}` in the router JSON, `"model": "auto"` chooses the route by the state's language, and `"auto"` cannot be a route ID. Per-request `max_tokens`, `temperatures`, `overflow`, `max_windows` and (with `"model": "auto"`) `lang` are ordinary request JSON keys. There is one strict request contract, `Execute`/`ExecuteAsync`; events and `RequestId` are reported through the call options. See `docs/API.md` for request, schema and router contracts.

## Threading and cancellation

A `LayaEngine` may be shared across threads; native calls on one handle serialize. `Dispose` is idempotent and waits for in-flight calls. Calls made once `Dispose` has started, even while it still waits, throw `ObjectDisposedException` at once. Disposed from inside its own event callback, the engine returns at once and is released when the running call finishes. A finalizer releases the native handle if an engine is never disposed, but it is only a safety net: always dispose (`using`) so memory is freed promptly.

Inference blocks. `ExecuteAsync` runs the call on a thread-pool thread, which stays occupied for the whole inference; the options (`RequestId`, `Timeout`, `OnEvent`) are snapshotted when the method is called. `OnEvent` runs on the calling thread for sync calls and on that pool thread for `ExecuteAsync`; exceptions it throws are swallowed and never change results.

Cancellation is cooperative and no partial result is returned. The native cancel check is `dtl_cancel_flag` reading a per-call flag, so backend worker threads never call into managed code. A cancelled `CancellationToken` throws `OperationCanceledException` (inner `LayaException` with status 5; async calls return a canceled task). An exceeded `Timeout` throws `LayaException` with status 6 (`LayaStatus.DeadlineExceeded`).

## Errors

Argument mistakes the binding validates itself use standard .NET exceptions: `ArgumentNullException` for null strings, `ArgumentOutOfRangeException` for `Threads` outside 0..256, a non-positive `ContextTokens`, a non-finite or non-positive temperature, and for a `Timeout` that is negative or over 24 hours (sub-millisecond values round up to 1 ms). Everything else is a `LayaException` carrying the native `Status`, including inputs containing NUL characters (status 1). Values the native side validates (for example an unknown `Overflow` policy, `ContextTokens` below 32 or above the model's trained length, unknown temperature keys, unknown request keys) are a `LayaException` with status 1.
