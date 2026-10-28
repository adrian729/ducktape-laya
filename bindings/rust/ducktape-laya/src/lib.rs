//! Safe ownership over the shared CPU Laya engine. Inference is blocking;
//! async applications should use their runtime's blocking worker pool.
use ducktape_laya_sys as ffi;
use std::collections::BTreeMap;
use std::ffi::{c_char, CStr, CString};
use std::path::{Path, PathBuf};
use std::ptr::NonNull;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;
/// Native status codes (`dtl_status` in the C ABI), as carried by [`Error::status`].
#[non_exhaustive]
#[repr(i32)]
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Status {
    Ok = 0,
    InvalidArgument = 1,
    ModelError = 2,
    InferenceError = 3,
    OutOfMemory = 4,
    Cancelled = 5,
    DeadlineExceeded = 6,
    UnsupportedPlatform = 7,
}
impl Status {
    /// A native status value; an unknown one (impossible under ABI 2) maps to
    /// `InferenceError`.
    pub fn from_raw(raw: i32) -> Self {
        match raw {
            0 => Self::Ok,
            1 => Self::InvalidArgument,
            2 => Self::ModelError,
            3 => Self::InferenceError,
            4 => Self::OutOfMemory,
            5 => Self::Cancelled,
            6 => Self::DeadlineExceeded,
            7 => Self::UnsupportedPlatform,
            _ => Self::InferenceError,
        }
    }
}
#[derive(Debug)]
pub struct Error {
    pub status: Status,
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
        status: Status::InvalidArgument,
        message: message.into(),
    }
}
fn cstring(s: &str) -> Result<CString, Error> {
    CString::new(s).map_err(|_| invalid("Input contains a NUL byte"))
}
/// The model path as the bytes the native core opens: raw on Unix (any file
/// name works), UTF-8 on Windows.
fn path_cstring(path: &Path) -> Result<CString, Error> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        CString::new(path.as_os_str().as_bytes())
            .map_err(|_| invalid("Model path contains a NUL byte"))
    }
    #[cfg(not(unix))]
    {
        cstring(
            path.to_str()
                .ok_or_else(|| invalid("Model path is not valid UTF-8"))?,
        )
    }
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
    Err(Error {
        status: Status::from_raw(status),
        message,
    })
}
/// The bindings speak C ABI 2; refuse a library built for another version.
fn check_abi() -> Result<(), Error> {
    let version = unsafe { ffi::dtl_abi_version() };
    if version == 2 {
        return Ok(());
    }
    Err(Error {
        status: Status::UnsupportedPlatform,
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
    if owned.0.is_null() {
        return Err(Error {
            status: Status::InferenceError,
            message: "Native result was null".into(),
        });
    }
    Ok(unsafe { CStr::from_ptr(owned.0) }
        .to_string_lossy()
        .into_owned())
}
/// Runtime identity/capabilities without loading a model. No telemetry or input data.
pub fn runtime_info() -> Result<String, Error> {
    read_info(std::ptr::null_mut())
}
/// Open-time settings. Unset values keep the native defaults; the native
/// library validates every value (0 included) and rejects bad ones with
/// `Status::InvalidArgument`. For a router the config applies to every route,
/// which may override it (except `max_windows_per_call`, which is engine-level).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Config {
    threads: Option<u32>,
    context_tokens: Option<u32>,
    overflow: Option<String>,
    temperatures: BTreeMap<String, f64>,
    max_windows_per_call: Option<u32>,
}
impl Config {
    /// 0 selects automatic threading (at most 4 and at most the CPUs the process may use); otherwise 1..=256.
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
    /// Calibration overrides keyed like the GGUF metadata, e.g. `"choice"`,
    /// `"choice.3_5"` or `"noul.2"`, replacing any set before. Each value must
    /// be finite and positive.
    pub fn temperatures<K: Into<String>>(
        mut self,
        entries: impl IntoIterator<Item = (K, f64)>,
    ) -> Self {
        self.temperatures = entries.into_iter().map(|(k, v)| (k.into(), v)).collect();
        self
    }
    /// The most forward passes one call may plan: 1..=4096, default 256.
    pub fn max_windows_per_call(mut self, windows: u32) -> Self {
        self.max_windows_per_call = Some(windows);
        self
    }
    /// The JSON object text, or `None` when nothing is set.
    fn json(&self) -> Result<Option<String>, Error> {
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
        if let Some(windows) = self.max_windows_per_call {
            parts.push(format!("\"max_windows_per_call\":{windows}"));
        }
        if parts.is_empty() {
            return Ok(None);
        }
        Ok(Some(format!("{{{}}}", parts.join(","))))
    }
    /// `None` when nothing is set, so the native defaults apply.
    fn to_json(&self) -> Result<Option<CString>, Error> {
        self.json()?.map(|text| cstring(&text)).transpose()
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
/// A router's models: route ids mapped to a model path and an optional config
/// that overrides the open config for that route. Build it with [`route`],
/// [`route_with`], [`max_loaded`] and [`auto`], then pass `&router` to
/// [`Engine::open_router`].
///
/// [`route`]: RouterConfig::route
/// [`route_with`]: RouterConfig::route_with
/// [`max_loaded`]: RouterConfig::max_loaded
/// [`auto`]: RouterConfig::auto
#[derive(Clone, Debug, Default, PartialEq)]
pub struct RouterConfig {
    routes: BTreeMap<String, (PathBuf, Option<Config>)>,
    max_loaded: Option<u32>,
    auto: Option<(String, String)>,
}
impl RouterConfig {
    pub fn new() -> Self {
        Self::default()
    }
    /// Add (or replace) a route. Router paths travel in JSON, so they must be valid UTF-8.
    pub fn route(mut self, id: impl Into<String>, path: impl AsRef<Path>) -> Self {
        self.routes
            .insert(id.into(), (path.as_ref().to_path_buf(), None));
        self
    }
    /// Add (or replace) a route with its own config.
    pub fn route_with(
        mut self,
        id: impl Into<String>,
        path: impl AsRef<Path>,
        config: &Config,
    ) -> Self {
        self.routes.insert(
            id.into(),
            (path.as_ref().to_path_buf(), Some(config.clone())),
        );
        self
    }
    /// The most models loaded at once (the library default is 1).
    pub fn max_loaded(mut self, count: u32) -> Self {
        self.max_loaded = Some(count);
        self
    }
    /// Choose the route by the state's language with `"model": "auto"`.
    pub fn auto(mut self, english: impl Into<String>, default: impl Into<String>) -> Self {
        self.auto = Some((english.into(), default.into()));
        self
    }
    fn to_json(&self) -> Result<String, Error> {
        let mut models = Vec::new();
        for (id, (path, config)) in &self.routes {
            let path = json_string(
                path.to_str()
                    .ok_or_else(|| invalid("Route path is not valid UTF-8"))?,
            );
            let config = config.as_ref().map(Config::json).transpose()?.flatten();
            models.push(match config {
                Some(config) => format!(
                    "{}:{{\"path\":{path},\"config\":{config}}}",
                    json_string(id)
                ),
                None => format!("{}:{path}", json_string(id)),
            });
        }
        let mut parts = vec![format!("\"models\":{{{}}}", models.join(","))];
        if let Some(count) = self.max_loaded {
            parts.push(format!("\"max_loaded\":{count}"));
        }
        if let Some((english, default)) = &self.auto {
            parts.push(format!(
                "\"auto\":{{\"english\":{},\"default\":{}}}",
                json_string(english),
                json_string(default)
            ));
        }
        Ok(format!("{{{}}}", parts.join(",")))
    }
}
/// What [`Engine::open_router`] accepts: router JSON text (`&str`, `String`) or a
/// [`RouterConfig`] (`&RouterConfig`).
pub trait RouterSource {
    fn router_json(&self) -> Result<String, Error>;
}
impl RouterSource for &str {
    fn router_json(&self) -> Result<String, Error> {
        Ok((*self).to_owned())
    }
}
impl RouterSource for String {
    fn router_json(&self) -> Result<String, Error> {
        Ok(self.clone())
    }
}
impl RouterSource for &RouterConfig {
    fn router_json(&self) -> Result<String, Error> {
        self.to_json()
    }
}
/// Cancels the calls it is passed to ([`CallOptions::cancellation_token`]) from
/// any thread. Clones share one state; a cancelled token stays cancelled, so use
/// a new one for later calls. The native side reads it with `dtl_cancel_flag`, so
/// no Rust code runs on worker threads.
#[derive(Clone, Debug, Default)]
pub struct CancellationToken(Arc<AtomicU32>);
impl CancellationToken {
    pub fn new() -> Self {
        Self::default()
    }
    /// Stop the calls using this token at their next safe point (status `Cancelled`).
    pub fn cancel(&self) {
        self.0.store(Status::Cancelled as u32, Ordering::Relaxed);
    }
    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::Relaxed) != 0
    }
}
/// Per-call controls for [`Engine::execute_with`].
#[derive(Default)]
pub struct CallOptions<'a> {
    request_id: u64,
    timeout_ms: u64,
    cancelled: Option<&'a CancellationToken>,
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
    /// Cooperative cancellation. A token that is already cancelled fails the
    /// call at once with `Status::Cancelled`: no native call, no events.
    pub fn cancellation_token(mut self, token: &'a CancellationToken) -> Self {
        self.cancelled = Some(token);
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
    /// Open a single GGUF model with the library defaults. See [`Engine::open_with`].
    pub fn open(model_path: impl AsRef<Path>) -> Result<Self, Error> {
        Self::open_with(model_path, &Config::default())
    }
    /// Open a single GGUF model. On Unix the path's bytes reach the file system
    /// unchanged; on Windows the path must be valid UTF-8 (`InvalidArgument`
    /// otherwise).
    pub fn open_with(model_path: impl AsRef<Path>, config: &Config) -> Result<Self, Error> {
        let path = path_cstring(model_path.as_ref())?;
        Self::spawn(config, |config, out, error| unsafe {
            ffi::dtl_open(path.as_ptr(), config, out, error)
        })
    }
    /// Open a bounded, lazy local model registry with the library defaults. See [`Engine::open_router_with`].
    pub fn open_router(router: impl RouterSource) -> Result<Self, Error> {
        Self::open_router_with(router, &Config::default())
    }
    /// Open a bounded, lazy local model registry from router JSON (see
    /// docs/API.md) or a [`RouterConfig`]. Requests must name a route in
    /// `model`, or use `"model": "auto"` when the router has an `auto` config.
    pub fn open_router_with(router: impl RouterSource, config: &Config) -> Result<Self, Error> {
        let router = cstring(&router.router_json()?)?;
        Self::spawn(config, |config, out, error| unsafe {
            ffi::dtl_open_router(router.as_ptr(), config, out, error)
        })
    }
    /// Arguments and config are converted before the library is touched.
    fn spawn(
        config: &Config,
        open: impl FnOnce(*const c_char, *mut *mut ffi::Engine, *mut *mut c_char) -> i32,
    ) -> Result<Self, Error> {
        let config = config.to_json()?;
        check_abi()?;
        let mut raw = std::ptr::null_mut();
        let mut error = std::ptr::null_mut();
        let status = open(
            config.as_ref().map_or(std::ptr::null(), |c| c.as_ptr()),
            &mut raw,
            &mut error,
        );
        check(status, error)?;
        NonNull::new(raw).map(Self).ok_or_else(|| Error {
            status: Status::ModelError,
            message: "Native engine returned null".into(),
        })
    }
    /// Execute one request object or an array of requests with default options.
    pub fn execute(&self, request_json: &str) -> Result<String, Error> {
        self.execute_with(request_json, CallOptions::default())
    }
    /// Execute with a request id, deadline, cancellation token and/or event
    /// callback. Takes the options by value because they own the exclusive
    /// borrow of the event callback. Checks run in order: the request's
    /// conversion, an already-cancelled token, then the native call.
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
        if options
            .cancelled
            .is_some_and(CancellationToken::is_cancelled)
        {
            return Err(Error {
                status: Status::Cancelled,
                message: "The call was cancelled before it started".into(),
            });
        }
        let mut hook = options.on_event.take().map(|callback| Hook { callback });
        let mut native = unsafe { ffi::dtl_default_call_options() };
        native.request_id = options.request_id;
        native.timeout_ms = options.timeout_ms;
        if let Some(hook) = hook.as_mut() {
            native.on_event = Some(notify);
            native.event_user_data = (hook as *mut Hook<'_>).cast();
        }
        if let Some(token) = options.cancelled {
            native.should_cancel = Some(ffi::dtl_cancel_flag);
            native.cancel_user_data = token.0.as_ptr().cast();
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
                status: Status::InferenceError,
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
    const NOUL: &str = r#"{"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}"#;
    fn model() -> Option<String> {
        std::env::var("LAYA_MODEL").ok()
    }
    fn open(path: impl AsRef<Path>) -> Result<Engine, Error> {
        Engine::open_with(path, &two())
    }
    fn two() -> Config {
        Config::default().threads(2)
    }
    #[test]
    fn open_passes_path_bytes_to_the_core() {
        let missing = open("/nonexistent/m.gguf").err().unwrap();
        assert_eq!(missing.status, Status::ModelError);
        assert_eq!(
            Engine::open("/nonexistent/m.gguf").err().unwrap().status,
            Status::ModelError
        );
        #[cfg(unix)]
        {
            use std::os::unix::ffi::OsStrExt;
            let bad = std::ffi::OsStr::from_bytes(b"/tmp/\xff.gguf");
            let error = open(bad).err().unwrap();
            assert_eq!(error.status, Status::ModelError);
            assert!(!error.message.is_empty());
        }
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
            .temperatures([("noul.2", 2.0), ("choice", 1.5), ("we\"ird", 1.0)])
            .max_windows_per_call(64);
        assert_eq!(
            config.to_json().unwrap().unwrap().to_str().unwrap(),
            r#"{"threads":2,"context_tokens":1024,"temperatures":{"choice":1.5,"noul.2":2,"we\"ird":1},"max_windows_per_call":64}"#
        );
        // Zero is sent as given; the core rejects it.
        assert_eq!(
            Config::default()
                .threads(0)
                .max_windows_per_call(0)
                .to_json()
                .unwrap()
                .unwrap()
                .to_str()
                .unwrap(),
            r#"{"threads":0,"max_windows_per_call":0}"#
        );
        let bad = Config::default().temperatures([("choice", f64::NAN)]);
        assert_eq!(bad.to_json().unwrap_err().status, Status::InvalidArgument);
    }
    #[test]
    fn router_config_json_is_deterministic() {
        let router = RouterConfig::new()
            .route("b", "/m/b.gguf")
            .route_with("a", "/m/a.gguf", &Config::default().context_tokens(64))
            .max_loaded(2)
            .auto("a", "b");
        assert_eq!(
            router.to_json().unwrap(),
            r#"{"models":{"a":{"path":"/m/a.gguf","config":{"context_tokens":64}},"b":"/m/b.gguf"},"max_loaded":2,"auto":{"english":"a","default":"b"}}"#
        );
        assert_eq!(
            RouterConfig::new().route("a", "x").to_json().unwrap(),
            r#"{"models":{"a":"x"}}"#
        );
        assert_eq!("{}".router_json().unwrap(), "{}");
        assert_eq!(String::from("{}").router_json().unwrap(), "{}");
        #[cfg(unix)]
        {
            use std::os::unix::ffi::OsStrExt;
            let bad = RouterConfig::new().route("a", std::ffi::OsStr::from_bytes(b"\xff"));
            assert_eq!(bad.to_json().unwrap_err().status, Status::InvalidArgument);
        }
        // The builder's JSON reaches the core, which validates it.
        let local = RouterConfig::new()
            .route("b", "Cargo.toml")
            .route_with("a", "Cargo.toml", &Config::default().context_tokens(64))
            .max_loaded(2)
            .auto("a", "b");
        drop(Engine::open_router_with(&local, &two()).unwrap());
        let bad = RouterConfig::new()
            .route("a", "Cargo.toml")
            .auto("a", "missing");
        assert_eq!(
            Engine::open_router(&bad).err().unwrap().status,
            Status::InvalidArgument
        );
    }
    #[test]
    fn cancelled_token_fails_before_the_native_call() {
        let router = Engine::open_router(r#"{"models":{"unused":"Cargo.toml"}}"#).unwrap();
        let token = CancellationToken::new();
        assert!(!token.is_cancelled());
        token.clone().cancel();
        assert!(token.is_cancelled());
        let mut events = Vec::new();
        let mut on_event = |e: &str| events.push(e.to_owned());
        // A request that would fail the core's validation is cancelled first, with no events.
        let error = router
            .execute_with(
                "{}",
                CallOptions::default()
                    .cancellation_token(&token)
                    .on_event(&mut on_event),
            )
            .unwrap_err();
        assert_eq!(error.status, Status::Cancelled);
        assert!(events.is_empty());
        // The request's own conversion error comes first.
        let error = router
            .execute_with("\0", CallOptions::default().cancellation_token(&token))
            .unwrap_err();
        assert_eq!(error.status, Status::InvalidArgument);
    }
    #[test]
    fn contains_panicking_payload_destructors() {
        struct Bomb;
        impl Drop for Bomb {
            fn drop(&mut self) {
                std::panic::panic_any(Bomb);
            }
        }
        let engine = Engine::open_router(r#"{"models":{"unused":"Cargo.toml"}}"#).unwrap();
        let mut calls = 0;
        let mut callback = |_: &str| {
            calls += 1;
            std::panic::panic_any(Bomb);
        };
        let result = engine.execute_with("{}", CallOptions::default().on_event(&mut callback));
        assert_eq!(result.unwrap_err().status, Status::InvalidArgument);
        assert_eq!(calls, 2);
    }
    #[test]
    fn real_features() {
        let Some(path) = model() else { return };
        let engine = open(&path).unwrap();
        // Unknown top-level keys are rejected.
        let tagged = NOUL.replacen('{', "{\"metadata\":\"caller tag\",", 1);
        assert_eq!(
            engine.execute(&tagged).unwrap_err().status,
            Status::InvalidArgument
        );
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
        let router = Engine::open_router_with(
            format!(
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
        assert_eq!(
            router.execute(request).unwrap_err().status,
            Status::InvalidArgument
        );
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
        // The policy reaches the engine: the default (reject) would refuse this request.
        let engine = Engine::open_with(&path, &two().overflow("truncate_end")).unwrap();
        assert!(engine.execute(&long).is_ok());
        assert_eq!(
            Engine::open_with(&path, &two().overflow("bogus"))
                .err()
                .unwrap()
                .status,
            Status::InvalidArgument
        );
    }
    #[test]
    fn config_and_per_request_settings() {
        let Some(path) = model() else { return };
        let engine = Engine::open_with(&path, &two().context_tokens(1024)).unwrap();
        let info = engine.info().unwrap();
        assert!(info.contains("\"context_tokens\":1024"), "{info}");
        let with = |extra: &str| NOUL.replacen('{', &format!("{{{extra},"), 1);
        for extra in [
            "\"max_tokens\":1025",
            "\"max_tokens\":0",
            "\"temperatures\":{\"noul.2\":-1}",
        ] {
            assert_eq!(
                engine.execute(&with(extra)).unwrap_err().status,
                Status::InvalidArgument,
                "{extra}"
            );
        }
    }
    #[test]
    fn invalid_config_is_rejected() {
        let Some(path) = model() else { return };
        for config in [
            two().threads(257),
            two().context_tokens(8),
            two().context_tokens(u32::MAX),
            two().temperatures([("choice", 0.0)]),
            two().temperatures([("choice", -1.0)]),
        ] {
            assert_eq!(
                Engine::open_with(&path, &config).err().unwrap().status,
                Status::InvalidArgument
            );
        }
        let router = format!(r#"{{"models":{{"a":"{}"}}}}"#, escape(&path));
        assert_eq!(
            Engine::open_router_with(router.as_str(), &two().threads(257))
                .err()
                .unwrap()
                .status,
            Status::InvalidArgument
        );
        let bad = format!(
            r#"{{"models":{{"a":{{"path":"{}","config":{{"threads":999}}}}}}}}"#,
            escape(&path)
        );
        assert!(Engine::open_router_with(bad.as_str(), &two()).is_err());
    }
    #[test]
    fn router_auto_validation() {
        let open = |json: &str| Engine::open_router_with(json, &two());
        for bad in [
            r#"{"models":{"a":"Cargo.toml"},"auto":{"english":"a"}}"#,
            r#"{"models":{"a":"Cargo.toml"},"auto":{"english":"a","default":"missing"}}"#,
            r#"{"models":{"a":"Cargo.toml"},"auto":{"english":"a","default":"a","extra":"a"}}"#,
            r#"{"models":{"a":"Cargo.toml"},"auto":"a"}"#,
            r#"{"models":{"auto":"Cargo.toml"}}"#,
        ] {
            assert_eq!(
                open(bad).err().unwrap().status,
                Status::InvalidArgument,
                "{bad}"
            );
        }
        let plain = open(r#"{"models":{"a":"Cargo.toml"}}"#).unwrap();
        let auto =
            open(r#"{"models":{"a":"Cargo.toml"},"auto":{"english":"a","default":"a"}}"#).unwrap();
        let body = |extra: &str| NOUL.replacen('{', &format!("{{{extra},"), 1);
        // "model": "auto" without an auto config, and lang without auto, are rejected before any load.
        assert_eq!(
            plain
                .execute(&body("\"model\":\"auto\""))
                .unwrap_err()
                .status,
            Status::InvalidArgument
        );
        assert_eq!(
            auto.execute(&body("\"lang\":\"en\"")).unwrap_err().status,
            Status::InvalidArgument
        );
        assert_eq!(
            auto.execute(&body("\"model\":\"a\",\"lang\":\"en\""))
                .unwrap_err()
                .status,
            Status::InvalidArgument
        );
        assert_eq!(
            auto.execute(&body("\"model\":\"auto\",\"lang\":\"\""))
                .unwrap_err()
                .status,
            Status::InvalidArgument
        );
    }
    #[test]
    fn cancellation_and_deadline() {
        let Some(path) = model() else { return };
        let engine = open(&path).unwrap();
        let baseline = engine.execute(NOUL).unwrap();
        let token = CancellationToken::new();
        assert_eq!(
            engine
                .execute_with(NOUL, CallOptions::default().cancellation_token(&token))
                .unwrap(),
            baseline
        );
        token.cancel();
        assert_eq!(
            engine
                .execute_with(NOUL, CallOptions::default().cancellation_token(&token))
                .unwrap_err()
                .status,
            Status::Cancelled
        );
        assert_eq!(
            engine
                .execute_with(NOUL, CallOptions::default().timeout_ms(1))
                .unwrap_err()
                .status,
            Status::DeadlineExceeded
        );
        assert_eq!(engine.execute(NOUL).unwrap(), baseline);
    }
    #[test]
    fn status_values_match_header() {
        // Typed out again until the binding-abi policy check compares them with the header.
        assert_eq!(
            [
                Status::Ok,
                Status::InvalidArgument,
                Status::ModelError,
                Status::InferenceError,
                Status::OutOfMemory,
                Status::Cancelled,
                Status::DeadlineExceeded,
                Status::UnsupportedPlatform
            ]
            .map(|status| status as i32),
            [0, 1, 2, 3, 4, 5, 6, 7]
        );
        assert_eq!(Status::from_raw(5), Status::Cancelled);
        assert_eq!(Status::from_raw(99), Status::InferenceError);
    }
    #[test]
    fn events_carry_request_id() {
        let router = Engine::open_router(r#"{"models":{"unused":"Cargo.toml"}}"#).unwrap();
        // A request without a route is invalid; events are still delivered.
        let mut events = Vec::new();
        let mut on_event = |e: &str| events.push(e.to_owned());
        let error = router
            .execute_with(
                "{}",
                CallOptions::default().request_id(5).on_event(&mut on_event),
            )
            .unwrap_err();
        assert_eq!(error.status, Status::InvalidArgument);
        assert!(!events.is_empty());
        assert!(events.iter().all(|e| e.contains("\"request_id\":5")));
        let Some(path) = model() else { return };
        let engine = open(&path).unwrap();
        let token = CancellationToken::new();
        let mut events = Vec::new();
        let mut on_event = |e: &str| events.push(e.to_owned());
        let result = engine
            .execute_with(
                NOUL,
                CallOptions::default()
                    .request_id(10)
                    .cancellation_token(&token)
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
        let engine = open(&path).unwrap();
        let baseline = engine.execute(NOUL).unwrap();
        // Two names for one file with one slot: concurrent calls evict each other.
        let p = escape(&path);
        let router = Engine::open_router_with(
            format!(r#"{{"models":{{"a":"{p}","b":"{p}"}},"max_loaded":1}}"#),
            &two(),
        )
        .unwrap();
        let token = CancellationToken::new();
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
            let cancelled = scope.spawn(|| {
                engine.execute_with(NOUL, CallOptions::default().cancellation_token(&token))
            });
            token.cancel();
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
                Err(error) => assert_eq!(error.status, Status::Cancelled),
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
            let engine = open(&model).unwrap();
            assert!(engine
                .info()
                .unwrap()
                .contains("\"decision_type\":\"laya\""));
        }
    }
    #[test]
    fn rejects_bad_path() {
        assert!(open("missing.gguf").is_err());
    }
    #[test]
    fn rejects_nul() {
        assert_eq!(open("a\0b").err().unwrap().status, Status::InvalidArgument);
    }
}
