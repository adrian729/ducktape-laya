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
pub struct Engine(NonNull<ffi::Engine>);
// SAFETY: the native ABI serializes inference on each handle. Rust borrows
// prevent Drop racing with decide; the pointer is never exposed publicly.
unsafe impl Send for Engine {}
unsafe impl Sync for Engine {}
impl Engine {
    pub fn open(model_path: &str, threads: u32) -> Result<Self, Error> {
        let path = cstring(model_path)?;
        let mut options = unsafe { ffi::dtl_default_options() };
        options.threads = threads;
        let mut raw = std::ptr::null_mut();
        let mut error = std::ptr::null_mut();
        let status = unsafe { ffi::dtl_open(path.as_ptr(), &options, &mut raw, &mut error) };
        check(status, error)?;
        NonNull::new(raw).map(Self).ok_or_else(|| Error {
            status: 2,
            message: "Native engine returned null".into(),
        })
    }
    pub fn decide(&self, request_json: &str) -> Result<String, Error> {
        let request = cstring(request_json)?;
        let mut result = std::ptr::null_mut();
        let mut error = std::ptr::null_mut();
        let status =
            unsafe { ffi::dtl_decide(self.0.as_ptr(), request.as_ptr(), &mut result, &mut error) };
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
        unsafe { ffi::dtl_close(self.0.as_ptr()) }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_bad_path() {
        assert!(Engine::open("missing.gguf", 2).is_err());
    }
    #[test]
    fn rejects_nul() {
        assert_eq!(Engine::open("a\0b", 2).err().unwrap().status, 1);
    }
}
