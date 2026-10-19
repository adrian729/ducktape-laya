# Ducktape Laya

Linux native Laya GGUF decisions backed by the shared `libducktape_laya.so`.
Install the native library and set `DUCKTAPE_LAYA_LIB_DIR` to its library directory,
or expose its `ducktape-laya.pc` through `PKG_CONFIG_PATH` at build time.
Configure your application's library loader path when bundling the shared library.
No model or runtime download occurs. Model files are application-owned resources.
The safe crate provides ownership and errors; the sys crate exposes the C ABI.

## Usage

```rust
use ducktape_laya::{status, CallOptions, Engine};

// threads: 0 = automatic (capped at 4), otherwise 1..=256.
let engine = Engine::open("Laya-Q8_0.gguf", 0)?;
let request = r#"{"state":"Refund me","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}"#;
let result_json = engine.execute(request)?;

// Deadline and cooperative cancellation; add an observer with
// `execute_observed_with_options`.
let stop = std::sync::atomic::AtomicBool::new(false);
let options = CallOptions { timeout_ms: 5000, cancelled: Some(&stop) };
match engine.execute_with_options(request, &options) {
    Ok(json) => println!("{json}"),
    Err(error) if error.status == status::DEADLINE_EXCEEDED => eprintln!("too slow"),
    Err(error) => eprintln!("status {}: {}", error.status, error.message),
}
# Ok::<(), ducktape_laya::Error>(())
```

- `Engine::open_router` opens a lazy multi-model registry; on a router, `decide` and `execute` are strict and require a `model` in the request.
- Calls are blocking and the engine is `Send + Sync`; use your runtime's blocking pool from async code.
- Errors are `Error { status, message }`. `status` is one of the constants in `ducktape_laya::status` (`INVALID_ARGUMENT`, `MODEL_ERROR`, `CANCELLED`, `DEADLINE_EXCEEDED`, ...).
