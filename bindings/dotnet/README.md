# Ducktape Laya for .NET

Linux native Laya GGUF decisions backed by the shared `libducktape_laya.so`.
Requires .NET 10 on Linux x86-64 and the native library. The package targets `net10.0`, uses source-generated P/Invoke, and is trim/AOT compatible.
No model or runtime download occurs. Model files are application-owned resources and are not bundled.

## Library resolution

1. `$DUCKTAPE_LAYA_LIB_DIR/libducktape_laya.so`
2. `runtimes/linux-x64/native/libducktape_laya.so` next to the application (the NuGet layout)
3. The system loader: `libducktape_laya.so`, then `libducktape_laya.so.0`

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

A `LayaEngine` may be shared across threads; native calls on one handle serialize. `Dispose` is idempotent, waits for in-flight calls, and later calls throw `ObjectDisposedException`. Disposed from inside its own event callback, the engine returns at once and is released when the running call finishes.

Inference blocks. `ExecuteAsync` and `DecideAsync` run the call on the thread pool. A cancelled token or an exceeded `Timeout` ends the call cooperatively with a `LayaException` whose `Status` is 5 (`LayaStatus.Cancelled`) or 6 (`LayaStatus.DeadlineExceeded`); no partial result is returned.

`OnEvent` runs synchronously on the calling thread; exceptions it throws are swallowed and never change results. Native errors surface as `LayaException` with the native `Status`; inputs containing NUL characters fail with status 1.
