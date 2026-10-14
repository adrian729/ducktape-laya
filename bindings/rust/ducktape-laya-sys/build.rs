use std::path::PathBuf;
fn main() {
    println!("cargo:rerun-if-env-changed=DUCKTAPE_LAYA_LIB_DIR");
    println!("cargo:rerun-if-env-changed=PKG_CONFIG_PATH");
    let dir = if let Some(dir) = std::env::var_os("DUCKTAPE_LAYA_LIB_DIR") {
        PathBuf::from(dir)
    } else {
        let output = std::process::Command::new("pkg-config")
            .args(["--variable=libdir", "ducktape-laya"])
            .output()
            .expect("Set DUCKTAPE_LAYA_LIB_DIR or install pkg-config and ducktape-laya");
        assert!(output.status.success(), "Native ducktape-laya was not found. Set DUCKTAPE_LAYA_LIB_DIR or PKG_CONFIG_PATH to its installation.");
        PathBuf::from(
            String::from_utf8(output.stdout)
                .expect("Non-UTF8 pkg-config output")
                .trim(),
        )
    };
    assert!(
        dir.join("libducktape_laya.so").is_file(),
        "DUCKTAPE_LAYA_LIB_DIR must contain libducktape_laya.so: {}",
        dir.display()
    );
    println!("cargo:rustc-link-search=native={}", dir.display());
    println!("cargo:rustc-link-lib=dylib=ducktape_laya");
}
