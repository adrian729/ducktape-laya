//! Safe ownership over the shared CPU Laya engine. Inference is blocking;
//! async applications should use their runtime's blocking worker pool.
use ducktape_laya_sys as ffi;
use std::collections::BTreeMap;
use std::ffi::{c_char, CStr, CString};
use std::path::{Path, PathBuf};
use std::ptr::NonNull;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
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
/// What [`Engine::open_router`] accepts: router JSON text (`&str`, `&String`,
/// `String`, anything `AsRef<str>` by reference) or a [`RouterConfig`] (`&RouterConfig`).
pub trait RouterSource {
    fn router_json(&self) -> Result<String, Error>;
}
impl<T: AsRef<str> + ?Sized> RouterSource for &T {
    fn router_json(&self) -> Result<String, Error> {
        Ok((*self).as_ref().to_owned())
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
/// What an [`Event`] reports: the call started, or ended with or without an error.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum EventKind {
    Start,
    End,
    Error,
}
/// A passive observer event of one call ([`CallOptions::on_event`]). It carries no
/// state, prompts, answers or paths.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Event {
    pub kind: EventKind,
    pub request_id: u64,
    /// `Status::Ok` for `Start` and `End`; the failure for `Error`.
    pub status: Status,
    /// Milliseconds since the call began inside the library; 0 for `Start`.
    pub elapsed_ms: f64,
}
impl Event {
    /// Parse the native event text, `{"elapsed_ms":X,"event":"start","request_id":N,"status":N}`;
    /// `None` when it is anything else.
    fn parse(text: &str) -> Option<Self> {
        let field = |key: &str| {
            let pattern = format!("\"{key}\":");
            let rest = &text[text.find(&pattern)? + pattern.len()..];
            Some(rest[..rest.find([',', '}'])?].trim())
        };
        let kind = match field("event")? {
            "\"start\"" => EventKind::Start,
            "\"end\"" => EventKind::End,
            "\"error\"" => EventKind::Error,
            _ => return None,
        };
        Some(Self {
            kind,
            request_id: field("request_id")?.parse().ok()?,
            status: Status::from_raw(field("status")?.parse().ok()?),
            elapsed_ms: field("elapsed_ms")?.parse().ok()?,
        })
    }
}
/// The native deadline for a call that entered `elapsed` ago: the remaining time in
/// whole milliseconds, rounded up to at least 1 and saturating at `u64::MAX` (which
/// never expires); `None` once the deadline has passed. A zero timeout means none.
fn remaining_ms(timeout: Duration, elapsed: Duration) -> Option<u64> {
    if timeout.is_zero() {
        return Some(0);
    }
    let remaining = timeout
        .checked_sub(elapsed)
        .filter(|left| !left.is_zero())?;
    Some(u64::try_from(remaining.as_nanos().div_ceil(1_000_000)).unwrap_or(u64::MAX))
}
/// Per-call controls for [`Engine::execute_with`].
#[derive(Default)]
pub struct CallOptions<'a> {
    request_id: u64,
    timeout: Duration,
    cancelled: Option<&'a CancellationToken>,
    on_event: Option<&'a mut dyn FnMut(&Event)>,
}
impl<'a> CallOptions<'a> {
    /// Caller-chosen id echoed in every event of the call (default 0).
    pub fn request_id(mut self, id: u64) -> Self {
        self.request_id = id;
        self
    }
    /// The deadline, measured from the entry of `execute_with`; `Duration::ZERO`
    /// (the default) means none. A deadline that has already passed when the native
    /// call would start fails at once with `Status::DeadlineExceeded`: no native
    /// call, no events. Any length is accepted; a huge one never expires.
    pub fn timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }
    /// Cooperative cancellation. A token that is already cancelled fails the
    /// call at once with `Status::Cancelled`: no native call, no events.
    pub fn cancellation_token(mut self, token: &'a CancellationToken) -> Self {
        self.cancelled = Some(token);
        self
    }
    /// Passive `Start` then `End`/`Error` events, run synchronously on the
    /// calling thread outside native locks. Panics are contained (with
    /// panic=unwind); abort panics cannot be. Callbacks may reenter the engine
    /// but must not drop it.
    pub fn on_event(mut self, callback: &'a mut dyn FnMut(&Event)) -> Self {
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
    /// conversion, an already-cancelled token, an already-passed deadline, then
    /// the native call.
    pub fn execute_with(
        &self,
        request_json: &str,
        options: CallOptions<'_>,
    ) -> Result<String, Error> {
        self.execute_from(Instant::now(), request_json, options)
    }
    /// `execute_with` for a call that entered at `entry`.
    fn execute_from(
        &self,
        entry: Instant,
        request_json: &str,
        mut options: CallOptions<'_>,
    ) -> Result<String, Error> {
        struct Hook<'a> {
            callback: &'a mut dyn FnMut(&Event),
        }
        unsafe extern "C" fn notify(text: *const c_char, user: *mut std::ffi::c_void) {
            let hook = unsafe { &mut *user.cast::<Hook<'_>>() };
            if let Err(payload) = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let text = unsafe { CStr::from_ptr(text) }.to_string_lossy();
                // An event that does not parse is dropped, like an observer failure.
                if let Some(event) = Event::parse(&text) {
                    (hook.callback)(&event);
                }
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
        let Some(timeout_ms) = remaining_ms(options.timeout, entry.elapsed()) else {
            return Err(Error {
                status: Status::DeadlineExceeded,
                message: "The deadline passed before the call started".into(),
            });
        };
        let mut hook = options.on_event.take().map(|callback| Hook { callback });
        let mut native = unsafe { ffi::dtl_default_call_options() };
        native.request_id = options.request_id;
        native.timeout_ms = timeout_ms;
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
impl Engine {
    /// Close the engine; the same as dropping it.
    pub fn close(self) {}
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
    fn two() -> Config {
        Config::default().threads(2)
    }
    /// A router over a file that is never loaded: `{}` fails the core's validation
    /// (status 1) after the native call has started, and emits its events.
    fn unloaded_router() -> Engine {
        Engine::open_router(r#"{"models":{"unused":"Cargo.toml"}}"#).unwrap()
    }
    fn escape(path: &str) -> String {
        path.replace('\\', "\\\\").replace('"', "\\\"")
    }
    fn with_model(request: &str, name: &str) -> String {
        request.replacen('{', &format!("{{\"model\":\"{name}\","), 1)
    }
    #[test]
    fn open_passes_path_bytes_to_the_core() {
        #[cfg(unix)]
        {
            use std::os::unix::ffi::OsStrExt;
            let bad = std::ffi::OsStr::from_bytes(b"/tmp/\xff.gguf");
            let error = Engine::open_with(bad, &two()).err().unwrap();
            assert_eq!(error.status, Status::ModelError);
            assert!(!error.message.is_empty());
        }
    }
    #[test]
    fn native_status_propagates() {
        let error = unloaded_router().execute("{}").unwrap_err();
        assert_eq!(error.status, Status::InvalidArgument);
        assert!(!error.message.is_empty());
        let error = Engine::open_router("{}").err().unwrap();
        assert_eq!(error.status, Status::InvalidArgument);
    }
    #[test]
    fn config_json_is_deterministic_and_sparse() {
        assert!(Config::default().to_json().unwrap().is_none());
        let config = Config::default()
            .threads(2)
            .context_tokens(1024)
            .overflow("scan")
            .temperatures([("noul.2", 2.0), ("choice", 1.5), ("we\"ird", 1.0)])
            .max_windows_per_call(64);
        assert_eq!(
            config.to_json().unwrap().unwrap().to_str().unwrap(),
            r#"{"threads":2,"context_tokens":1024,"overflow":"scan","temperatures":{"choice":1.5,"noul.2":2,"we\"ird":1},"max_windows_per_call":64}"#
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
        // The builder's JSON reaches the core, which accepts it.
        let local = RouterConfig::new()
            .route("b", "Cargo.toml")
            .route_with("a", "Cargo.toml", &Config::default().context_tokens(64))
            .max_loaded(2)
            .auto("a", "b");
        Engine::open_router_with(&local, &two()).unwrap().close();
    }
    #[test]
    fn cancelled_token_fails_before_the_native_call() {
        let router = unloaded_router();
        let token = CancellationToken::new();
        assert!(!token.is_cancelled());
        token.clone().cancel();
        assert!(token.is_cancelled());
        let mut events = Vec::new();
        let mut on_event = |e: &Event| events.push(*e);
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
    fn remaining_time_rounds_up_and_saturates() {
        let ms = Duration::from_millis;
        assert_eq!(remaining_ms(Duration::ZERO, ms(5)), Some(0));
        assert_eq!(remaining_ms(ms(10), Duration::ZERO), Some(10));
        assert_eq!(remaining_ms(ms(10), ms(4)), Some(6));
        assert_eq!(remaining_ms(ms(10), Duration::from_micros(9_999)), Some(1));
        assert_eq!(
            remaining_ms(Duration::from_nanos(1), Duration::ZERO),
            Some(1)
        );
        assert_eq!(remaining_ms(ms(10), ms(10)), None);
        assert_eq!(remaining_ms(ms(10), ms(11)), None);
        assert_eq!(remaining_ms(Duration::MAX, ms(1)), Some(u64::MAX));
    }
    #[test]
    fn expired_deadline_fails_at_once() {
        let router = unloaded_router();
        let mut events = Vec::new();
        let mut on_event = |e: &Event| events.push(*e);
        // A call that entered a second ago with a 1 ms deadline, so the test does not depend on the
        // clock's resolution. The request would fail the core's validation, but the deadline comes first.
        let entry = Instant::now() - Duration::from_secs(1);
        let error = router
            .execute_from(
                entry,
                "{}",
                CallOptions::default()
                    .timeout(Duration::from_millis(1))
                    .on_event(&mut on_event),
            )
            .unwrap_err();
        assert_eq!(error.status, Status::DeadlineExceeded);
        assert!(events.is_empty());
        // A cancelled token is reported before the deadline.
        let token = CancellationToken::new();
        token.cancel();
        let error = router
            .execute_from(
                entry,
                "{}",
                CallOptions::default()
                    .timeout(Duration::from_millis(1))
                    .cancellation_token(&token),
            )
            .unwrap_err();
        assert_eq!(error.status, Status::Cancelled);
    }
    #[test]
    fn any_timeout_length_reaches_the_native_call() {
        let router = unloaded_router();
        for timeout in [Duration::from_secs(365 * 86_400), Duration::MAX] {
            let mut events = Vec::new();
            let mut on_event = |e: &Event| events.push(*e);
            let error = router
                .execute_with(
                    "{}",
                    CallOptions::default()
                        .timeout(timeout)
                        .on_event(&mut on_event),
                )
                .unwrap_err();
            assert_eq!(error.status, Status::InvalidArgument);
            assert_eq!(events.len(), 2);
        }
    }
    #[test]
    fn events_are_typed() {
        for request_id in [0, 5, u64::MAX] {
            let mut events = Vec::new();
            let mut on_event = |e: &Event| events.push(*e);
            let error = unloaded_router()
                .execute_with(
                    "{}",
                    CallOptions::default()
                        .request_id(request_id)
                        .on_event(&mut on_event),
                )
                .unwrap_err();
            assert_eq!(error.status, Status::InvalidArgument);
            assert_eq!(events.len(), 2);
            assert_eq!(events[0].kind, EventKind::Start);
            assert_eq!(events[0].status, Status::Ok);
            assert_eq!(events[0].elapsed_ms, 0.0);
            assert_eq!(events[1].kind, EventKind::Error);
            assert_eq!(events[1].status, Status::InvalidArgument);
            assert!(events[1].elapsed_ms > 0.0);
            assert!(events.iter().all(|e| e.request_id == request_id));
        }
        // Without a request id the events carry 0.
        let mut ids = Vec::new();
        let mut on_event = |e: &Event| ids.push(e.request_id);
        let _ =
            unloaded_router().execute_with("{}", CallOptions::default().on_event(&mut on_event));
        assert_eq!(ids, [0, 0]);
    }
    #[test]
    fn event_parsing() {
        let event = Event::parse(
            r#"{"elapsed_ms":1.5e-05,"event":"end","request_id":18446744073709551615,"status":0}"#,
        )
        .unwrap();
        assert_eq!(
            event,
            Event {
                kind: EventKind::End,
                request_id: u64::MAX,
                status: Status::Ok,
                elapsed_ms: 1.5e-5,
            }
        );
        for bad in [
            "",
            "{}",
            r#"{"elapsed_ms":0,"event":"other","request_id":1,"status":0}"#,
            r#"{"elapsed_ms":0,"event":"start","request_id":-1,"status":0}"#,
            r#"{"elapsed_ms":0,"event":"start","request_id":1}"#,
        ] {
            assert!(Event::parse(bad).is_none(), "{bad}");
        }
    }
    #[test]
    fn contains_panicking_payload_destructors() {
        struct Bomb;
        impl Drop for Bomb {
            fn drop(&mut self) {
                std::panic::panic_any(Bomb);
            }
        }
        let engine = unloaded_router();
        let mut calls = 0;
        let mut callback = |_: &Event| {
            calls += 1;
            std::panic::panic_any(Bomb);
        };
        let result = engine.execute_with("{}", CallOptions::default().on_event(&mut callback));
        assert_eq!(result.unwrap_err().status, Status::InvalidArgument);
        assert_eq!(calls, 2);
    }
    #[test]
    fn status_from_raw() {
        // The values themselves are compared with the header by the binding-abi policy check.
        assert_eq!(Status::from_raw(5), Status::Cancelled);
        assert_eq!(Status::from_raw(99), Status::InferenceError);
    }
    #[test]
    fn diagnostics() {
        let runtime = runtime_info().unwrap();
        assert!(runtime.contains("\"abi_version\":2"));
    }
    #[test]
    fn rejects_nul() {
        assert_eq!(
            Engine::open("a\0b").err().unwrap().status,
            Status::InvalidArgument
        );
        assert_eq!(
            unloaded_router().execute("{\0}").unwrap_err().status,
            Status::InvalidArgument
        );
    }
    #[test]
    fn real_features() {
        let Some(path) = model() else { return };
        let engine = Engine::open_with(&path, &two()).unwrap();
        assert!(engine
            .info()
            .unwrap()
            .contains("\"decision_type\":\"laya\""));
        let request = r#"{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}"#;
        let single = engine.execute(request).unwrap();
        assert!(single.contains("\"values\":{\"refund\":true}"));
        let batch = format!("[{request},{request}]");
        let mut events = Vec::new();
        let mut on_event = |e: &Event| events.push(*e);
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
        assert_eq!(events[0].kind, EventKind::Start);
        assert_eq!(events[1].kind, EventKind::End);
        assert_eq!(events[1].status, Status::Ok);
        assert!(events[1].elapsed_ms > 0.0);
        assert!(events.iter().all(|e| e.request_id == 21));
        assert_eq!(
            engine
                .execute_with(
                    "{}",
                    CallOptions::default()
                        .request_id(22)
                        .on_event(&mut |_| panic!("contained observer"))
                )
                .unwrap_err()
                .status,
            Status::InvalidArgument
        );
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
    }
    /// A prompt of many windows: with `scan` and a small context it takes seconds.
    fn long_request() -> String {
        format!(
            r#"{{"state":"{}","overflow":"scan","max_windows":256,"questions":{{"refund":{{"type":"noul","instructions":"Is a refund requested?"}}}}}}"#,
            "refund ".repeat(80)
        )
    }
    #[test]
    fn cancellation_and_deadline_stop_a_running_call() {
        let Some(path) = model() else { return };
        let engine = Engine::open_with(&path, &two().context_tokens(64)).unwrap();
        let request = long_request();
        let started = Instant::now();
        engine.execute(&request).unwrap();
        let full = started.elapsed();
        assert!(full > Duration::from_secs(1), "{full:?}");
        // Cancelled from the start event: the native call stops at its next safe point.
        let token = CancellationToken::new();
        let mut on_event = |e: &Event| {
            if e.kind == EventKind::Start {
                token.cancel();
            }
        };
        let started = Instant::now();
        let error = engine
            .execute_with(
                &request,
                CallOptions::default()
                    .cancellation_token(&token)
                    .on_event(&mut on_event),
            )
            .unwrap_err();
        assert_eq!(error.status, Status::Cancelled);
        assert!(
            started.elapsed() < full / 2,
            "{:?} of {full:?}",
            started.elapsed()
        );
        // A deadline well inside the full run stops it with status 6.
        let started = Instant::now();
        let error = engine
            .execute_with(
                &request,
                CallOptions::default().timeout(Duration::from_millis(100)),
            )
            .unwrap_err();
        assert_eq!(error.status, Status::DeadlineExceeded);
        assert!(
            started.elapsed() < full / 2,
            "{:?} of {full:?}",
            started.elapsed()
        );
        // The engine still answers, and a new token is not cancelled.
        let baseline = engine.execute(NOUL).unwrap();
        assert_eq!(
            engine
                .execute_with(
                    NOUL,
                    CallOptions::default().cancellation_token(&CancellationToken::new())
                )
                .unwrap(),
            baseline
        );
    }
    #[test]
    fn shared_across_threads() {
        let Some(path) = model() else { return };
        let engine = Engine::open_with(&path, &two()).unwrap();
        let baseline = engine.execute(NOUL).unwrap();
        // Two names for one file with one slot: concurrent calls evict each other.
        let p = escape(&path);
        let router = Engine::open_router_with(
            format!(r#"{{"models":{{"a":"{p}","b":"{p}"}},"max_loaded":1}}"#),
            &two(),
        )
        .unwrap();
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
            for caller in callers {
                assert_eq!(caller.join().unwrap(), baseline);
            }
            for caller in routed {
                let (name, result) = caller.join().unwrap();
                let expected =
                    format!("{},\"model\":\"{name}\"}}", &baseline[..baseline.len() - 1]);
                assert_eq!(result, expected);
            }
        });
        // Engines may be closed on a thread other than the one that opened them.
        std::thread::spawn(move || {
            engine.close();
            router.close();
        })
        .join()
        .unwrap();
    }
}
