//! Raw ABI v2 bindings. Pointers and lifetimes follow ducktape_laya.h.
use std::ffi::{c_char, c_void};
/// Opaque handle for a single model or a router.
#[repr(C)]
pub struct Engine {
    _private: [u8; 0],
}
pub type EventCallback = Option<unsafe extern "C" fn(*const c_char, *mut c_void)>;
pub type CancelCallback = Option<unsafe extern "C" fn(*mut c_void) -> u32>;
#[repr(C)]
#[derive(Clone, Copy)]
pub struct CallOptions {
    pub struct_size: u32,
    pub reserved: u32,
    pub request_id: u64,
    pub timeout_ms: u64,
    pub on_event: EventCallback,
    pub event_user_data: *mut c_void,
    pub should_cancel: CancelCallback,
    pub cancel_user_data: *mut c_void,
}
extern "C" {
    pub fn dtl_abi_version() -> u32;
    pub fn dtl_open(
        model_path: *const c_char,
        config_json: *const c_char,
        out: *mut *mut Engine,
        error: *mut *mut c_char,
    ) -> i32;
    pub fn dtl_open_router(
        router_json: *const c_char,
        config_json: *const c_char,
        out: *mut *mut Engine,
        error: *mut *mut c_char,
    ) -> i32;
    pub fn dtl_info(
        engine: *mut Engine,
        result_json: *mut *mut c_char,
        error: *mut *mut c_char,
    ) -> i32;
    pub fn dtl_default_call_options() -> CallOptions;
    pub fn dtl_cancel_flag(user_data: *mut c_void) -> u32;
    pub fn dtl_execute(
        engine: *mut Engine,
        request_json: *const c_char,
        options: *const CallOptions,
        result_json: *mut *mut c_char,
        error: *mut *mut c_char,
    ) -> i32;
    pub fn dtl_close(engine: *mut Engine);
    pub fn dtl_free(buffer: *mut c_void);
}
