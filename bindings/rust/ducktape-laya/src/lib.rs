//! Safe ownership over the shared CPU Laya engine. Inference is blocking;
//! async applications should use their runtime's blocking worker pool.
use ducktape_laya_sys as ffi;
use std::ffi::{c_char, CStr, CString};
use std::ptr::NonNull;
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
fn cstring(s: &str) -> Result<CString, Error> {
    CString::new(s).map_err(|_| Error {
        status: 1,
        message: "Input contains a NUL byte".into(),
    })
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
fn read_info(engine: *mut ffi::Engine) -> Result<String, Error> {
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
enum Handle {
    Model(NonNull<ffi::Engine>),
    Router(NonNull<ffi::Router>),
}
pub struct Engine(Handle);
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
#[derive(Default)]
pub struct CallOptions<'a> {
    /// 0 disables the deadline; otherwise 1..86400000 milliseconds.
    pub timeout_ms: u64,
    /// Cancellation is latched when observed; keep this flag alive for the call.
    pub cancelled: Option<&'a std::sync::atomic::AtomicBool>,
}
// SAFETY: the native ABI serializes inference on each handle. Rust borrows
// prevent Drop racing with decide; the pointer is never exposed publicly.
unsafe impl Send for Engine {}
unsafe impl Sync for Engine {}
impl Engine {
    /// Model metadata for a single engine; routers report common runtime capabilities.
    pub fn info(&self) -> Result<String, Error> {
        read_info(match &self.0 {
            Handle::Model(p) => p.as_ptr(),
            Handle::Router(_) => std::ptr::null_mut(),
        })
    }
    /// Open a single GGUF model. `threads` 0 selects automatic threading
    /// (capped at 4); otherwise it must be 1..=256.
    pub fn open(model_path: &str, threads: u32) -> Result<Self, Error> {
        let path = cstring(model_path)?;
        let mut options = unsafe { ffi::dtl_default_options() };
        options.threads = threads;
        let mut raw = std::ptr::null_mut();
        let mut error = std::ptr::null_mut();
        let status = unsafe { ffi::dtl_open(path.as_ptr(), &options, &mut raw, &mut error) };
        check(status, error)?;
        NonNull::new(raw)
            .map(|p| Self(Handle::Model(p)))
            .ok_or_else(|| Error {
                status: 2,
                message: "Native engine returned null".into(),
            })
    }
    /// Open a bounded, lazy local model registry. See docs/API.md for config.
    /// `threads` 0 selects automatic threading (capped at 4); otherwise 1..=256.
    pub fn open_router(config_json: &str, threads: u32) -> Result<Self, Error> {
        let config = cstring(config_json)?;
        let mut options = unsafe { ffi::dtl_default_options() };
        options.threads = threads;
        let mut raw = std::ptr::null_mut();
        let mut error = std::ptr::null_mut();
        let status =
            unsafe { ffi::dtl_router_open(config.as_ptr(), &options, &mut raw, &mut error) };
        check(status, error)?;
        NonNull::new(raw)
            .map(|p| Self(Handle::Router(p)))
            .ok_or_else(|| Error {
                status: 2,
                message: "Native router returned null".into(),
            })
    }
    /// Decide one primitive request. On a router this is strict: the request
    /// must name a configured model, as with [`Engine::execute`].
    pub fn decide(&self, request_json: &str) -> Result<String, Error> {
        self.execute_impl(request_json, 0, None, true, None)
    }
    /// Execute one primitive/structured request or a bounded JSON array.
    pub fn execute(&self, request_json: &str) -> Result<String, Error> {
        self.execute_observed(request_json, 0, None)
    }
    /// Passive events run synchronously on this thread, outside native locks.
    /// Callback panics are contained (with panic=unwind); abort panics cannot be.
    /// Callbacks may reenter; do not wait for another call that waits on yours.
    pub fn execute_observed(
        &self,
        request_json: &str,
        request_id: u64,
        callback: Option<&mut dyn FnMut(&str)>,
    ) -> Result<String, Error> {
        self.execute_impl(request_json, request_id, callback, false, None)
    }
    /// Execute with shared native cooperative cancellation/deadline controls.
    pub fn execute_with_options(
        &self,
        request_json: &str,
        options: &CallOptions<'_>,
    ) -> Result<String, Error> {
        self.controlled(request_json, 0, None, options, false)
    }
    pub fn decide_with_options(
        &self,
        request_json: &str,
        options: &CallOptions<'_>,
    ) -> Result<String, Error> {
        self.controlled(request_json, 0, None, options, true)
    }
    /// Like [`Engine::execute_observed`] and [`Engine::execute_with_options`] combined.
    pub fn execute_observed_with_options(
        &self,
        request_json: &str,
        request_id: u64,
        callback: Option<&mut dyn FnMut(&str)>,
        options: &CallOptions<'_>,
    ) -> Result<String, Error> {
        self.controlled(request_json, request_id, callback, options, false)
    }
    /// Like [`Engine::decide_with_options`] with an observer; strict on a router.
    pub fn decide_observed_with_options(
        &self,
        request_json: &str,
        request_id: u64,
        callback: Option<&mut dyn FnMut(&str)>,
        options: &CallOptions<'_>,
    ) -> Result<String, Error> {
        self.controlled(request_json, request_id, callback, options, true)
    }
    fn controlled(
        &self,
        request_json: &str,
        request_id: u64,
        callback: Option<&mut dyn FnMut(&str)>,
        options: &CallOptions<'_>,
        legacy: bool,
    ) -> Result<String, Error> {
        unsafe extern "C" fn cancelled(data: *mut std::ffi::c_void) -> u32 {
            let flag = unsafe { &*data.cast::<std::sync::atomic::AtomicBool>() };
            if flag.load(std::sync::atomic::Ordering::Relaxed) {
                5
            } else {
                0
            }
        }
        let mut native = unsafe { ffi::dtl_default_call_options() };
        native.timeout_ms = options.timeout_ms;
        if let Some(flag) = options.cancelled {
            native.should_cancel = Some(cancelled);
            native.user_data = (flag as *const std::sync::atomic::AtomicBool)
                .cast_mut()
                .cast();
        }
        self.execute_impl(request_json, request_id, callback, legacy, Some(&native))
    }
    fn execute_impl(
        &self,
        request_json: &str,
        request_id: u64,
        callback: Option<&mut dyn FnMut(&str)>,
        legacy: bool,
        options: Option<&ffi::CallOptions>,
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
        let mut hook = callback.map(|callback| Hook { callback });
        let observer = ffi::Observer {
            request_id,
            callback: hook.as_ref().map(|_| notify as unsafe extern "C" fn(_, _)),
            user_data: hook
                .as_mut()
                .map_or(std::ptr::null_mut(), |h| (h as *mut Hook<'_>).cast()),
        };
        let mut result = std::ptr::null_mut();
        let mut error = std::ptr::null_mut();
        let status = unsafe {
            match self.0 {
                Handle::Model(p) if legacy => ffi::dtl_decide_with_options(
                    p.as_ptr(),
                    request.as_ptr(),
                    &observer,
                    options.map_or(std::ptr::null(), |value| value),
                    &mut result,
                    &mut error,
                ),
                Handle::Model(p) => ffi::dtl_execute_with_options(
                    p.as_ptr(),
                    request.as_ptr(),
                    &observer,
                    options.map_or(std::ptr::null(), |value| value),
                    &mut result,
                    &mut error,
                ),
                Handle::Router(p) => ffi::dtl_router_execute_with_options(
                    p.as_ptr(),
                    request.as_ptr(),
                    &observer,
                    options.map_or(std::ptr::null(), |value| value),
                    &mut result,
                    &mut error,
                ),
            }
        };
        let output = Buffer(result);
        check(status, error)?;
        if output.0.is_null() {
            return Err(Error {
                status: 3,
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
        unsafe {
            match self.0 {
                Handle::Model(p) => ffi::dtl_close(p.as_ptr()),
                Handle::Router(p) => ffi::dtl_router_close(p.as_ptr()),
            }
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn contains_panicking_payload_destructors() {
        struct Bomb;
        impl Drop for Bomb {
            fn drop(&mut self) {
                std::panic::panic_any(Bomb);
            }
        }
        let engine = Engine::open_router(r#"{"models":{"unused":"Cargo.toml"}}"#, 1).unwrap();
        let mut calls = 0;
        let mut callback = |_: &str| {
            calls += 1;
            std::panic::panic_any(Bomb);
        };
        let result = engine.execute_observed("{}", 1, Some(&mut callback));
        assert_eq!(result.unwrap_err().status, 1);
        assert_eq!(calls, 2);
    }
    #[test]
    fn real_features() {
        let Ok(path) = std::env::var("LAYA_MODEL") else {
            return;
        };
        let engine = Engine::open(&path, 2).unwrap();
        let primitive = r#"{"state":"Refund me","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}"#;
        let tagged = primitive.replacen('{', "{\"metadata\":\"caller tag\",", 1);
        assert_eq!(
            engine.decide(&tagged).unwrap(),
            engine.decide(primitive).unwrap()
        );
        assert_eq!(engine.execute(&tagged).unwrap_err().status, 1);
        let request = r#"{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}"#;
        let single = engine.execute(request).unwrap();
        assert!(single.contains("\"values\":{\"refund\":true}"));
        let batch = format!("[{request},{request}]");
        let mut events = Vec::new();
        let result = engine
            .execute_observed(&batch, 21, Some(&mut |e| events.push(e.to_owned())))
            .unwrap();
        assert_eq!(result, format!("[{single},{single}]"));
        assert_eq!(events.len(), 2);
        assert!(events[0].contains("\"event\":\"start\""));
        assert!(events[1].contains("\"request_id\":21"));
        assert!(engine
            .execute_observed("{}", 22, Some(&mut |_| panic!("contained observer")))
            .is_err());
        // Reentry uses a distinct borrowed callback and the same native engine.
        engine
            .execute_observed(
                request,
                23,
                Some(&mut |_| {
                    engine.execute(request).unwrap();
                }),
            )
            .unwrap();
        let escaped = path.replace('\\', "\\\\").replace('"', "\\\"");
        let router = Engine::open_router(
            &format!(r#"{{"models":{{"english":"{escaped}"}},"max_loaded":1}}"#),
            2,
        )
        .unwrap();
        let routed = request.replacen('{', "{\"model\":\"english\",", 1);
        assert!(router
            .execute(&routed)
            .unwrap()
            .contains("\"model\":\"english\""));
        assert_eq!(router.execute(request).unwrap_err().status, 1);
    }
    #[test]
    fn cancellation_and_deadline() {
        let Ok(path) = std::env::var("LAYA_MODEL") else {
            return;
        };
        let engine = Engine::open(&path, 2).unwrap();
        let request = r#"{"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}"#;
        let baseline = engine.execute(request).unwrap();
        let flag = std::sync::atomic::AtomicBool::new(true);
        let options = CallOptions {
            timeout_ms: 0,
            cancelled: Some(&flag),
        };
        assert_eq!(
            engine
                .execute_with_options(request, &options)
                .unwrap_err()
                .status,
            5
        );
        assert_eq!(
            engine
                .decide_with_options(request, &options)
                .unwrap_err()
                .status,
            5
        );
        assert_eq!(
            engine
                .execute_with_options(
                    request,
                    &CallOptions {
                        timeout_ms: 1,
                        cancelled: None
                    }
                )
                .unwrap_err()
                .status,
            6
        );
        assert_eq!(engine.execute(request).unwrap(), baseline);
        flag.store(false, std::sync::atomic::Ordering::Relaxed);
        assert_eq!(
            engine.execute_with_options(request, &options).unwrap(),
            baseline
        );
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
    fn observer_with_options() {
        let router = Engine::open_router(r#"{"models":{"unused":"Cargo.toml"}}"#, 1).unwrap();
        let flag = std::sync::atomic::AtomicBool::new(true);
        let options = CallOptions {
            timeout_ms: 0,
            cancelled: Some(&flag),
        };
        // Strict decide on a router without a model: invalid argument, events still delivered.
        let mut events = Vec::new();
        let error = router
            .decide_observed_with_options(
                "{}",
                5,
                Some(&mut |e| events.push(e.to_owned())),
                &options,
            )
            .unwrap_err();
        assert!(matches!(error.status, 1 | 5));
        assert!(!events.is_empty());
        assert!(events.iter().all(|e| e.contains("\"request_id\":5")));
        let Ok(path) = std::env::var("LAYA_MODEL") else {
            return;
        };
        let engine = Engine::open(&path, 2).unwrap();
        let request = r#"{"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}"#;
        let mut events = Vec::new();
        let error = engine
            .execute_observed_with_options(
                request,
                9,
                Some(&mut |e| events.push(e.to_owned())),
                &options,
            )
            .unwrap_err();
        assert_eq!(error.status, status::CANCELLED);
        assert!(events
            .iter()
            .any(|e| e.contains("\"event\":\"error\"") && e.contains("\"request_id\":9")));
        flag.store(false, std::sync::atomic::Ordering::Relaxed);
        let mut events = Vec::new();
        let result = engine
            .decide_observed_with_options(
                request,
                10,
                Some(&mut |e| events.push(e.to_owned())),
                &options,
            )
            .unwrap();
        assert_eq!(result, engine.execute(request).unwrap());
        assert_eq!(events.len(), 2);
    }
    #[test]
    fn shared_across_threads() {
        let Ok(path) = std::env::var("LAYA_MODEL") else {
            return;
        };
        let engine = Engine::open(&path, 2).unwrap();
        let request = r#"{"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}"#;
        let baseline = engine.execute(request).unwrap();
        let escaped = path.replace('\\', "\\\\").replace('"', "\\\"");
        // Two names for one file with one slot: concurrent calls evict each other.
        let router = Engine::open_router(
            &format!(r#"{{"models":{{"a":"{escaped}","b":"{escaped}"}},"max_loaded":1}}"#),
            2,
        )
        .unwrap();
        let flag = std::sync::atomic::AtomicBool::new(false);
        std::thread::scope(|scope| {
            let callers: Vec<_> = (0..3)
                .map(|_| scope.spawn(|| engine.execute(request).unwrap()))
                .collect();
            let routed: Vec<_> = ["a", "b"]
                .map(|name| {
                    let request = request.replacen('{', &format!("{{\"model\":\"{name}\","), 1);
                    let router = &router;
                    scope.spawn(move || (name, router.execute(&request).unwrap()))
                })
                .into_iter()
                .collect();
            // Cancellation is requested from another thread while the call waits or runs.
            let cancelled = scope.spawn(|| {
                engine.execute_with_options(
                    request,
                    &CallOptions {
                        timeout_ms: 0,
                        cancelled: Some(&flag),
                    },
                )
            });
            flag.store(true, std::sync::atomic::Ordering::Relaxed);
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
                Err(error) => assert_eq!(error.status, 5),
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
        assert!(runtime.contains("\"abi_version\":1"));
        if let Ok(model) = std::env::var("LAYA_MODEL") {
            let engine = Engine::open(&model, 2).unwrap();
            assert!(engine
                .info()
                .unwrap()
                .contains("\"decision_type\":\"laya\""));
        }
    }
    #[test]
    fn rejects_bad_path() {
        assert!(Engine::open("missing.gguf", 2).is_err());
    }
    #[test]
    fn rejects_nul() {
        assert_eq!(Engine::open("a\0b", 2).err().unwrap().status, 1);
    }
}
