# Ducktape Laya for Java

Java binding for the shared `libducktape_laya.so`, using the Foreign Function & Memory API. It has no JNI, JNA or third-party dependencies, and JSON stays as `String`.

Requirements: Java 22 or newer, Linux x86-64, and the native library. No model or runtime download occurs; model files are application-owned resources.

The library is located in this order:

1. system property `ducktape.laya.library` (full path to the `.so`)
2. `$DUCKTAPE_LAYA_LIB_DIR/libducktape_laya.so`
3. a copy bundled in the jar at `/native/linux-x64/libducktape_laya.so`, extracted to a temp file
4. the default loader path (`libducktape_laya.so.0`, then `libducktape_laya.so`)

Run with native access enabled: `--enable-native-access=ALL-UNNAMED` on the classpath, or `--enable-native-access=io.github.adrian729.laya` on the module path.

```java
try (Laya laya = Laya.open("Laya-Q8_0.gguf", 4)) {
    String result = laya.execute(requestJson);
    var options = CallOptions.none().withTimeout(Duration.ofSeconds(5)).withOnEvent(System.err::println);
    String bounded = laya.execute(requestJson, options);
}
```

```kotlin
Laya.open("Laya-Q8_0.gguf", 4).use { laya ->
    println(laya.execute(requestJson))
}
```

`Laya.openRouter(configJson, threads)` opens a bounded local model registry; requests then carry `"model": "id"`. `decide` keeps the legacy primitive semantics where unrelated top-level metadata is ignored.

A `Laya` is thread-safe. Native calls on one handle serialize, inference blocks the calling thread, and `close()` waits for in-flight calls and is idempotent; later calls throw `LayaException` with status 1. Closed from inside its own event callback, `close()` returns at once and the handle is released when the running call finishes.

`CallOptions` carries a `requestId`, a `timeout` (zero for none, at most 24 hours), a `CancellationToken` and an `onEvent` consumer. Cancellation is cooperative and checked by native worker threads; a cancelled call throws status 5 and an expired one status 6, with no partial result. Event callbacks run on the calling thread, and exceptions they throw are discarded.

Errors are `LayaException` with `status()` and constants such as `LayaException.CANCELLED`. Inputs containing a NUL character are rejected with status 1 before reaching native code.

Build and test without Maven: `bindings/java/build.sh` (set `LAYA_MODEL` to also run the real-model tests). A `pom.xml` is provided for Maven users.
