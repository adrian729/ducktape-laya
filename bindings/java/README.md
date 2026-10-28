# Ducktape Laya for Java

Local [Laya](https://github.com/NandhaKishorM/laya) decisions from Java and Kotlin: ask named questions about a text (`choice` picks one of several options, `noul` gives the probability that a statement is true, `score` picks a level on an ordered scale) and get probabilities. The binding calls the shared native library through the Foreign Function & Memory API, with no JNI, JNA or third-party dependencies; requests and results are JSON `String`s.

```text
io.github.adrian729:ducktape-laya:0.3.0
```

Requirements: Java 22 or newer (CI tests 25). The jar bundles the native library for Linux x86-64, Linux arm64, macOS arm64, macOS x86-64 and Windows x64 (0.2.0: Linux x86-64 only) ([platforms](https://github.com/adrian729/ducktape-laya/blob/main/docs/PLATFORMS.md)). No model or runtime download occurs; download the model separately ([models](https://github.com/adrian729/ducktape-laya/blob/main/docs/MODELS.md)).

The library is located in this order:

1. `$DUCKTAPE_LAYA_LIBRARY`, the absolute path of the library file
2. `$DUCKTAPE_LAYA_LIB_DIR/` (an absolute directory) plus the platform's file name (`libducktape_laya.so`, `libducktape_laya.dylib` or `ducktape_laya.dll`)
3. a copy bundled in the jar at `/native/<platform>/` (`linux-x64/libducktape_laya.so`, `linux-arm64/libducktape_laya.so`, `macos-arm64/libducktape_laya.dylib`, `macos-x64/libducktape_laya.dylib`, `windows-x64/ducktape_laya.dll`; released jars carry Linux x86-64 and arm64, macOS arm64 and x86-64 and Windows x64). On Linux and macOS it is extracted to a private temporary directory (mode 700; on macOS under the leaf `libducktape_laya-<first 16 hex of its sha256>.dylib`, so a same-named file on `DYLD_LIBRARY_PATH` cannot shadow it, under `java.io.tmpdir` or `-Dducktape.laya.tmpdir`, e.g. when `/tmp` is mounted `noexec`) and deleted again right after it is loaded, so nothing is left behind even if the JVM is killed. On Windows, where a loaded DLL cannot be deleted, it goes to one shared copy per library content and owner in `%LOCALAPPDATA%\ducktape-laya\<content hash>-<owner hash>\` (or under `-Dducktape.laya.tmpdir`); an administrator's elevated processes, whose files the Administrators group owns, use their own directory. The directory is created with an owner-only ACL protected from inheritance; an existing one must belong to the current user and is made owner-only again, and any entry in it other than the library and in-progress `.part` copies is removed, before the copy is compared with the bundled bytes and rewritten if it differs or belongs to someone else. A directory owned by another account is refused: an error with `-Dducktape.laya.tmpdir`, otherwise the loader falls back to a fresh owner-only directory under `java.io.tmpdir` (never a predictable name in a shared directory), as it does without `LOCALAPPDATA`. The directory given with `-Dducktape.laya.tmpdir` itself should not be writable by other users, since the checks run before the DLL is loaded. A bundled copy that cannot be loaded is an error naming it and the operating system's reason, not a fallback to step 4.
4. an installed library: on Linux `libducktape_laya.so.1`, then `libducktape_laya.so`, through the dynamic loader (`LD_LIBRARY_PATH`, the loader cache and the system directories, never the current directory); on macOS `libducktape_laya.1.dylib`, then `libducktape_laya.dylib`, by absolute path in `/opt/homebrew/lib` and `/usr/local/lib` only. Windows has no such step: a search by name would include the current directory and `PATH`.

An explicit override (1 or 2) must be an absolute path (a relative one is a `LayaLoadException`) and is honoured or fails; it never falls back to later steps. Without an override, a platform other than Linux x86-64 or arm64, macOS arm64 or x86-64, or Windows x64 fails with status 7 (build the library from source and set an override). On Alpine and other musl systems the bundled glibc library cannot load, so step 3 is skipped and only a musl build found in step 4 is used; without one the error says that the bundled library needs glibc. On macOS every load is followed by a `dladdr` check that the file providing `dtl_abi_version` is the one requested, so a library swapped in through `DYLD_LIBRARY_PATH` is refused with status 7. After loading, the binding requires `dtl_abi_version() == 2`, the `dtl_cancel_flag` symbol and a `package_version` (from `dtl_info`) equal to the jar's own, so a library of another release found elsewhere is refused. Loading is lazy (first API call, including `Engine.runtimeInfo()`): a failure throws a fresh `LayaLoadException` (a `LayaException` with `Status.UNSUPPORTED_PLATFORM`, status 7) whose message and cause give the reason, and is not cached: the next call tries again, so a library installed or an environment fixed afterwards is picked up.

Run with native access enabled: `--enable-native-access=ALL-UNNAMED` on the classpath, or `--enable-native-access=io.github.adrian729.laya` on the module path.

The snippets below are fragments: `requestJson` is your request as a JSON `String`, and the imports (`io.github.adrian729.laya.*`, `java.time.Duration`) are omitted.

```java
try (Engine laya = Engine.open("Laya-Q8_0.gguf", Config.none().withThreads(4))) {
    String result = laya.execute(requestJson);
    var options = CallOptions.none().withTimeout(Duration.ofSeconds(5)).withOnEvent(System.err::println);
    String bounded = laya.execute(requestJson, options);
}
```

```kotlin
Engine.open("Laya-Q8_0.gguf", Config.none().withThreads(4)).use { laya ->
    println(laya.execute(requestJson))
}
```

`Engine.open(path)` and `Engine.openRouter(router)` use the library defaults. `Engine.open(path, config)` and `Engine.openRouter(router, config)` take an immutable `Config` (`Config.none().withThreads(n).withContextTokens(n).withOverflow(policy).withMaxWindowsPerCall(n).withTemperatures(map)`) that is serialized to the native config JSON; a `null` config means `Config.none()`, unset (null) fields are omitted and keys are written in a fixed order. The binding sends values as given and the library validates them (status 1): thread count, context size, the overflow policy name, `max_windows_per_call` (the most forward passes one call may plan, 1..4096; 0 is sent and rejected), temperature keys and values. The binding itself rejects only what JSON cannot carry: a non-finite temperature (`LayaArgumentException`) and a null temperature key or value (`NullPointerException`). `withOverflow` sets the default policy for prompts over the limit: `reject` (default), `truncate_end`, `truncate_start` or `scan`; a request's own `overflow` wins. Paths come as `String` or `java.nio.file.Path`. A router opens a bounded local model registry: pass the router JSON as a `String`, or build a `RouterConfig(Map<String, RouterConfig.Route> models, Integer maxLoaded, RouterConfig.Auto auto)` with `Route(Path path, Config config)` and `Auto(String english, String defaultRoute)`. Requests carry `"model": "id"`, or `"model": "auto"` when the router has an `auto` entry, to choose by the state's language (`"auto"` cannot be a route ID); the `Config` applies to every route, a route's own `config` overrides it key by key, and `max_windows_per_call` belongs to the shared `Config` only. There is one strict request contract (`execute`); per-request `"max_tokens"`, `"temperatures"`, `"overflow"`, `"max_windows"` and (with `"model": "auto"`) `"lang"` are plain request JSON keys. `info()` reports model metadata (`context_tokens`, `trained_context_tokens`, ...) for a single model and runtime information only for a router.

An `Engine` is thread-safe. Native calls on one handle serialize, inference blocks the calling thread, and `close()` waits for in-flight calls and is idempotent. Calls made once `close()` has started, even while it still waits, throw `EngineClosedException` (a `LayaException`, status 1) at once. Closed from inside its own event callback, `close()` returns at once and the handle is released when the running call finishes. A `Cleaner` releases the native handle if an `Engine` becomes unreachable without `close()`, but that is only a safety net: call `close()` (try-with-resources) for prompt release of model memory.

`CallOptions` carries a `requestId`, a `timeout`, a `CancellationToken` and an `onEvent` consumer. Passing `null` options means `CallOptions.none()`. A timeout of zero means none; positive values round up to whole milliseconds (minimum 1 ms), up to 24 hours. A cancelled call throws status 5 and an expired one status 6, with no partial result. `onEvent` runs on the calling thread and exceptions it throws are discarded.

Cancellation: each call using a token gets a 4-byte native flag read by the library's own `dtl_cancel_flag` callback, so no Java code runs on native worker threads. `CancellationToken.cancel()` (from any thread) sets the flag of every call currently using the token, and later calls fail at once: a cancelled token stays cancelled, so use a new token for new calls.

Errors: `LayaException` with `status()`, a `Status` enum (`Status.CANCELLED`, ...); `EngineClosedException` after `close()`; `LayaLoadException` (status 7, `UNSUPPORTED_PLATFORM`) for load, platform, ABI and version failures. Arguments the binding cannot convert throw `LayaArgumentException` (an `IllegalArgumentException` with `status()` 1): a NUL character, invalid Unicode, a non-finite temperature, a negative request ID or an out-of-range timeout. A null request or path is a `NullPointerException`. Order of checks in a call: closed engine, then argument conversions, then an already-cancelled token (status 5 at once, with no native call and no events), then the native call; when opening, conversions come before the library loads.

## From a checkout

To use unreleased changes, build the native library ([building](https://github.com/adrian729/ducktape-laya/blob/main/docs/BUILDING.md)), then build the jar without Maven:

```sh
bindings/java/build.sh            # compiles, writes bindings/java/target/ducktape-laya.jar and runs the tests
bindings/java/build.sh --no-test  # jar only
```

`build.sh` needs JDK 22+ on `PATH` and uses `DUCKTAPE_LAYA_LIB_DIR`, defaulting to the checkout's `build` directory; set `LAYA_MODEL` to also run the real-model tests. Put the jar on your application's classpath and set `DUCKTAPE_LAYA_LIB_DIR` (or `DUCKTAPE_LAYA_LIBRARY=/absolute/path/to/libducktape_laya.so`) when running, since this development jar does not bundle the library. The release jar built by `scripts/package-native.py` bundles it and needs neither. A `pom.xml` is provided for Maven users.

The request and result format, routing and limits are in the [API guide](https://github.com/adrian729/ducktape-laya/blob/main/docs/API.md).
