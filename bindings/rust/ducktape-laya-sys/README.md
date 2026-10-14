# Ducktape Laya

Linux native Laya GGUF decisions backed by the shared `libducktape_laya.so`.
Install the native library and set `DUCKTAPE_LAYA_LIB_DIR` to its library directory,
or expose its `ducktape-laya.pc` through `PKG_CONFIG_PATH` at build time.
Configure your application's library loader path when bundling the shared library.
No model or runtime download occurs. Model files are application-owned resources.
The safe crate provides ownership and errors; the sys crate exposes the C ABI.
