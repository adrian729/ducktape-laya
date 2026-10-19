# Ducktape Laya for .NET

Linux native Laya GGUF decisions backed by the shared `libducktape_laya.so`.
Requires .NET 10 on Linux x86-64 and the native library. The package targets `net10.0`, uses source-generated P/Invoke, and is trim/AOT compatible.
No model or runtime download occurs. Model files are application-owned resources and are not bundled.

## Library resolution

1. `$DUCKTAPE_LAYA_LIB_DIR/libducktape_laya.so`. When this variable is set it is authoritative: if that file cannot be loaded, a `DllNotFoundException` naming the path is thrown and nothing else is tried.
2. `runtimes/linux-x64/native/libducktape_laya.so` next to the application (the NuGet layout)
3. The system loader: `libducktape_laya.so`, then `libducktape_laya.so.0`

After loading, the binding requires `dtl_abi_version() == 1` and the `dtl_cancel_flag` export; an incompatible or too old library fails with `LayaException` (`UnsupportedPlatform`). Load failures are reported on every call, not just the first.

To place a native build into the package, set `-p:DucktapeLayaNativeLibrary=/path/to/libducktape_laya.so` when building or packing the library project.

## Example

```csharp
using Ducktape.Laya;

using var engine = LayaEngine.Open("Laya-Q8_0.gguf", threads: 2);
var options = new LayaCallOptions { Timeout = TimeSpan.FromSeconds(30), OnEvent = Console.WriteLine };
string result = engine.Execute(requestJson, options, cancellationToken);
```

`Decide` keeps the legacy primitive request semantics. `OpenRouter(configJson)` opens a lazy local model registry; see `docs/API.md` for request, schema and router contracts.

## Threading and cancellation

A `LayaEngine` may be shared across threads; native calls on one handle serialize. `Dispose` is idempotent, waits for in-flight calls, and later calls throw `ObjectDisposedException`. Disposed from inside its own event callback, the engine returns at once and is released when the running call finishes. A finalizer releases the native handle if an engine is never disposed, but it is only a safety net: always dispose (`using`) so memory is freed promptly.

Inference blocks. `ExecuteAsync` and `DecideAsync` run the call on a thread-pool thread, which stays occupied for the whole inference; the options (`RequestId`, `Timeout`, `OnEvent`) are snapshotted when the method is called. `OnEvent` runs on the calling thread for sync calls and on that pool thread for the Async variants; exceptions it throws are swallowed and never change results.

Cancellation is cooperative and no partial result is returned. The native cancel check is `dtl_cancel_flag` reading a per-call flag, so backend worker threads never call into managed code. A cancelled `CancellationToken` throws `OperationCanceledException` (inner `LayaException` with status 5; async calls return a canceled task). An exceeded `Timeout` throws `LayaException` with status 6 (`LayaStatus.DeadlineExceeded`).

## Errors

Argument mistakes the binding validates itself use standard .NET exceptions: `ArgumentNullException` for null strings, `ArgumentOutOfRangeException` for `threads` outside 0..256 and for a `Timeout` that is negative or over 24 hours (sub-millisecond values round up to 1 ms). Everything else is a `LayaException` carrying the native `Status`, including inputs containing NUL characters (status 1). `Decide` on a router uses strict `Execute` semantics (a `model` is required and unrelated metadata is rejected).
