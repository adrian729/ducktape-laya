# Ducktape Laya for Java

Java binding for the shared `libducktape_laya.so`, using the Foreign Function & Memory API. It has no JNI, JNA or third-party dependencies, and JSON stays as `String`.

Requirements: Java 22 or newer, Linux x86-64, and the native library. No model or runtime download occurs; model files are application-owned resources.

The library is located in this order:

1. system property `ducktape.laya.library` (full path to the `.so`)
2. `$DUCKTAPE_LAYA_LIB_DIR/libducktape_laya.so`
3. a copy bundled in the jar at `/native/linux-x64/libducktape_laya.so`, extracted to a temp file
4. the default loader path (`libducktape_laya.so.1`, then `libducktape_laya.so`)

An explicit override (1 or 2) is honoured or fails; it never falls back to later steps. The Linux x86-64 check applies only when no override is given. After loading, the binding requires `dtl_abi_version() == 2` and the `dtl_cancel_flag` symbol. Loading is lazy (first API call, including `Laya.runtimeInfo()`), not in a static initializer: a failure is cached and every API call throws a fresh `LayaException` with `UNSUPPORTED_PLATFORM` (status 7) whose message includes the cause.

Run with native access enabled: `--enable-native-access=ALL-UNNAMED` on the classpath, or `--enable-native-access=io.github.adrian729.laya` on the module path.

```java
try (Laya laya = Laya.open("Laya-Q8_0.gguf", Config.none().withThreads(4))) {
    String result = laya.execute(requestJson);
    var options = CallOptions.none().withTimeout(Duration.ofSeconds(5)).withOnEvent(System.err::println);
    String bounded = laya.execute(requestJson, options);
}
```

```kotlin
Laya.open("Laya-Q8_0.gguf", Config.none().withThreads(4)).use { laya ->
    println(laya.execute(requestJson))
}
```

`Laya.open(path)` and `Laya.openRouter(routerJson)` use the library defaults. `Laya.open(path, config)` and `Laya.openRouter(routerJson, config)` take an immutable `Config` (`Config.none().withThreads(n).withContextTokens(n).withTemperatures(map)`) that is serialized to the native config JSON; unset fields are omitted and keys are written in a fixed order. `threads` must be 0..256, `contextTokens` positive and temperatures finite and above 0 (otherwise `IllegalArgumentException`); the library validates the rest (unknown temperature keys, context size limits) and reports status 1. A router opens a bounded local model registry (requests carry `"model": "id"`); the `Config` applies to every route and a route's own `config` overrides it key by key. There is one strict request contract (`execute`); per-request `"max_tokens"` and `"temperatures"` are plain request JSON keys. `info()` reports model metadata (`context_tokens`, `trained_context_tokens`, ...) for a single model and runtime information only for a router.

A `Laya` is thread-safe. Native calls on one handle serialize, inference blocks the calling thread, and `close()` waits for in-flight calls and is idempotent; later calls throw `LayaClosedException` (a `LayaException`, status 1). Closed from inside its own event callback, `close()` returns at once and the handle is released when the running call finishes. A `Cleaner` releases the native handle if a `Laya` becomes unreachable without `close()`, but that is only a safety net: call `close()` (try-with-resources) for prompt release of model memory.

`CallOptions` carries a `requestId`, a `timeout`, a `CancellationToken` and an `onEvent` consumer. Passing `null` options means `CallOptions.none()`. A timeout of zero means none; positive values round up to whole milliseconds (minimum 1 ms), up to 24 hours. A cancelled call throws status 5 and an expired one status 6, with no partial result. `onEvent` runs on the calling thread and exceptions it throws are discarded.

Cancellation: each call using a token gets a 4-byte native flag read by the library's own `dtl_cancel_flag` callback, so no Java code runs on native worker threads. `CancellationToken.cancel()` (from any thread) sets the flag of every call currently using the token, and later calls start cancelled until `reset()`, which affects future calls only.

Errors: `LayaException` with `status()` and constants such as `LayaException.CANCELLED`; `LayaClosedException` after `close()`; `UNSUPPORTED_PLATFORM` for load, platform or ABI failures. Arguments the binding validates itself throw standard exceptions: `IllegalArgumentException` for a bad timeout or thread count, `NullPointerException` for a null request, path or config. Inputs containing a NUL character are rejected with `LayaException` status 1 before reaching native code.

Build and test without Maven: `bindings/java/build.sh` (set `LAYA_MODEL` to also run the real-model tests). A `pom.xml` is provided for Maven users.
