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

engine, err := laya.Open("Laya-Q8_0.gguf", 2)
if err != nil {
	log.Fatal(err)
}
defer engine.Close()
out, err := engine.Execute(`{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}`)
```

Inference is blocking, so call it from goroutines. An `*Engine` is safe for concurrent use; native calls on one handle serialize.
`Close` waits for in-flight calls and is idempotent; later calls fail with status 1. Do not call `Close` from an `OnEvent` callback of the same engine: it would wait for its own call and deadlock.
`ExecuteContext` and `DecideContext` map context cancellation to status 5 and a deadline to the native timeout (status 6, at most 24 hours).
Cancellation is cooperative and returns no partial result. `CallOptions.OnEvent` receives observer events synchronously on the calling thread; panics in it are contained.
`OpenRouter` opens a bounded lazy model registry (see `docs/API.md`). Errors are `*laya.Error` with a `Status` matching `ducktape_laya.h`.
