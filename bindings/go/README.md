# Ducktape Laya for Go

Local [Laya](https://github.com/NandhaKishorM/laya) decisions from Go: ask named questions about a text (`choice` picks one of several options, `noul` gives the probability that a statement is true, `score` picks a level on an ordered scale) and get probabilities, computed on the CPU by the shared native library through cgo.

```sh
go get github.com/adrian729/ducktape-laya/bindings/go@v0.3.0
```

Requires Go 1.22+ (CI tests 1.27), a C compiler, and the native library and header from the [v0.3.0 release archive](https://github.com/adrian729/ducktape-laya/releases/tag/v0.3.0) for Linux x86-64, Linux arm64, macOS arm64, macOS x86-64 (`-macos-arm64.tar.gz`, `-macos-x64.tar.gz`; releases from the one that promotes macOS to tier 2) or Windows x64 ([Windows](#windows); the v0.1.0 archive has ABI 1 and does not work with this binding); other platforms build the library [from a checkout](#from-a-checkout). Platform requirements: [platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md). No model or runtime download occurs; download the model separately ([models](https://github.com/adrian729/ducktape-laya/blob/main/docs/MODELS.md)).

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

`Close` rejects new calls, waits for all in-flight calls, releases the handle, and is idempotent; concurrent `Close` calls all return after release. A call on a closed engine returns a fresh `*laya.Error` (status 1) that wraps the sentinel `laya.ErrClosed`, so `errors.Is(err, laya.ErrClosed)` matches and no error value is shared. Called from inside an `OnEvent` callback of the same engine (on the callback's goroutine), `Close` does not wait for its own call: it closes the engine at once and returns, and the running call releases the handle when it returns. A callback must still not wait for another goroutine's `Close` of the same engine.

`CloseCancelPending` is `Close` after cancelling every call that has entered and not yet returned, whether it is running or waiting for the native lock: each fails with an `*laya.Error` of status 5 (`errors.Is(err, context.Canceled)`). Every call has its own native cancel flag, so a context shared between calls is never cancelled by it. Called from an `OnEvent` callback it cancels every running call, the callback's own included, and returns at once.

A finalizer releases the native handle if an `Engine` is dropped without `Close`; it is only a safety net, so still call `Close` for prompt release.

`Open(path, config)`, `OpenRouter(*laya.RouterConfig, config)` and `OpenRouterJSON(routerJSON, config)` take an optional `*laya.Config` (nil means defaults). Go's one exception to the other bindings: a zero field means unset and is not sent (elsewhere 0 is sent and rejected), and only set fields reach the native library. The fields are `Threads` (1..256; 0 is automatic: at most 4 and at most the CPUs the process may use), `ContextTokens` (the prompt capacity, default 512, at most the model's maximum, `trained_context_tokens`, 8,192 for the shipped files), `MaxWindowsPerCall` (the most forward passes one call may plan, 1..4096, default 256), `Overflow` (the default policy for prompts over the limit: `"reject"` (default), `"truncate_end"`, `"truncate_start"` or `"scan"`; a request's own `overflow` wins) and `Temperatures` (calibration temperatures such as `"choice.3_5"` or `"noul"`). The binding does not validate values: the core rejects them with status 1. It rejects only what JSON cannot carry: invalid UTF-8 in `Overflow`, a temperature key, a route id or path, or a non-finite temperature (status 1). A router takes `MaxWindowsPerCall` only in its shared config, where it bounds the whole call. Model paths reach the core as raw bytes.

A router is a `laya.RouterConfig{Models: map[string]laya.Route{"en": {Path: "en.gguf", Config: &laya.Config{...}}}, MaxLoaded: 2, Auto: &laya.AutoRoutes{English: "en", Default: "ml"}}` (`MaxLoaded` 0 keeps the library default, 1), or the same as JSON text through `OpenRouterJSON`. The config applies to every route and a route's own `Config` overrides it key by key. Requests accept optional `max_tokens`, `temperatures`, `overflow`, `max_windows` and (with `"model": "auto"`) `lang` keys; there is a single strict request contract (`Execute`), and unknown top-level keys are rejected.

The library is linked at build time, so the run-time overrides `DUCKTAPE_LAYA_LIBRARY` and `DUCKTAPE_LAYA_LIB_DIR` of the other bindings do not apply: choose the library with `CGO_LDFLAGS` and `LD_LIBRARY_PATH` (or an rpath) as above. There is no package-version check at run time either; `Open` checks the C ABI version (below).

Calls report errors in one order: closed engine, then argument conversions (NUL in the request), then an already-done context (it fails at once, with no native call and no events), then the native call. `ExecuteContext` returns an `*laya.Error` that matches `context.Canceled` (status 5) or `context.DeadlineExceeded` (status 6) with `errors.Is`. The context's deadline is the call's deadline, measured from the call's entry: the time left is passed to the core as `timeout_ms`, rounded up to whole milliseconds, and there is no upper bound; a deadline that has already passed fails at once with status 6, without a native call or events. A context watcher stays for cancellation. Cancellation is cooperative and returns no partial result.

`OpenRouter` opens a bounded lazy model registry (see the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md)). Requests name a route in `"model"`; with `"auto": {"english": "en", "default": "ml"}` in the router JSON, `"model": "auto"` chooses the route by the state's language, and `"auto"` cannot be a route ID.

`CallOptions.RequestID` (a `uint64`, default 0) is reported in events. `CallOptions.OnEvent` is a `func(laya.Event)` that receives events synchronously on the calling thread; panics in it are contained. An `Event` has `Kind` (`laya.EventStart`, `laya.EventEnd` or `laya.EventError`, an `EventKind` string), `RequestID uint64`, `Status laya.Status` and `ElapsedMs float64` (0 on start). Events contain no state, prompts, answers or paths.

Errors are `*laya.Error` with a `Status` of type `laya.Status` (`laya.StatusInvalidArgument` and the others) matching `ducktape_laya.h`. `Open` and `OpenRouter` also check that the library speaks C ABI 2 and fail with status 7 otherwise; in practice a program built against the 0.2 header needs `libducktape_laya.so.1` and fails to start when only the 0.1.0 library (`libducktape_laya.so.0`) is installed.

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

Windows x64 is a tier-2 platform, tested in CI (Go 1.27 with the runner image's MinGW-w64 gcc, which is not pinned). Use the `ducktape-laya-V-windows-x64.zip` release asset (from 0.3.0): its `lib` directory holds the import library `ducktape_laya.lib`, `bin` the DLL and `include` the header. Or build the library from a checkout with clang-cl ([building](https://github.com/adrian729/ducktape-laya/blob/main/docs/BUILDING.md#build-the-library)); it produces `ducktape_laya.dll` and `ducktape_laya.lib` in the build directory.

cgo on Windows needs a MinGW-w64 gcc on `PATH` (for example from [MinGW-Builds](https://github.com/niXman/mingw-builds-binaries) or MSYS2). With `-L<build>`, its linker finds `ducktape_laya.lib` for `-lducktape_laya`; no per-OS flags are needed in your code. At run time Windows looks for `ducktape_laya.dll` next to the executable or on `PATH`, so ship the DLL beside your program. In PowerShell:

```powershell
$env:CGO_CFLAGS = "-IC:\path\to\ducktape-laya\include"
$env:CGO_LDFLAGS = "-LC:\path\to\ducktape-laya\build"
$env:PATH = "C:\path\to\ducktape-laya\build;$env:PATH"
go test ./...
```

With the zip, use `<zip>\include` for `CGO_CFLAGS`, `<zip>\lib` for `CGO_LDFLAGS` and `<zip>\bin` on `PATH`.

The DLL uses the Microsoft C++ runtime (`/MD`), so the target machine needs the Microsoft Visual C++ Redistributable, which is almost always installed.
