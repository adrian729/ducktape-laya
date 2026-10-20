//! Safe ownership over the shared CPU Laya engine. Inference is blocking;
//! async applications should use their runtime's blocking worker pool.
use ducktape_laya_sys as ffi;
use std::collections::BTreeMap;
use std::ffi::{c_char, CStr, CString};
use std::ptr::NonNull;
use std::sync::atomic::AtomicU32;
#[derive(Debug)]
pub struct Error {
    pub status: i32,
    pub message: String,
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(f)
    }
}
impl std::error::Error for Error {}
fn invalid(message: impl Into<String>) -> Error {
    Error {
        status: status::INVALID_ARGUMENT,
        message: message.into(),
    }
}
fn cstring(s: &str) -> Result<CString, Error> {
    CString::new(s).map_err(|_| invalid("Input contains a NUL byte"))
}
struct Buffer(*mut c_char);
impl Drop for Buffer {
    fn drop(&mut self) {
        unsafe { ffi::dtl_free(self.0.cast()) }
    }
}
fn check(status: i32, message: *mut c_char) -> Result<(), Error> {
    let owned = Buffer(message);
    if status == 0 {
        return Ok(());
    }
    let message = if owned.0.is_null() {
        "Laya operation failed".into()
    } else {
        unsafe { CStr::from_ptr(owned.0) }
            .to_string_lossy()
            .into_owned()
    };
    Err(Error { status, message })
}
/// The bindings speak C ABI 2; refuse a library built for another version.
fn check_abi() -> Result<(), Error> {
    let version = unsafe { ffi::dtl_abi_version() };
    if version == 2 {
        return Ok(());
    }
    Err(Error {
        status: status::UNSUPPORTED_PLATFORM,
        message: format!("Native ducktape-laya ABI {version} is not supported (expected 2)"),
    })
}
fn read_info(engine: *mut ffi::Engine) -> Result<String, Error> {
    check_abi()?;
    let mut result = std::ptr::null_mut();
    let mut error = std::ptr::null_mut();
    let status = unsafe { ffi::dtl_info(engine, &mut result, &mut error) };
    let owned = Buffer(result);
    check(status, error)?;
    Ok(unsafe { CStr::from_ptr(owned.0) }
        .to_string_lossy()
        .into_owned())
}
/// Runtime identity/capabilities without loading a model. No telemetry or input data.
pub fn runtime_info() -> Result<String, Error> {
    read_info(std::ptr::null_mut())
}
/// Native status codes (`dtl_status` in the C ABI), as carried by [`Error::status`].
pub mod status {
    pub const OK: i32 = 0;
    pub const INVALID_ARGUMENT: i32 = 1;
    pub const MODEL_ERROR: i32 = 2;
    pub const INFERENCE_ERROR: i32 = 3;
    pub const OUT_OF_MEMORY: i32 = 4;
    pub const CANCELLED: i32 = 5;
    pub const DEADLINE_EXCEEDED: i32 = 6;
    pub const UNSUPPORTED_PLATFORM: i32 = 7;
}
/// Open-time settings. Unset values keep the native defaults; the native
/// library validates ranges and rejects bad values with `INVALID_ARGUMENT`.
/// For a router the config applies to every route, which may override it.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Config {
    threads: Option<u32>,
    context_tokens: Option<u32>,
    overflow: Option<String>,
    temperatures: BTreeMap<String, f64>,
}
impl Config {
    /// 0 selects automatic threading (capped at 4); otherwise 1..=256.
    pub fn threads(mut self, threads: u32) -> Self {
        self.threads = Some(threads);
        self
    }
    /// Prompt capacity in tokens, up to the model's trained length (default 512).
    pub fn context_tokens(mut self, tokens: u32) -> Self {
        self.context_tokens = Some(tokens);
        self
    }
    /// Engine-wide policy for prompts over the token limit: `"reject"`
    /// (default), `"truncate_end"`, `"truncate_start"` or `"scan"`.
    pub fn overflow(mut self, policy: &str) -> Self {
        self.overflow = Some(policy.to_owned());
        self
    }
    /// Calibration override keyed like the GGUF metadata, e.g. `"choice"`,
    /// `"choice.3_5"` or `"noul.2"`. The value must be finite and positive.
    pub fn temperature(mut self, key: impl Into<String>, value: f64) -> Self {
        self.temperatures.insert(key.into(), value);
        self
    }
    /// `None` when nothing is set, so the native defaults apply.
    fn to_json(&self) -> Result<Option<CString>, Error> {
        let mut parts = Vec::new();
        if let Some(threads) = self.threads {
            parts.push(format!("\"threads\":{threads}"));
        }
        if let Some(tokens) = self.context_tokens {
            parts.push(format!("\"context_tokens\":{tokens}"));
        }
        if let Some(policy) = &self.overflow {
            parts.push(format!("\"overflow\":{}", json_string(policy)));
        }
        if !self.temperatures.is_empty() {
            let mut items = Vec::new();
            for (key, value) in &self.temperatures {
                if !value.is_finite() {
                    return Err(invalid("Temperature must be finite"));
                }
                items.push(format!("{}:{value}", json_string(key)));
            }
            parts.push(format!("\"temperatures\":{{{}}}", items.join(",")));
        }
        if parts.is_empty() {
            return Ok(None);
        }
        cstring(&format!("{{{}}}", parts.join(","))).map(Some)
    }
}
fn json_string(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
/// Per-call controls for [`Engine::execute_with`].
#[derive(Default)]
pub struct CallOptions<'a> {
    request_id: u64,
    timeout_ms: u64,
    cancelled: Option<&'a AtomicU32>,
    on_event: Option<&'a mut dyn FnMut(&str)>,
}
impl<'a> CallOptions<'a> {
    /// Caller-chosen id echoed in every event of the call.
    pub fn request_id(mut self, id: u64) -> Self {
        self.request_id = id;
        self
    }
    /// 0 disables the deadline; otherwise 1..=86400000 milliseconds.
    pub fn timeout_ms(mut self, ms: u64) -> Self {
        self.timeout_ms = ms;
        self
    }
    /// Cooperative cancellation: store 0 to continue, `status::DEADLINE_EXCEEDED`
    /// to report a host deadline, any other value to cancel. The native side
    /// reads it with `dtl_cancel_flag`, so no Rust code runs on worker threads.
    /// Keep the flag alive for the call.
    pub fn cancel_flag(mut self, flag: &'a AtomicU32) -> Self {
        self.cancelled = Some(flag);
        self
    }
    /// Passive "start" then "end"/"error" events, run synchronously on the
    /// calling thread outside native locks. Panics are contained (with
    /// panic=unwind); abort panics cannot be. Callbacks may reenter the engine
    /// but must not drop it.
    pub fn on_event(mut self, callback: &'a mut dyn FnMut(&str)) -> Self {
        self.on_event = Some(callback);
        self
    }
}
/// A single GGUF model or a router over several; both are used identically.
pub struct Engine(NonNull<ffi::Engine>);
// SAFETY: the native ABI serializes inference on each handle. Rust borrows
// prevent Drop racing with execute; the pointer is never exposed publicly.
unsafe impl Send for Engine {}
unsafe impl Sync for Engine {}
impl Engine {
    /// Model metadata for a single engine; routers report common runtime capabilities.
    pub fn info(&self) -> Result<String, Error> {
        read_info(self.0.as_ptr())
    }
    /// Open a single GGUF model.
    pub fn open(model_path: &str, config: &Config) -> Result<Self, Error> {
        let path = cstring(model_path)?;
        Self::open_with(config, |config, out, error| unsafe {
            ffi::dtl_open(path.as_ptr(), config, out, error)
        })
    }
    /// Open a bounded, lazy local model registry. See docs/API.md for the JSON.
    /// Requests must name a route in `model`.
    pub fn open_router(router_json: &str, config: &Config) -> Result<Self, Error> {
        let router = cstring(router_json)?;
        Self::open_with(config, |config, out, error| unsafe {
            ffi::dtl_open_router(router.as_ptr(), config, out, error)
        })
    }
    fn open_with(
        config: &Config,
        open: impl FnOnce(*const c_char, *mut *mut ffi::Engine, *mut *mut c_char) -> i32,
    ) -> Result<Self, Error> {
        check_abi()?;
        let config = config.to_json()?;
        let mut raw = std::ptr::null_mut();
        let mut error = std::ptr::null_mut();
        let status = open(
            config.as_ref().map_or(std::ptr::null(), |c| c.as_ptr()),
            &mut raw,
            &mut error,
        );
        check(status, error)?;
        NonNull::new(raw).map(Self).ok_or_else(|| Error {
            status: status::MODEL_ERROR,
            message: "Native engine returned null".into(),
        })
    }
    /// Execute one request object or an array of requests with default options.
    pub fn execute(&self, request_json: &str) -> Result<String, Error> {
        self.execute_with(request_json, CallOptions::default())
    }
    /// Execute with a request id, deadline, cancellation flag and/or event
    /// callback. Takes the options by value because they own the exclusive
    /// borrow of the event callback.
    pub fn execute_with(
        &self,
        request_json: &str,
        mut options: CallOptions<'_>,
    ) -> Result<String, Error> {
        struct Hook<'a> {
            callback: &'a mut dyn FnMut(&str),
        }
        unsafe extern "C" fn notify(text: *const c_char, user: *mut std::ffi::c_void) {
            let hook = unsafe { &mut *user.cast::<Hook<'_>>() };
            if let Err(payload) = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let event = unsafe { CStr::from_ptr(text) }.to_string_lossy();
                (hook.callback)(&event);
            })) {
                // A custom panic payload can itself panic in Drop. Contain that
                // too, and leak only the secondary payload rather than risk an
                // unbounded chain of destructors unwinding across the C ABI.
                if let Err(secondary) =
                    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| drop(payload)))
                {
                    std::mem::forget(secondary);
                }
            }
        }
        let request = cstring(request_json)?;
        let mut hook = options.on_event.take().map(|callback| Hook { callback });
        let mut native = unsafe { ffi::dtl_default_call_options() };
        native.request_id = options.request_id;
        native.timeout_ms = options.timeout_ms;
        if let Some(hook) = hook.as_mut() {
            native.on_event = Some(notify);
            native.event_user_data = (hook as *mut Hook<'_>).cast();
        }
        if let Some(flag) = options.cancelled {
            native.should_cancel = Some(ffi::dtl_cancel_flag);
            native.cancel_user_data = (flag as *const AtomicU32).cast_mut().cast();
        }
        let mut result = std::ptr::null_mut();
        let mut error = std::ptr::null_mut();
        let status = unsafe {
            ffi::dtl_execute(
                self.0.as_ptr(),
                request.as_ptr(),
                &native,
                &mut result,
                &mut error,
            )
        };
        let output = Buffer(result);
        check(status, error)?;
        if output.0.is_null() {
            return Err(Error {
                status: status::INFERENCE_ERROR,
                message: "Native result was null".into(),
            });
        }
        Ok(unsafe { CStr::from_ptr(output.0) }
            .to_string_lossy()
            .into_owned())
    }
}
impl Drop for Engine {
    fn drop(&mut self) {
        unsafe { ffi::dtl_close(self.0.as_ptr()) }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::Ordering;
    const NOUL: &str = r#"{"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}"#;
    fn model() -> Option<String> {
        std::env::var("LAYA_MODEL").ok()
    }
    fn two() -> Config {
        Config::default().threads(2)
    }
    fn escape(path: &str) -> String {
        path.replace('\\', "\\\\").replace('"', "\\\"")
    }
    fn with_model(request: &str, name: &str) -> String {
        request.replacen('{', &format!("{{\"model\":\"{name}\","), 1)
    }
    #[test]
    fn config_json_is_deterministic_and_sparse() {
        assert!(Config::default().to_json().unwrap().is_none());
        let config = Config::default()
            .threads(2)
            .context_tokens(1024)
            .temperature("noul.2", 2.0)
            .temperature("choice", 1.5)
            .temperature("we\"ird", 1.0);
        assert_eq!(
            config.to_json().unwrap().unwrap().to_str().unwrap(),
            r#"{"threads":2,"context_tokens":1024,"temperatures":{"choice":1.5,"noul.2":2,"we\"ird":1}}"#
        );
        let bad = Config::default().temperature("choice", f64::NAN);
        assert_eq!(bad.to_json().unwrap_err().status, 1);
    }
    #[test]
    fn contains_panicking_payload_destructors() {
        struct Bomb;
        impl Drop for Bomb {
            fn drop(&mut self) {
                std::panic::panic_any(Bomb);
            }
        }
        let engine = Engine::open_router(r#"{"models":{"unused":"Cargo.toml"}}"#, &two()).unwrap();
        let mut calls = 0;
        let mut callback = |_: &str| {
            calls += 1;
            std::panic::panic_any(Bomb);
        };
        let result = engine.execute_with("{}", CallOptions::default().on_event(&mut callback));
        assert_eq!(result.unwrap_err().status, 1);
        assert_eq!(calls, 2);
    }
    #[test]
    fn real_features() {
        let Some(path) = model() else { return };
        let engine = Engine::open(&path, &two()).unwrap();
        // Unknown top-level keys are rejected.
        let tagged = NOUL.replacen('{', "{\"metadata\":\"caller tag\",", 1);
        assert_eq!(engine.execute(&tagged).unwrap_err().status, 1);
        let request = r#"{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}"#;
        let single = engine.execute(request).unwrap();
        assert!(single.contains("\"values\":{\"refund\":true}"));
        let batch = format!("[{request},{request}]");
        let mut events = Vec::new();
        let mut on_event = |e: &str| events.push(e.to_owned());
        let result = engine
            .execute_with(
                &batch,
                CallOptions::default()
                    .request_id(21)
                    .on_event(&mut on_event),
            )
            .unwrap();
        assert_eq!(result, format!("[{single},{single}]"));
        assert_eq!(events.len(), 2);
        assert!(events[0].contains("\"event\":\"start\""));
        assert!(events[1].contains("\"request_id\":21"));
        assert!(engine
            .execute_with(
                "{}",
                CallOptions::default()
                    .request_id(22)
                    .on_event(&mut |_| panic!("contained observer"))
            )
            .is_err());
        // Reentry uses a distinct borrowed callback and the same native engine.
        engine
            .execute_with(
                request,
                CallOptions::default().request_id(23).on_event(&mut |_| {
                    engine.execute(request).unwrap();
                }),
            )
            .unwrap();
        let router = Engine::open_router(
            &format!(
                r#"{{"models":{{"english":"{}"}},"max_loaded":1}}"#,
                escape(&path)
            ),
            &two(),
        )
        .unwrap();
        assert!(router
            .execute(&with_model(request, "english"))
            .unwrap()
            .contains("\"model\":\"english\""));
        assert_eq!(router.execute(request).unwrap_err().status, 1);
    }
    #[test]
    fn overflow_policy_applies_and_is_validated() {
        let Some(path) = model() else { return };
        let config = two().context_tokens(64).overflow("scan");
        assert_eq!(
            config.to_json().unwrap().unwrap().to_str().unwrap(),
            r#"{"threads":2,"context_tokens":64,"overflow":"scan"}"#
        );
        let long = format!(
            r#"{{"state":"{}","questions":{{"refund":{{"type":"noul","instructions":"Is a refund requested?"}}}}}}"#,
            "refund ".repeat(800)
        );
        let engine = Engine::open(&path, &two().overflow("truncate_end")).unwrap();
        let out = engine.execute(&long).unwrap();
        assert!(out.contains("\"state_tokens_dropped\":") && !out.contains("\"state_tokens_dropped\":0"), "{out}");
        let rejected = long.replacen('{', "{\"overflow\":\"reject\",", 1);
        assert_eq!(engine.execute(&rejected).unwrap_err().status, 1);
        assert_eq!(Engine::open(&path, &two().overflow("bogus")).err().unwrap().status, 1);
    }
    #[test]
    fn config_and_per_request_settings() {
        let Some(path) = model() else { return };
        let engine = Engine::open(&path, &two().context_tokens(1024)).unwrap();
        let info = engine.info().unwrap();
        assert!(info.contains("\"context_tokens\":1024"), "{info}");
        // max_tokens is bounded by the engine's context_tokens.
        let with = |extra: &str| NOUL.replacen('{', &format!("{{{extra},"), 1);
        let baseline = engine.execute(NOUL).unwrap();
        assert_eq!(
            engine.execute(&with("\"max_tokens\":1024")).unwrap(),
            baseline
        );
        assert_eq!(
            engine
                .execute(&with("\"max_tokens\":1025"))
                .unwrap_err()
                .status,
            1
        );
        assert_eq!(
            engine
                .execute(&with("\"max_tokens\":0"))
                .unwrap_err()
                .status,
            1
        );
        // Calibration temperatures change confidence; per-request overrides config.
        let hot = Engine::open(&path, &two().temperature("noul.2", 4.0)).unwrap();
        assert_ne!(hot.execute(NOUL).unwrap(), baseline);
        assert_eq!(
            hot.execute(&with("\"temperatures\":{\"noul.2\":1.0}"))
                .unwrap(),
            engine
                .execute(&with("\"temperatures\":{\"noul.2\":1.0}"))
                .unwrap()
        );
        assert_eq!(
            engine
                .execute(&with("\"temperatures\":{\"noul.2\":-1}"))
                .unwrap_err()
                .status,
            1
        );
    }
    #[test]
    fn invalid_config_is_rejected() {
        let Some(path) = model() else { return };
        for config in [
            two().threads(257),
            two().context_tokens(8),
            two().context_tokens(u32::MAX),
            two().temperature("choice", 0.0),
            two().temperature("choice", -1.0),
        ] {
            assert_eq!(Engine::open(&path, &config).err().unwrap().status, 1);
        }
        let router = format!(r#"{{"models":{{"a":"{}"}}}}"#, escape(&path));
        assert_eq!(
            Engine::open_router(&router, &two().threads(257))
                .err()
                .unwrap()
                .status,
            1
        );
    }
    #[test]
    fn router_per_route_config() {
        let Some(path) = model() else { return };
        let p = escape(&path);
        let router = Engine::open_router(
            &format!(
                r#"{{"models":{{"small":{{"path":"{p}","config":{{"context_tokens":64}}}},"plain":"{p}"}},"max_loaded":1}}"#
            ),
            &two().context_tokens(1024),
        )
        .unwrap();
        let big = format!("\"max_tokens\":{}", 512);
        let ask = |model: &str| {
            router.execute(&with_model(
                &NOUL.replacen('{', &format!("{{{big},"), 1),
                model,
            ))
        };
        // The route override wins for "small"; "plain" inherits the open config.
        assert_eq!(ask("small").unwrap_err().status, 1);
        assert!(ask("plain").is_ok());
        let bad = format!(r#"{{"models":{{"a":{{"path":"{p}","config":{{"threads":999}}}}}}}}"#);
        assert!(Engine::open_router(&bad, &two()).is_err());
    }
    #[test]
    fn cancellation_and_deadline() {
        let Some(path) = model() else { return };
        let engine = Engine::open(&path, &two()).unwrap();
        let baseline = engine.execute(NOUL).unwrap();
        let flag = AtomicU32::new(status::CANCELLED as u32);
        let cancelled = || CallOptions::default().cancel_flag(&flag);
        assert_eq!(
            engine.execute_with(NOUL, cancelled()).unwrap_err().status,
            status::CANCELLED
        );
        flag.store(status::DEADLINE_EXCEEDED as u32, Ordering::Relaxed);
        assert_eq!(
            engine.execute_with(NOUL, cancelled()).unwrap_err().status,
            status::DEADLINE_EXCEEDED
        );
        assert_eq!(
            engine
                .execute_with(NOUL, CallOptions::default().timeout_ms(1))
                .unwrap_err()
                .status,
            status::DEADLINE_EXCEEDED
        );
        assert_eq!(engine.execute(NOUL).unwrap(), baseline);
        flag.store(0, Ordering::Relaxed);
        assert_eq!(engine.execute_with(NOUL, cancelled()).unwrap(), baseline);
    }
    #[test]
    fn status_constants_match_header() {
        assert_eq!(
            [
                status::OK,
                status::INVALID_ARGUMENT,
                status::MODEL_ERROR,
                status::INFERENCE_ERROR,
                status::OUT_OF_MEMORY,
                status::CANCELLED,
                status::DEADLINE_EXCEEDED,
                status::UNSUPPORTED_PLATFORM
            ],
            [0, 1, 2, 3, 4, 5, 6, 7]
        );
    }
    #[test]
    fn events_carry_request_id() {
        let router = Engine::open_router(r#"{"models":{"unused":"Cargo.toml"}}"#, &two()).unwrap();
        let flag = AtomicU32::new(1);
        // A request without a route is invalid; events are still delivered.
        let mut events = Vec::new();
        let mut on_event = |e: &str| events.push(e.to_owned());
        let error = router
            .execute_with(
                "{}",
                CallOptions::default()
                    .request_id(5)
                    .cancel_flag(&flag)
                    .on_event(&mut on_event),
            )
            .unwrap_err();
        assert!(matches!(error.status, 1 | 5));
        assert!(!events.is_empty());
        assert!(events.iter().all(|e| e.contains("\"request_id\":5")));
        let Some(path) = model() else { return };
        let engine = Engine::open(&path, &two()).unwrap();
        let mut events = Vec::new();
        let mut on_event = |e: &str| events.push(e.to_owned());
        let error = engine
            .execute_with(
                NOUL,
                CallOptions::default()
                    .request_id(9)
                    .cancel_flag(&flag)
                    .on_event(&mut on_event),
            )
            .unwrap_err();
        assert_eq!(error.status, status::CANCELLED);
        assert!(events
            .iter()
            .any(|e| e.contains("\"event\":\"error\"") && e.contains("\"request_id\":9")));
        flag.store(0, Ordering::Relaxed);
        let mut events = Vec::new();
        let mut on_event = |e: &str| events.push(e.to_owned());
        let result = engine
            .execute_with(
                NOUL,
                CallOptions::default()
                    .request_id(10)
                    .cancel_flag(&flag)
                    .on_event(&mut on_event),
            )
            .unwrap();
        assert_eq!(result, engine.execute(NOUL).unwrap());
        assert_eq!(events.len(), 2);
        assert!(events.iter().all(|e| e.contains("\"request_id\":10")));
    }
    #[test]
    fn shared_across_threads() {
        let Some(path) = model() else { return };
        let engine = Engine::open(&path, &two()).unwrap();
        let baseline = engine.execute(NOUL).unwrap();
        // Two names for one file with one slot: concurrent calls evict each other.
        let p = escape(&path);
        let router = Engine::open_router(
            &format!(r#"{{"models":{{"a":"{p}","b":"{p}"}},"max_loaded":1}}"#),
            &two(),
        )
        .unwrap();
        let flag = AtomicU32::new(0);
        std::thread::scope(|scope| {
            let callers: Vec<_> = (0..3)
                .map(|_| scope.spawn(|| engine.execute(NOUL).unwrap()))
                .collect();
            let routed: Vec<_> = ["a", "b"]
                .map(|name| {
                    let request = with_model(NOUL, name);
                    let router = &router;
                    scope.spawn(move || (name, router.execute(&request).unwrap()))
                })
                .into_iter()
                .collect();
            // Cancellation is requested from another thread while the call waits or runs.
            let cancelled = scope
                .spawn(|| engine.execute_with(NOUL, CallOptions::default().cancel_flag(&flag)));
            flag.store(status::CANCELLED as u32, Ordering::Relaxed);
            for caller in callers {
                assert_eq!(caller.join().unwrap(), baseline);
            }
            for caller in routed {
                let (name, result) = caller.join().unwrap();
                let expected =
                    format!("{},\"model\":\"{name}\"}}", &baseline[..baseline.len() - 1]);
                assert_eq!(result, expected);
            }
            match cancelled.join().unwrap() {
                Ok(result) => assert_eq!(result, baseline),
                Err(error) => assert_eq!(error.status, status::CANCELLED),
            }
        });
        // Engines may be closed on a thread other than the one that opened them.
        std::thread::spawn(move || drop((engine, router)))
            .join()
            .unwrap();
    }
    #[test]
    fn diagnostics() {
        let runtime = runtime_info().unwrap();
        assert!(runtime.contains("\"abi_version\":2"));
        if let Some(model) = model() {
            let engine = Engine::open(&model, &two()).unwrap();
            assert!(engine
                .info()
                .unwrap()
                .contains("\"decision_type\":\"laya\""));
        }
    }
    #[test]
    fn rejects_bad_path() {
        assert!(Engine::open("missing.gguf", &two()).is_err());
    }
    #[test]
    fn rejects_nul() {
        assert_eq!(
            Engine::open("a\0b", &two()).err().unwrap().status,
            status::INVALID_ARGUMENT
        );
    }
}
