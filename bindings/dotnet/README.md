# Ducktape Laya for .NET

Local [Laya](https://github.com/NandhaKishorM/laya) decisions from .NET: ask named questions about a text (`choice` picks one of several options, `noul` gives the probability that a statement is true, `score` picks a level on an ordered scale) and get calibrated probabilities, computed on the CPU by the shared native library bundled in the package.

```sh
dotnet add package DucktapeLaya
```

Requires .NET 10. The package targets `net10.0`, uses source-generated P/Invoke, and is trim/AOT compatible. It bundles the native library for Linux x86-64 (0.2.0), and from the next release also Linux arm64 and Windows x64 (`linux-x64`, `linux-arm64`, `win-x64`; Windows needs the Microsoft Visual C++ Redistributable); NuGet picks the running platform's runtime identifier ([platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md)). No model or runtime download occurs; download the model separately ([models](https://github.com/adrian729/ducktape-laya/blob/main/docs/MODELS.md)).

## Library resolution

1. `$DUCKTAPE_LAYA_LIB_DIR/` plus the platform's file name (`libducktape_laya.so`, `libducktape_laya.dylib` or `ducktape_laya.dll`). When this variable is set it is authoritative: that file is loaded or the call fails; nothing else is tried.
2. Otherwise the copy shipped with the package: `runtimes/<rid>/native/` next to the application (the NuGet layout: `linux-x64`, `linux-arm64`, `osx-arm64`, `win-x64`; released packages carry `linux-x64`, `linux-arm64` and `win-x64`), or the library file in the application directory (a flattened publish). It comes first, so an unrelated installed `libducktape_laya` cannot shadow it, and when present it must load: a bundled copy that cannot be loaded is an error naming it and the operating system's reason (on Linux typically a glibc older than 2.38, on Windows a missing Visual C++ Redistributable). On Alpine and other musl systems the bundled glibc copy is skipped.
3. Then `libducktape_laya.so.1` and `libducktape_laya.so` (on macOS `libducktape_laya.1.dylib`, then `libducktape_laya.dylib`; on Windows `ducktape_laya.dll`) through the system loader. On Windows the search is limited to the assembly directory and the safe system directories (never the current directory or `PATH`); on Linux it is the dynamic loader's search (`LD_LIBRARY_PATH`, the cache and the system directories, never the current directory).

A library must report `dtl_abi_version() == 2` and export `dtl_cancel_flag`. Every load failure, a missing library, one that cannot be loaded and one of another ABI, throws `LayaException` with `Status` 7 (`LayaStatus.UnsupportedPlatform`); the loader's own exception, if any, is its `InnerException`. On musl without a musl build in step 3 the message says that the bundled library needs glibc. Load failures are reported on every call, not just the first.

To place a native build into the package, set `-p:DucktapeLayaNativeLibrary=/path/to/libducktape_laya.so` when building or packing the library project. Without it, `dotnet pack` still succeeds but produces a package without the native library. The release package is built by `scripts/package-native.py` ([building](https://github.com/adrian729/ducktape-laya/blob/main/docs/BUILDING.md#build-the-packages)).

## From a checkout

To use unreleased changes, build the native library ([building](https://github.com/adrian729/ducktape-laya/blob/main/docs/BUILDING.md)), then reference the project from your application:

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

`LayaConfig` (init-only) holds `Threads` (0 = automatic: at most 4 and at most the CPUs the process may use; otherwise 1..256), `ContextTokens` (prompt capacity), `Overflow` (default policy for prompts over the limit: `reject` (default), `truncate_end`, `truncate_start` or `scan`; a request's own `overflow` wins) and `Temperatures` (calibration overrides keyed like `"choice.3_5"`); unset members are omitted from the config JSON. `OpenRouter(routerJson, config)` opens a lazy local model registry in which the config applies to every route and a route's own `config` overrides it key by key. Requests name a route in `"model"`; with `"auto": {"english": "en", "default": "ml"}` in the router JSON, `"model": "auto"` chooses the route by the state's language, and `"auto"` cannot be a route ID. Per-request `max_tokens`, `temperatures`, `overflow`, `max_windows` and (with `"model": "auto"`) `lang` are ordinary request JSON keys. There is one strict request contract, `Execute`/`ExecuteAsync`; events and `RequestId` are reported through the call options. See the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md) for request, schema and router contracts.

## Threading and cancellation

A `LayaEngine` may be shared across threads; native calls on one handle serialize. `Dispose` is idempotent and waits for in-flight calls. Calls made once `Dispose` has started, even while it still waits, throw `ObjectDisposedException` at once (the .NET convention; the other bindings report a closed engine as status 1). Disposed from inside its own event callback, the engine returns at once and is released when the running call finishes. A finalizer releases the native handle if an engine is never disposed, but it is only a safety net: always dispose (`using`) so memory is freed promptly.

Inference blocks. `ExecuteAsync` runs the call on a thread-pool thread, which stays occupied for the whole inference; the options (`RequestId`, `Timeout`, `OnEvent`) are snapshotted when the method is called. `OnEvent` runs on the calling thread for sync calls and on that pool thread for `ExecuteAsync`; exceptions it throws are swallowed and never change results.

Cancellation is cooperative and no partial result is returned. The native cancel check is `dtl_cancel_flag` reading a per-call flag, so backend worker threads never call into managed code. A cancelled `CancellationToken` throws `OperationCanceledException` (inner `LayaException` with status 5; async calls return a canceled task). An exceeded `Timeout` throws `LayaException` with status 6 (`LayaStatus.DeadlineExceeded`).

## Errors

Argument mistakes the binding validates itself use standard .NET exceptions: `ArgumentNullException` for null strings, `ArgumentOutOfRangeException` for `Threads` outside 0..256, a non-positive `ContextTokens`, a non-finite or non-positive temperature, and for a `Timeout` that is negative or over 24 hours (sub-millisecond values round up to 1 ms). Everything else is a `LayaException` carrying the native `Status`, including inputs containing NUL characters (status 1). Values the native side validates (for example an unknown `Overflow` policy, `ContextTokens` below 32 or above the model's maximum (`trained_context_tokens`, 8,192 for the shipped files), unknown temperature keys, unknown request keys) are a `LayaException` with status 1.
