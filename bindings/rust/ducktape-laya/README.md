# Ducktape Laya for Rust

Local [Laya](https://github.com/NandhaKishorM/laya) decisions from Rust: ask named questions about a text (`choice` picks one of several options, `noul` gives the probability that a statement is true, `score` picks a level on an ordered scale) and get probabilities, computed on the CPU by the shared native library. This safe crate provides ownership and errors; [`ducktape-laya-sys`](https://crates.io/crates/ducktape-laya-sys) exposes the raw C ABI.

## Install

```sh
cargo add ducktape-laya
```

The crate links the native library, which comes from the release archive: [v0.3.0](https://github.com/adrian729/ducktape-laya/releases/tag/v0.3.0) has `ducktape-laya-0.3.0-linux-x64.tar.gz`, `ducktape-laya-0.3.0-linux-arm64.tar.gz` and `ducktape-laya-0.3.0-windows-x64.zip`; releases that promote macOS to tier 2 also have `-macos-arm64.tar.gz` and `-macos-x64.tar.gz` ([platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md)). At build time, set `DUCKTAPE_LAYA_LIB_DIR` to the archive's `lib` directory, or expose its `lib/pkgconfig/ducktape-laya.pc` through `PKG_CONFIG_PATH`. At run time the loader must find the library: set `LD_LIBRARY_PATH` (`DYLD_LIBRARY_PATH` on macOS), install it system-wide, or bundle it with your application ([deployment](https://github.com/adrian729/ducktape-laya/blob/main/docs/PACKAGING.md)). On Windows, `DUCKTAPE_LAYA_LIB_DIR` names the zip's `lib` (the import library `ducktape_laya.lib`) and its `bin` (the DLL) goes on `PATH` at run time. No model or runtime download occurs; download the model separately ([models](https://github.com/adrian729/ducktape-laya/blob/main/docs/MODELS.md)). Tested with Rust 1.98.

## Usage

```rust
use ducktape_laya::{CallOptions, CancellationToken, Config, Engine, RouterConfig, Status};

let engine = Engine::open("Laya-Q8_0.gguf")?;
// Or with open options. Unset values keep the defaults; set values, 0 included, go to the library, which validates them.
let config = Config::default().threads(2).context_tokens(1024).overflow("reject")
    .temperatures([("choice", 1.5)]).max_windows_per_call(64);
let engine = Engine::open_with("Laya-Q8_0.gguf", &config)?;
let request = r#"{"state":"Refund me","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}"#;
let result_json = engine.execute(request)?;

// Request id, deadline, cooperative cancellation and events. A token is cloneable and can be
// cancelled from any thread; a call given an already-cancelled token fails at once with `Status::Cancelled`.
let stop = CancellationToken::new();
let mut events = Vec::new();
let mut on_event = |event: &str| events.push(event.to_owned());
let options = CallOptions::default()
    .request_id(7)
    .timeout_ms(5000)
    .cancellation_token(&stop)
    .on_event(&mut on_event);
match engine.execute_with(request, options) {
    Ok(json) => println!("{json}"),
    Err(error) if error.status == Status::DeadlineExceeded => eprintln!("too slow"),
    Err(error) => eprintln!("{:?}: {}", error.status, error.message),
}

// A router from a builder (or from JSON text: `Engine::open_router(r#"{"models":{...}}"#)`).
let router = RouterConfig::new().route("en", "Laya-Q8_0.gguf").route("ml", "laya-multilingual-f16.gguf")
    .max_loaded(1).auto("en", "ml");
let engine = Engine::open_router_with(&router, &config)?;
# Ok::<(), ducktape_laya::Error>(())
```

- `Config::overflow` sets the default policy for prompts over the limit: `"reject"` (default), `"truncate_end"`, `"truncate_start"` or `"scan"`; a request's own `overflow` wins. `Config::max_windows_per_call` (1 to 4,096, default 256) bounds the forward passes one call may plan. The library validates every value.
- Model paths are any `AsRef<Path>`. On Unix the path's bytes reach the file system unchanged, so non-UTF-8 names work; on Windows they must be UTF-8. Paths inside a router are JSON strings, so they must be UTF-8.
- `Engine::open_router` opens a lazy multi-model registry from router JSON (`&str`, `String`) or a `&RouterConfig`; requests on a router must name a route in `model`, or send `"model": "auto"` when the router has `.auto("en", "ml")` (`"auto": {"english": "en", "default": "ml"}` in JSON), to choose by the state's language (`"auto"` cannot be a route ID). The same `execute` and `execute_with` calls serve both. A route may carry its own config (`route_with`).
- Requests are plain JSON; per-request `max_tokens`, `temperatures`, `overflow`, `max_windows` and (with `"model": "auto"`) `lang` need no extra API.
- 0.2 and 0.3 crates need a native library of C ABI 2 (0.2.0 or later): the program links against `libducktape_laya.so.1` and its `DUCKTAPE_LAYA_2` symbols, so with only the 0.1.0 library (`libducktape_laya.so.0`) it fails to start. Opening also checks `dtl_abi_version()` and fails with `Status::UnsupportedPlatform` for any ABI but 2.
- Calls are blocking and the engine is `Send + Sync`; use your runtime's blocking pool from async code.
- Errors are `Error { status, message }`, with `status` a `ducktape_laya::Status` (`InvalidArgument`, `ModelError`, `Cancelled`, `DeadlineExceeded`, ...). When several checks fail, the first applies: the arguments' conversion (the request, config and paths, before the library is touched), an already-cancelled token, then the native call.

## From a checkout

To use unreleased changes, build the native library ([building](https://github.com/adrian729/ducktape-laya/blob/main/docs/BUILDING.md)), then depend on the crate by path and point the build at the library:

```toml
[dependencies]
ducktape-laya = { path = "/path/to/ducktape-laya/bindings/rust/ducktape-laya" }
```

```sh
export DUCKTAPE_LAYA_LIB_DIR=/path/to/ducktape-laya/build
export LD_LIBRARY_PATH="$DUCKTAPE_LAYA_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cargo build
```

`examples/decide.rs` runs one request from standard input: `cargo run --example decide -- /path/to/Laya-Q8_0.gguf < request.json` in this crate's directory, with the same variables set.

The request and result format, routing and limits are in the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md).
