# Ducktape Laya

Linux native Laya GGUF decisions backed by the shared `libducktape_laya.so`.
Install the native library and set `DUCKTAPE_LAYA_LIB_DIR` to its library directory,
or expose its `ducktape-laya.pc` through `PKG_CONFIG_PATH` at build time.
Configure your application's library loader path when bundling the shared library.
No model or runtime download occurs. Model files are application-owned resources.
The safe crate provides ownership and errors; the sys crate exposes the C ABI.

## Usage

```rust
use ducktape_laya::{status, CallOptions, Config, Engine};

// threads: 0 = automatic (capped at 4), otherwise 1..=256. Unset values keep the defaults.
let config = Config::default().threads(2).context_tokens(1024).temperature("choice", 1.5);
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

- `Engine::open_router` opens a lazy multi-model registry; requests on a router must name a route in `model`; the same `execute` and `execute_with` calls serve both. A route may carry its own config in the router JSON.
- Requests are plain JSON; per-request `max_tokens` and `temperatures` need no extra API.
- Calls are blocking and the engine is `Send + Sync`; use your runtime's blocking pool from async code.
- Errors are `Error { status, message }`. `status` is one of the constants in `ducktape_laya::status` (`INVALID_ARGUMENT`, `MODEL_ERROR`, `CANCELLED`, `DEADLINE_EXCEEDED`, ...).
