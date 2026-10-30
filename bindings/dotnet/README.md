# Ducktape Laya for .NET

Local [Laya](https://github.com/NandhaKishorM/laya) decisions from .NET: ask named questions about a text (`choice` picks one of several options, `noul` gives the probability that a statement is true, `score` picks a level on an ordered scale) and get probabilities, computed on the CPU by the shared native library bundled in the package.

```sh
dotnet add package DucktapeLaya
```

Requires .NET 10. The package targets `net10.0`, uses source-generated P/Invoke, and is trim/AOT compatible. It bundles the native library for Linux x86-64, Linux arm64, macOS arm64, macOS x86-64 and Windows x64 (`linux-x64`, `linux-arm64`, `osx-arm64`, `osx-x64`, `win-x64`; 0.2.0: Linux x86-64 only; Windows needs the Microsoft Visual C++ Redistributable); NuGet picks the running platform's runtime identifier ([platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md)). No model or runtime download occurs; download the model separately ([models](https://github.com/adrian729/ducktape-laya/blob/main/docs/MODELS.md)).

## Library resolution

1. `$DUCKTAPE_LAYA_LIBRARY`, the absolute path of the library file, else `$DUCKTAPE_LAYA_LIB_DIR/` (an absolute directory) plus the platform's file name (`libducktape_laya.so`, `libducktape_laya.dylib` or `ducktape_laya.dll`). When either variable is set it is authoritative: that file is loaded or the call fails; nothing else is tried. A relative path is a load error.
2. Otherwise the copy shipped with the package: `runtimes/<rid>/native/` next to the application (the NuGet layout: `linux-x64`, `linux-arm64`, `osx-arm64`, `osx-x64`, `win-x64`; released packages carry all five), or the library file in the application directory (a flattened publish). It comes first, so an unrelated installed `libducktape_laya` cannot shadow it, and when present it must load: a bundled copy that cannot be loaded is an error naming it and the operating system's reason (on Linux typically a glibc older than 2.38, on Windows a missing Visual C++ Redistributable). On Alpine and other musl systems the bundled glibc copy is skipped.
3. Then `libducktape_laya.so.1` and `libducktape_laya.so` (on Windows `ducktape_laya.dll`) through the system loader. On macOS no bare name is ever loaded: the absolute paths `/opt/homebrew/lib` then `/usr/local/lib` are tried, `libducktape_laya.1.dylib` before `libducktape_laya.dylib`, and every macOS load is checked with `dladdr`: a library that `DYLD_LIBRARY_PATH` swapped in is refused with status 7. On Windows the search is limited to the assembly directory and the safe system directories (never the current directory or `PATH`); on Linux it is the dynamic loader's search (`LD_LIBRARY_PATH`, the cache and the system directories, never the current directory).

A library must report `dtl_abi_version() == 2`, export `dtl_cancel_flag` and report a `package_version` (from `dtl_info`) equal to the assembly's own version, so a library of another release found elsewhere is refused. Every load failure, a missing library, one that cannot be loaded, one of another ABI or version and a relative override, throws `LayaLoadException` (a `LayaException` with `Status` `Status.UnsupportedPlatform`, 7); the loader's own exception, if any, is its `InnerException`. On musl without a musl build in step 3 the message says that the bundled library needs glibc. A failure is not cached: the next call tries again.

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

using var engine = Engine.Open("Laya-Q8_0.gguf", new Config { Threads = 2, ContextTokens = 1024 });
var options = new CallOptions { Timeout = TimeSpan.FromSeconds(30), OnEvent = e => Console.WriteLine($"{e.Kind} {e.Status} {e.ElapsedMs} ms") };
string result = engine.Execute(requestJson, options, cancellationToken);
```

`Config` (init-only) holds `Threads` (0 = automatic: at most 4 and at most the CPUs the process may use; otherwise 1..256), `ContextTokens` (prompt capacity), `Overflow` (default policy for prompts over the limit: `reject` (default), `truncate_end`, `truncate_start` or `scan`; a request's own `overflow` wins), `MaxWindowsPerCall` (the most forward passes one call may plan on the engine, 1..4096) and `Temperatures` (calibration overrides keyed like `"choice.3_5"`). A null member is unset and omitted from the config JSON; any other value, `0` included, is sent as given and the library validates it (status 1). `OpenRouter(routerJson, config)` or `OpenRouter(RouterConfig, config)` opens a lazy local model registry in which the config applies to every route and a route's own `config` overrides it key by key (`MaxWindowsPerCall` belongs to the shared config only). A `RouterConfig` has `Models` (route id to `Route { Path, Config }`), `MaxLoaded` and `Auto` (`AutoRoutes { English, Default }`). Requests name a route in `"model"`; with an `Auto` entry (`"auto": {"english": "en", "default": "ml"}` in the router JSON), `"model": "auto"` chooses the route by the state's language, and `"auto"` cannot be a route ID. Paths are `string`, .NET's path type. Per-request `max_tokens`, `temperatures`, `overflow`, `max_windows` and (with `"model": "auto"`) `lang` are ordinary request JSON keys. There is one strict request contract, `Execute`/`ExecuteAsync`; `CallOptions` carries `RequestId` (`ulong`, default 0, reported only in events), `Timeout` and `OnEvent`. `OnEvent` is an `Action<Event>`: `Event` is a record `(EventKind Kind, ulong RequestId, Status Status, double ElapsedMs)` and `EventKind` is `Start`, `End` or `Error` (`ElapsedMs` is 0 on `Start`; an event that fails to parse is dropped). See the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md) for request, schema and router contracts.

## Threading and cancellation

An `Engine` may be shared across threads; native calls on one handle serialize. `Close(bool cancelPending = false)` is idempotent and waits for in-flight calls; `Dispose()` is `Close(false)`. With `cancelPending: true`, every call that has entered and not yet returned (running, or waiting for the engine's native lock) is cancelled first, through a flag of its own, so it fails like a cancellation through its caller's token (`OperationCanceledException` with an inner `LayaException`, status 5) and a token shared through the call is never cancelled by it. Calls made once `Close` has started, even while it still waits, throw `EngineClosedException` at once (an `ObjectDisposedException`, the .NET convention, that also carries `Status` 1 like the other bindings). Closed from inside its own event callback (which cancels every running call, its own included, when `cancelPending` is true), the engine returns at once and is released when the running call finishes. A finalizer releases the native handle if an engine is never disposed, but it is only a safety net: always dispose (`using`) so memory is freed promptly.

Inference blocks. `ExecuteAsync` runs the call on a thread-pool thread, which stays occupied for the whole inference; the options (`RequestId`, `Timeout`, `OnEvent`) are snapshotted when the method is called, and the deadline counts from that call. `OnEvent` runs on the calling thread for sync calls and on that pool thread for `ExecuteAsync`; exceptions it throws are swallowed and never change results.

Cancellation is cooperative and no partial result is returned. The native cancel check is `dtl_cancel_flag` reading a per-call flag that the token and `Close` both set, so backend worker threads never call into managed code. A token that is already cancelled throws `OperationCanceledException` at once, with no native call and no events; one cancelled during the call ends it with `OperationCanceledException` (inner `LayaException` with status 5); async calls return a canceled task. `Timeout` is a `TimeSpan?` measured from the call's entry (`ExecuteAsync`: from its own call), with no upper bound; null, zero and `Timeout.InfiniteTimeSpan` mean none, and it is rounded up to whole milliseconds. An exceeded `Timeout` throws `LayaException` with status 6 (`Status.DeadlineExceeded`); a deadline already past when the native call would start fails at once the same way, with no native call and no events.

## Errors

Every native failure is a `LayaException` whose `Status` is the `Status` enum. Subclasses: `LayaLoadException` (library missing, unloadable or mismatched; status 7), `LayaArgumentException : ArgumentException` (an argument the binding cannot convert: a NUL character or invalid Unicode in a path, request or config string, a non-finite temperature; status 1) and `EngineClosedException : ObjectDisposedException` (status 1). `ArgumentNullException` is thrown for null strings and `ArgumentOutOfRangeException` for a negative `Timeout` other than `Timeout.InfiniteTimeSpan` (sub-millisecond values round up to 1 ms). The binding does not validate configuration: values the native side validates (thread count, `ContextTokens`, an unknown `Overflow` policy, `MaxWindowsPerCall`, unknown or non-positive temperatures, unknown request keys) are a `LayaException` with status 1.

Order of checks in a call: closed engine, then argument conversions, then an already-cancelled token, then an expired deadline, then the native call. When opening, conversions come before the library loads.
