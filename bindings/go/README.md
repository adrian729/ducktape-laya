# Ducktape Laya for Go

Local [Laya](https://github.com/NandhaKishorM/laya) decisions from Go: ask named questions about a text (`choice` picks one of several options, `noul` gives the probability that a statement is true, `score` picks a level on an ordered scale) and get calibrated probabilities, computed on the CPU by the shared native library through cgo.

```sh
go get github.com/adrian729/ducktape-laya/bindings/go@v0.2.0
```

Requires Go 1.22+ (CI tests 1.27), a C compiler, and the native library and header from the [v0.2.0 release archive](https://github.com/adrian729/ducktape-laya/releases/tag/v0.2.0) (Linux x86-64; the v0.1.0 archive has ABI 1 and does not work with this binding). From the next release, Linux arm64 and Windows x64 archives are published too ([Windows](#windows)); other platforms build the library [from a checkout](#from-a-checkout). Platform requirements: [platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md). No model or runtime download occurs; download the model separately ([models](https://github.com/adrian729/ducktape-laya/blob/main/docs/MODELS.md)).

The package links `-lducktape_laya` and includes `<ducktape_laya.h>`, so tell cgo where they are:

```sh
export CGO_CFLAGS=-I/opt/ducktape-laya/include
export CGO_LDFLAGS=-L/opt/ducktape-laya/lib
export LD_LIBRARY_PATH=/opt/ducktape-laya/lib   # or bundle the library next to your binary with an rpath
```

Alternatively expose `ducktape-laya.pc` through `PKG_CONFIG_PATH` and build with `-tags pkgconfig`.

```go
package main

import (
	"fmt"
	"log"

	laya "github.com/adrian729/ducktape-laya/bindings/go"
)

func main() {
	engine, err := laya.Open("Laya-Q8_0.gguf", &laya.Config{Threads: 2})
	if err != nil {
		log.Fatal(err)
	}
	defer engine.Close()
	out, err := engine.Execute(`{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}`)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(out)
}
```

Inference is blocking, so call it from goroutines. An `*Engine` is safe for concurrent use; native calls on one handle serialize.

`Close` rejects new calls with `laya.ErrClosed` (status 1), waits for all in-flight calls, releases the handle, and is idempotent; concurrent `Close` calls all return after release. Never call `Close` from inside an `OnEvent` callback of the same engine, because it would wait for its own call and deadlock. Use the non-blocking `CloseDeferred()` there: it marks the engine closed and the last running call releases the handle.

A finalizer releases the native handle if an `Engine` is dropped without `Close`; it is only a safety net, so still call `Close` for prompt release.

`Open(path, config)` and `OpenRouter(routerJSON, config)` take an optional `*laya.Config` (nil means defaults; zero fields are omitted): `Threads` is 0 for automatic (capped at 4) or 1..256, `ContextTokens` is the prompt capacity (default 512, at most the model's trained length), `Overflow` is the default policy for prompts over the limit (`"reject"` (default), `"truncate_end"`, `"truncate_start"` or `"scan"`; a request's own `overflow` wins), and `Temperatures` overrides calibration temperatures such as `"choice.3_5"` or `"noul"` (finite and > 0). Invalid values fail with status 1. For a router the config applies to every route and a route's own `config` overrides it key by key. Requests accept optional `max_tokens`, `temperatures`, `overflow`, `max_windows` and (with `"model": "auto"`) `lang` keys; there is a single strict request contract (`Execute`), and unknown top-level keys are rejected.

`ExecuteContext` returns an `*laya.Error` that matches `context.Canceled` (status 5) or `context.DeadlineExceeded` (status 6) with `errors.Is`; an already-done context returns at once with no native call. Deadlines beyond the native 24 hour maximum are enforced by the context watcher instead of being clamped. Cancellation is cooperative and returns no partial result. `CallOptions.OnEvent` receives events synchronously on the calling thread; panics in it are contained.

`OpenRouter` opens a bounded lazy model registry (see the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md)). Requests name a route in `"model"`; with `"auto": {"english": "en", "default": "ml"}` in the router JSON, `"model": "auto"` chooses the route by the state's language, and `"auto"` cannot be a route ID.

Errors are `*laya.Error` with a `Status` matching `ducktape_laya.h`. `Open` and `OpenRouter` also check that the library speaks C ABI 2 and fail with status 7 otherwise; in practice a program built against the 0.2 header needs `libducktape_laya.so.1` and fails to start when only the 0.1.0 library (`libducktape_laya.so.0`) is installed.

## From a checkout

To use unreleased changes, build the native library ([building](https://github.com/adrian729/ducktape-laya/blob/main/docs/BUILDING.md)), then require the module from your own `go.mod` with a `replace` directive pointing at the checkout:

```sh
go mod edit -require=github.com/adrian729/ducktape-laya/bindings/go@v0.0.0 \
  -replace=github.com/adrian729/ducktape-laya/bindings/go=/path/to/ducktape-laya/bindings/go
export CGO_CFLAGS=-I/path/to/ducktape-laya/include
export CGO_LDFLAGS=-L/path/to/ducktape-laya/build
export LD_LIBRARY_PATH=/path/to/ducktape-laya/build
go build ./...
```

To run the binding's own tests, use the same variables in `bindings/go` and set `LAYA_MODEL` for the real-model tests: `go test ./...`.

## Windows

Windows x64 is a tier-2 platform, tested in CI (Go 1.27, MinGW-w64 gcc 15). From the next release use the `ducktape-laya-V-windows-x64.zip` release asset: its `lib` directory holds the import library `ducktape_laya.lib`, `bin` the DLL and `include` the header. Before that, build the library from a checkout with clang-cl ([building](https://github.com/adrian729/ducktape-laya/blob/main/docs/BUILDING.md#build-the-library)); it produces `ducktape_laya.dll` and `ducktape_laya.lib` in the build directory.

cgo on Windows needs a MinGW-w64 gcc on `PATH` (for example from [MinGW-Builds](https://github.com/niXman/mingw-builds-binaries) or MSYS2). With `-L<build>`, its linker finds `ducktape_laya.lib` for `-lducktape_laya`; no per-OS flags are needed in your code. At run time Windows looks for `ducktape_laya.dll` next to the executable or on `PATH`, so ship the DLL beside your program. In PowerShell:

```powershell
$env:CGO_CFLAGS = "-IC:\path\to\ducktape-laya\include"
$env:CGO_LDFLAGS = "-LC:\path\to\ducktape-laya\build"
$env:PATH = "C:\path\to\ducktape-laya\build;$env:PATH"
go test ./...
```

With the zip, use `<zip>\include` for `CGO_CFLAGS`, `<zip>\lib` for `CGO_LDFLAGS` and `<zip>\bin` on `PATH`.

The DLL uses the Microsoft C++ runtime (`/MD`), so the target machine needs the Microsoft Visual C++ Redistributable, which is almost always installed.
