# ducktape-laya-sys

Raw Rust declarations for the C ABI of ducktape-laya's native library (`libducktape_laya.so.1`; on macOS `libducktape_laya.dylib`; on Windows `ducktape_laya.dll` with its import library `ducktape_laya.lib`). Most applications should use the safe [`ducktape-laya`](https://crates.io/crates/ducktape-laya) crate instead.

The build links the installed native library: set `DUCKTAPE_LAYA_LIB_DIR` to its `lib` directory (on Windows the zip's `lib`, with its `bin` on `PATH` at run time), or expose its `ducktape-laya.pc` through `PKG_CONFIG_PATH`. The library comes from the [release archive](https://github.com/adrian729/ducktape-laya/releases) of the same version. No model or runtime download occurs.

The C contract (ownership, statuses, call options) is in [ducktape_laya.h](https://github.com/adrian729/ducktape-laya/blob/main/include/ducktape_laya.h) and the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md).
