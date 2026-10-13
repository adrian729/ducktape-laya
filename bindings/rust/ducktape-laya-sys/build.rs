fn main() {
    println!("cargo:rerun-if-env-changed=DUCKTAPE_LAYA_LIB_DIR");
    let dir = std::env::var_os("DUCKTAPE_LAYA_LIB_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../build")
        });
    println!("cargo:rustc-link-search=native={}", dir.display());
    println!("cargo:rustc-link-lib=dylib=ducktape_laya");
}
