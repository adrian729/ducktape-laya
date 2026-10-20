# Ducktape Laya for Go

Linux x86-64 Laya GGUF decisions through cgo, backed by the shared `libducktape_laya.so`.
Requires Go 1.22+, a C compiler and the native library with `ducktape_laya.h` from the release tarball.
No model or runtime download occurs. Model files are application-owned resources.

The package links `-lducktape_laya` and includes `<ducktape_laya.h>`, so tell cgo where they are:

```sh
export CGO_CFLAGS=-I/opt/ducktape-laya/include
export CGO_LDFLAGS=-L/opt/ducktape-laya/lib
export LD_LIBRARY_PATH=/opt/ducktape-laya/lib   # or bundle the library next to your binary with an rpath
```

Alternatively expose `ducktape-laya.pc` through `PKG_CONFIG_PATH` and build with `-tags pkgconfig`.

```go
import laya "github.com/adrian729/ducktape-laya/bindings/go"

engine, err := laya.Open("Laya-Q8_0.gguf", &laya.Config{Threads: 2})
if err != nil {
	log.Fatal(err)
}
defer engine.Close()
out, err := engine.Execute(`{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}`)
```

Inference is blocking, so call it from goroutines. An `*Engine` is safe for concurrent use; native calls on one handle serialize.
`Close` rejects new calls with `laya.ErrClosed` (status 1), waits for all in-flight calls, releases the handle, and is idempotent; concurrent `Close` calls all return after release. Never call `Close` from inside an `OnEvent` callback of the same engine, because it would wait for its own call and deadlock. Use the non-blocking `CloseDeferred()` there: it marks the engine closed and the last running call releases the handle.
A finalizer releases the native handle if an `Engine` is dropped without `Close`; it is only a safety net, so still call `Close` for prompt release.
`Open(path, config)` and `OpenRouter(routerJSON, config)` take an optional `*laya.Config` (nil means defaults; zero fields are omitted): `Threads` is 0 for automatic (capped at 4) or 1..256, `ContextTokens` is the prompt capacity (default 512, at most the model's trained length), and `Temperatures` overrides calibration temperatures such as `"choice.3_5"` or `"noul"` (finite and > 0). Invalid values fail with status 1. For a router the config applies to every route and a route's own `config` overrides it key by key. Requests accept optional `max_tokens` and `temperatures` keys; there is a single strict request contract (`Execute`), and unknown top-level keys are rejected.
`ExecuteContext` returns an `*laya.Error` that matches `context.Canceled` (status 5) or `context.DeadlineExceeded` (status 6) with `errors.Is`; an already-done context returns at once with no native call. Deadlines beyond the native 24 hour maximum are enforced by the context watcher instead of being clamped.
Cancellation is cooperative and returns no partial result. `CallOptions.OnEvent` receives events synchronously on the calling thread; panics in it are contained.
`OpenRouter` opens a bounded lazy model registry (see `docs/API.md`). Errors are `*laya.Error` with a `Status` matching `ducktape_laya.h`.
