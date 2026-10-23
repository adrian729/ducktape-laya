# Ducktape Laya

Native Laya GGUF decisions backed by the shared native library (`libducktape_laya.so`; on Windows `ducktape_laya.dll` with its import library `ducktape_laya.lib`).
Install the native library and set `DUCKTAPE_LAYA_LIB_DIR` to its library directory,
or expose its `ducktape-laya.pc` through `PKG_CONFIG_PATH` at build time.
Configure your application's library loader path when bundling the shared library.
No model or runtime download occurs. Model files are application-owned resources.
The safe crate provides ownership and errors; the sys crate exposes the C ABI.

## Usage

```rust
use ducktape_laya::{status, CallOptions, Config, Engine};

// threads: 0 = automatic (capped at 4), otherwise 1..=256. Unset values keep the defaults.
let config = Config::default().threads(2).context_tokens(1024).overflow("reject").temperature("choice", 1.5);
let engine = Engine::open("Laya-Q8_0.gguf", &config)?;
let request = r#"{"state":"Refund me","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}"#;
let result_json = engine.execute(request)?;

// Request id, deadline, cooperative cancellation and events. The cancel flag is read
// natively (`dtl_cancel_flag`): store 0 to continue, any other value to cancel.
let stop = std::sync::atomic::AtomicU32::new(0);
let mut events = Vec::new();
let mut on_event = |event: &str| events.push(event.to_owned());
let options = CallOptions::default()
    .request_id(7)
    .timeout_ms(5000)
    .cancel_flag(&stop)
    .on_event(&mut on_event);
match engine.execute_with(request, options) {
    Ok(json) => println!("{json}"),
    Err(error) if error.status == status::DEADLINE_EXCEEDED => eprintln!("too slow"),
    Err(error) => eprintln!("status {}: {}", error.status, error.message),
}
# Ok::<(), ducktape_laya::Error>(())
```

- `Config::overflow` sets the default policy for prompts over the limit: `"reject"` (default), `"truncate_end"`, `"truncate_start"` or `"scan"`; a request's own `overflow` wins. The library validates the name.
- `Engine::open_router` opens a lazy multi-model registry; requests on a router must name a route in `model`, or send `"model": "auto"` when the router JSON has `"auto": {"english": "en", "default": "ml"}`, to choose by the state's language (`"auto"` cannot be a route ID). The same `execute` and `execute_with` calls serve both. A route may carry its own config in the router JSON.
- Requests are plain JSON; per-request `max_tokens`, `temperatures`, `overflow`, `max_windows` and (with `"model": "auto"`) `lang` need no extra API.
- 0.2 crates need the 0.2 native library: the program links against `libducktape_laya.so.1` and its `DUCKTAPE_LAYA_2` symbols, so with only the 0.1.0 library (`libducktape_laya.so.0`) it fails to start. Opening also checks `dtl_abi_version()` and fails with `UNSUPPORTED_PLATFORM` for any ABI but 2.
- Calls are blocking and the engine is `Send + Sync`; use your runtime's blocking pool from async code.
- Errors are `Error { status, message }`. `status` is one of the constants in `ducktape_laya::status` (`INVALID_ARGUMENT`, `MODEL_ERROR`, `CANCELLED`, `DEADLINE_EXCEEDED`, ...).

## From a checkout

Released versions: `cargo add ducktape-laya` (0.2) plus the native library from the [v0.2.0 release tarball](https://github.com/adrian729/ducktape-laya/releases/tag/v0.2.0) (Linux x86-64). From the next release the Linux arm64 tarball and the Windows x64 zip are published too; with the zip, set `DUCKTAPE_LAYA_LIB_DIR` to its `lib` directory (the linker needs `ducktape_laya.lib`) and put its `bin` directory, which holds the DLL, on `PATH` at run time. To use unreleased changes from main, build the native library (see the repository README), then depend on the crate by path and point the build at the library:

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
