//! Raw ABI v1 bindings. Pointers and lifetimes follow ducktape_laya.h.
use std::ffi::{c_char, c_void};
#[repr(C)]
pub struct Engine {
    _private: [u8; 0],
}
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Options {
    pub struct_size: u32,
    pub abi_version: u32,
    pub threads: u32,
    pub reserved: u32,
}
extern "C" {
    pub fn dtl_abi_version() -> u32;
    pub fn dtl_default_options() -> Options;
    pub fn dtl_open(
        path: *const c_char,
        options: *const Options,
        out: *mut *mut Engine,
        error: *mut *mut c_char,
    ) -> i32;
    pub fn dtl_decide(
        engine: *mut Engine,
        request: *const c_char,
        result: *mut *mut c_char,
        error: *mut *mut c_char,
    ) -> i32;
    pub fn dtl_close(engine: *mut Engine);
    pub fn dtl_free(buffer: *mut c_void);
}

#[repr(C)]
pub struct Router {
    _private: [u8; 0],
}
#[repr(C)]
pub struct Observer {
    pub request_id: u64,
    pub callback: Option<unsafe extern "C" fn(*const c_char, *mut c_void)>,
    pub user_data: *mut c_void,
}
extern "C" {
    pub fn dtl_execute(
        engine: *mut Engine,
        request: *const c_char,
        observer: *const Observer,
        result: *mut *mut c_char,
        error: *mut *mut c_char,
    ) -> i32;
    pub fn dtl_router_open(
        config: *const c_char,
        options: *const Options,
        out: *mut *mut Router,
        error: *mut *mut c_char,
    ) -> i32;
    pub fn dtl_router_execute(
        router: *mut Router,
        request: *const c_char,
        observer: *const Observer,
        result: *mut *mut c_char,
        error: *mut *mut c_char,
    ) -> i32;
    pub fn dtl_router_close(router: *mut Router);
}
