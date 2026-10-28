package io.github.adrian729.laya;

import java.lang.foreign.Arena;
import java.lang.foreign.FunctionDescriptor;
import java.lang.foreign.Linker;
import java.lang.foreign.SymbolLookup;
import java.lang.foreign.ValueLayout;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.UserPrincipal;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.*;

/** JUnit-free test runner: exits non-zero on failure. Real-model tests need LAYA_MODEL. */
public final class EngineTest {
    private static final String MODEL = System.getenv("LAYA_MODEL");
    private static final String PRIMITIVE =
        "{\"state\":\"Refund me\",\"questions\":{\"refund\":{\"type\":\"noul\",\"instructions\":\"Is a refund requested?\"}}}";
    private static final String REQUEST =
        "{\"state\":\"Please refund me.\",\"questions\":{\"refund\":{\"type\":\"noul\",\"instructions\":\"Is a refund requested?\"}}}";
    private static final String SCHEMA =
        "{\"state\":\"Please refund me.\",\"schema\":{\"type\":\"object\",\"properties\":{\"refund\":{\"type\":\"boolean\",\"description\":\"Is a refund requested?\"}}}}";

    private static final Config ONE = Config.none().withThreads(1);
    private static final Config TWO = Config.none().withThreads(2);

    interface Case {
        void run() throws Exception;
    }

    static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    static void eq(Object expected, Object actual, String what) {
        if (!java.util.Objects.equals(expected, actual)) throw new AssertionError(what + ": expected <" + expected + "> but was <" + actual + ">");
    }

    static Throwable thrown(Case c) {
        try { c.run(); } catch (Throwable t) { return t; }
        return null;
    }

    static int statusOf(Case c) throws Exception {
        try {
            c.run();
        } catch (LayaException e) {
            return e.status().code();
        } catch (LayaArgumentException e) {
            return e.status().code();
        }
        throw new AssertionError("expected LayaException or LayaArgumentException");
    }

    static String json(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    static String withModel(String request, String id) {
        return request.replaceFirst("\\{", "{\"model\":\"" + id + "\",");
    }

    static void diagnostics() {
        check(Engine.runtimeInfo().contains("\"abi_version\":2"), "runtime info");
        if (MODEL != null) try (Engine laya = Engine.open(MODEL, TWO)) {
            String info = laya.info();
            check(info.contains("\"decision_type\":\"laya\""), "model info");
            check(info.contains("\"context_tokens\":") && info.contains("\"trained_context_tokens\":"), "context fields " + info);
            check(!info.contains("\"max_tokens\""), "no max_tokens in info");
            check(Engine.runtimeInfo().contains("\"default_context_tokens\""), "runtime default_context_tokens");
        }
    }

    static void packagePlatforms() {
        eq("linux-x64", Native.platform("linux", "amd64"), "Linux x86-64");
        eq("linux-arm64", Native.platform("linux", "aarch64"), "Linux arm64");
        eq("macos-arm64", Native.platform("mac os x", "aarch64"), "macOS arm64");
        eq("windows-x64", Native.platform("windows server 2025", "amd64"), "Windows x64");
        eq("macos-x64", Native.platform("mac os x", "x86_64"), "macOS x86-64");
        eq("macos-x64", Native.platform("mac os x", "amd64"), "macOS amd64");
        // The macOS extraction leaf carries the content hash, so a same-named file on DYLD_LIBRARY_PATH cannot shadow it.
        check(Native.uniqueLeaf(new byte[] {1}).matches("libducktape_laya-[0-9a-f]{16}\\.dylib"), "macOS extraction leaf");
        check(!Native.uniqueLeaf(new byte[] {1}).equals(Native.uniqueLeaf(new byte[] {2})), "leaf depends on content");
        eq(null, Native.platform("windows 11", "aarch64"), "Windows arm64 has no package");
        eq(null, Native.platform("freebsd", "amd64"), "FreeBSD has no package");
    }

    /** musl detection from the dynamic loaders present, and the error the loader gives on musl. */
    static void muslDetection() throws Exception {
        Path root = Files.createTempDirectory("laya-musl");
        try {
            Path alpine = Files.createDirectories(root.resolve("alpine")), debian = Files.createDirectories(root.resolve("debian"));
            Path gcompat = Files.createDirectories(root.resolve("gcompat")), empty = Files.createDirectories(root.resolve("empty"));
            Files.createFile(alpine.resolve("ld-musl-x86_64.so.1"));
            Files.createFile(alpine.resolve("libc.musl-x86_64.so.1"));
            Files.createFile(debian.resolve("ld-linux-x86-64.so.2"));
            Files.createFile(gcompat.resolve("ld-musl-aarch64.so.1"));
            Files.createFile(gcompat.resolve("ld-linux-aarch64.so.1"));
            Path missing = root.resolve("missing");
            eq(true, Native.isMusl(alpine), "Alpine");
            eq(true, Native.isMusl(alpine, missing), "Alpine without /lib64");
            eq(false, Native.isMusl(debian), "glibc");
            eq(false, Native.isMusl(gcompat), "musl with a glibc loader (gcompat)");
            eq(false, Native.isMusl(alpine, debian), "a glibc loader in another directory");
            eq(false, Native.isMusl(empty, missing), "no loader");
        } finally {
            deleteRecursively(root);
        }
        LayaException e = Native.muslUnsupported();
        eq(Status.UNSUPPORTED_PLATFORM, e.status(), "musl status");
        check(e.getMessage().contains("needs glibc, and this looks like a musl system"), "musl message " + e.getMessage());
        // This host is not musl (the suite runs on glibc, macOS and Windows).
        eq(false, Native.isMusl(), "host detected as musl");
    }

    static void configJson() {
        check(Config.none().toJson() == null, "empty config");
        eq("{\"threads\":0}", Config.none().withThreads(0).toJson(), "threads only");
        eq("{\"threads\":2,\"context_tokens\":64,\"temperatures\":{\"choice\":1.5,\"noul.2\":2.0}}",
            Config.none().withTemperatures(Map.of("noul.2", 2.0, "choice", 1.5)).withContextTokens(64).withThreads(2).toJson(), "full config");
        eq("{\"threads\":2,\"context_tokens\":64,\"overflow\":\"scan\",\"temperatures\":{\"choice\":1.5}}",
            Config.none().withTemperatures(Map.of("choice", 1.5)).withOverflow("scan").withContextTokens(64).withThreads(2).toJson(), "overflow position");
        eq("{\"threads\":2,\"max_windows_per_call\":64}", Config.none().withMaxWindowsPerCall(64).withThreads(2).toJson(), "ceiling key");
        eq("{\"max_windows_per_call\":0}", Config.none().withMaxWindowsPerCall(0).toJson(), "0 is sent, the core rejects it");
        eq("{\"temperatures\":{\"a\\\"b\":1.0}}", Config.none().withTemperatures(Map.of("a\"b", 1.0)).toJson(), "escaped key");
    }

    static void rejectsBadPath() throws Exception {
        check(statusOf(() -> Engine.open("missing.gguf", TWO)) != 0, "bad path");
    }

    static void rejectsNul() throws Exception {
        eq(1, statusOf(() -> Engine.open("a\0b", TWO)), "open NUL");
        eq(1, statusOf(() -> Engine.openRouter("{\0}", ONE)), "router NUL");
    }

    static void rejectsInvalidUnicode() throws Exception {
        String lone = "a\ud800b";
        eq(1, statusOf(() -> Engine.open(lone, TWO)), "open lone surrogate");
        eq(1, statusOf(() -> Engine.openRouter("{\"models\":{\"x\":\"" + lone + "\"}}", ONE)), "router lone surrogate");
        eq(1, statusOf(() -> Engine.open("x", Config.none().withTemperatures(Map.of(lone, 1.0)))), "config lone surrogate");
        Path file = Files.createTempFile("laya", ".gguf");
        try (Engine router = Engine.openRouter("{\"models\":{\"x\":\"" + json(file.toString()) + "\"}}")) {
            check(thrown(() -> router.execute("{\"state\":\"" + lone + "\"}")) instanceof LayaArgumentException e && e.status() == Status.INVALID_ARGUMENT
                && e.getMessage().equals("request is not valid Unicode"), "request lone surrogate");
            // A valid surrogate pair is accepted (the library rejects the request for other reasons).
            check(thrown(() -> router.execute("{\"state\":\"\uD83D\uDE00\"}")) instanceof LayaException e && !e.getMessage().contains("Unicode"), "pair");
        } finally {
            Files.deleteIfExists(file);
        }
    }

    @SuppressWarnings("try") // closes the resource inside its own callback on purpose
    static void containsObserverFailures() throws Exception {
        Path file = Files.createTempFile("laya", ".gguf");
        try (Engine router = Engine.openRouter("{\"models\":{\"unused\":\"" + json(file.toString()) + "\"}}", ONE)) {
            int[] calls = {0};
            CallOptions options = CallOptions.none().withRequestId(1).withOnEvent(e -> {
                calls[0]++;
                throw new IllegalStateException("contained");
            });
            eq(1, statusOf(() -> router.execute("{}", options)), "invalid request status");
            eq(2, calls[0], "observer calls");
            int[] nested = {-1};
            CallOptions closing = CallOptions.none().withOnEvent(e -> {
                router.close();
                try { router.info(); } catch (LayaException x) { nested[0] = x.status().code(); }
            });
            eq(1, statusOf(() -> router.execute("{}", closing)), "close-in-callback request status");
            eq(1, nested[0], "calls rejected after close inside callback");
            eq(1, statusOf(() -> router.execute("{}")), "closed after the call returned");
            router.close();
        } finally {
            Files.deleteIfExists(file);
        }
    }

    @SuppressWarnings("try")
    static void pendingCloseFailsFast() throws Exception {
        Path file = Files.createTempFile("laya", ".gguf");
        try {
            Engine router = Engine.openRouter("{\"models\":{\"unused\":\"" + json(file.toString()) + "\"}}", ONE);
            ExecutorService pool = Executors.newCachedThreadPool();
            try {
                Future<?>[] closer = {null};
                boolean[] failedFast = {false};
                CallOptions options = CallOptions.none().withOnEvent(e -> {
                    if (closer[0] != null) return;
                    // close() on another thread waits for this call; calls from other threads meanwhile must
                    // fail instead of queueing behind it (this callback waits for them).
                    closer[0] = pool.submit(router::close);
                    long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(30);
                    try {
                        while (!failedFast[0] && System.nanoTime() < deadline) {
                            Future<Boolean> probe = pool.submit(() -> {
                                try { router.execute("{}"); return false; }
                                catch (EngineClosedException x) { return true; }
                                catch (LayaException x) { return false; }
                            });
                            failedFast[0] = probe.get(10, TimeUnit.SECONDS);
                        }
                    } catch (Exception x) {
                        throw new AssertionError("a new call blocked behind the pending close: " + x);
                    }
                });
                try { router.execute("{}", options); } catch (LayaException expected) { /* the invalid request */ }
                check(failedFast[0], "new calls fail fast while close is pending");
                closer[0].get(10, TimeUnit.SECONDS);
                check(thrown(() -> router.execute("{}")) instanceof EngineClosedException, "closed after the call returned");
            } finally {
                pool.shutdownNow();
            }
        } finally {
            Files.deleteIfExists(file);
        }
    }

    static void routerAutoValidation() throws Exception {
        Path file = Files.createTempFile("laya", ".gguf");
        try {
            String models = "\"models\":{\"a\":\"" + json(file.toString()) + "\"}";
            String[][] bad = {
                {"{\"english\":\"a\"}", "auto.default must name a route"},
                {"{\"english\":\"zz\",\"default\":\"a\"}", "auto.english must name a route"},
                {"[]", "auto must be an object"},
                {"{\"english\":\"a\",\"default\":\"a\",\"x\":\"a\"}", "auto: unsupported key x"},
            };
            for (String[] c : bad) {
                Throwable t = thrown(() -> Engine.openRouter("{" + models + ",\"auto\":" + c[0] + "}", ONE));
                check(t instanceof LayaException e && e.status() == Status.INVALID_ARGUMENT && e.getMessage().contains(c[1]), c[0] + " -> " + t);
            }
            try (Engine router = Engine.openRouter("{" + models + "}", ONE)) {
                Throwable noAuto = thrown(() -> router.execute(withModel(PRIMITIVE, "auto")));
                check(noAuto instanceof LayaException e && e.status() == Status.INVALID_ARGUMENT && e.getMessage().contains("requires an \"auto\" router config"), "auto without config: " + noAuto);
                Throwable lang = thrown(() -> router.execute(withModel(PRIMITIVE, "a").replaceFirst("\\{", "{\"lang\":\"en\",")));
                check(lang instanceof LayaException e && e.status() == Status.INVALID_ARGUMENT && e.getMessage().contains("lang"), "lang without auto: " + lang);
            }
        } finally {
            Files.deleteIfExists(file);
        }
    }

    static void closeIsIdempotent() throws Exception {
        Path file = Files.createTempFile("laya", ".gguf");
        try {
            Engine router = Engine.openRouter("{\"models\":{\"x\":\"" + json(file.toString()) + "\"}}", ONE);
            router.close();
            router.close();
            eq(1, statusOf(() -> router.execute("{}")), "closed execute");
            check(thrown(() -> router.execute("{}")) instanceof EngineClosedException, "closed exception type");
            eq(1, statusOf(router::info), "closed info");
        } finally {
            Files.deleteIfExists(file);
        }
    }

    static void realFeatures() throws Exception {
        try (Engine laya = Engine.open(MODEL, TWO)) {
            String tagged = PRIMITIVE.replaceFirst("\\{", "{\"metadata\":\"caller tag\",");
            eq(1, statusOf(() -> laya.execute(tagged)), "execute rejects metadata");
            String single = laya.execute(SCHEMA);
            check(single.contains("\"values\":{\"refund\":true}"), "schema result " + single);
            List<String> events = new ArrayList<>();
            String batch = laya.execute("[" + SCHEMA + "," + SCHEMA + "]",
                CallOptions.none().withRequestId(21).withOnEvent(events::add));
            eq("[" + single + "," + single + "]", batch, "batch");
            eq(2, events.size(), "event count");
            check(events.get(0).contains("\"event\":\"start\""), "start event");
            check(events.get(1).contains("\"request_id\":21"), "request id");
            check(statusOf(() -> laya.execute("{}", CallOptions.none().withOnEvent(e -> {
                throw new RuntimeException("contained observer");
            }))) != 0, "failing observer on invalid request");
            laya.execute(SCHEMA, CallOptions.none().withRequestId(23).withOnEvent(e -> laya.execute(SCHEMA)));

            try (Engine router = Engine.openRouter("{\"models\":{\"english\":\"" + json(MODEL) + "\"},\"max_loaded\":1}", TWO)) {
                check(router.execute(withModel(SCHEMA, "english")).contains("\"model\":\"english\""), "routed");
                eq(1, statusOf(() -> router.execute(SCHEMA)), "router needs model");
            }
        }
    }

    static void configAndPerRequestSettings() throws Exception {
        String choice = "{\"state\":\"Please refund me.\",\"questions\":{\"q\":{\"type\":\"choice\",\"instructions\":\"Is a refund requested?\",\"criteria\":[\"yes\",\"no\"]}}}";
        try (Engine laya = Engine.open(MODEL, TWO)) {
            eq(1, statusOf(() -> laya.execute(choice.replaceFirst("\\{", "{\"max_tokens\":0,"))), "max_tokens 0");
            eq(1, statusOf(() -> laya.execute(choice.replaceFirst("\\{", "{\"max_tokens\":100000000,"))), "max_tokens above context");
            eq(1, statusOf(() -> laya.execute(choice.replaceFirst("\\{", "{\"temperatures\":{\"choice\":0},"))), "temperature 0");
        }
        Config configured = TWO.withContextTokens(128).withTemperatures(Map.of("choice.2", 3.0));
        try (Engine laya = Engine.open(MODEL, configured)) {
            check(laya.info().contains("\"context_tokens\":128"), "configured context tokens");
        }
        eq(1, statusOf(() -> Engine.open(MODEL, TWO.withContextTokens(1))), "context_tokens below minimum");
        eq(1, statusOf(() -> Engine.open(MODEL, TWO.withOverflow("bogus"))), "invalid overflow");
        String longRequest = "{\"state\":\"" + "refund ".repeat(800) + "\",\"questions\":{\"refund\":{\"type\":\"noul\",\"instructions\":\"Is a refund requested?\"}}}";
        // The policy reaches the engine: the default (reject) would refuse this request.
        try (Engine laya = Engine.open(MODEL, TWO.withOverflow("truncate_end"))) {
            check(laya.execute(longRequest).contains("\"answers\""), "truncate_end accepts a long request");
        }
        eq(1, statusOf(() -> Engine.open(MODEL, TWO.withTemperatures(Map.of("bogus.key", 1.5)))), "unknown temperature key");
        try (Engine router = Engine.openRouter("{\"models\":{\"plain\":\"" + json(MODEL) + "\"},\"max_loaded\":1}", TWO)) {
            check(router.info().contains("\"default_context_tokens\""), "router info is runtime only");
        }
    }

    static void cancellationAndDeadline() throws Exception {
        try (Engine laya = Engine.open(MODEL, TWO)) {
            String baseline = laya.execute(REQUEST);
            CancellationToken token = new CancellationToken();
            token.cancel();
            CallOptions cancelled = CallOptions.none().withCancellation(token);
            eq(5, statusOf(() -> laya.execute(REQUEST, cancelled)), "pre-cancelled execute");
            eq(6, statusOf(() -> laya.execute(REQUEST, CallOptions.none().withTimeout(Duration.ofMillis(1)))), "deadline");
            eq(baseline, laya.execute(REQUEST), "unchanged after stop");
            eq(baseline, laya.execute(REQUEST, CallOptions.none().withCancellation(new CancellationToken())), "uncancelled token");
            check(thrown(() -> CallOptions.none().withTimeout(Duration.ofDays(2))) instanceof IllegalArgumentException, "timeout range");
            // Cancelled from another thread while the call is running (the start event fires first).
            CancellationToken live = new CancellationToken();
            CallOptions viaThread = CallOptions.none().withCancellation(live).withOnEvent(e -> {
                Thread t = new Thread(live::cancel);
                t.start();
                try { t.join(); } catch (InterruptedException x) { Thread.currentThread().interrupt(); }
            });
            eq(5, statusOf(() -> laya.execute(REQUEST, viaThread)), "cancel from another thread");
            check(live.isCancelled(), "token stays cancelled");
            eq(5, statusOf(() -> laya.execute(REQUEST, CallOptions.none().withCancellation(live))), "a cancelled token stays cancelled");
        }
    }

    static void nullOptions() throws Exception {
        try (Engine laya = Engine.open(MODEL)) {
            String baseline = laya.execute(PRIMITIVE);
            eq(baseline, laya.execute(PRIMITIVE, null), "null options execute");
        }
    }

    static void argumentValidation() throws Exception {
        eq(0L, CallOptions.none().timeoutMillis(), "none");
        eq(1L, CallOptions.none().withTimeout(Duration.ofNanos(1)).timeoutMillis(), "1ns");
        eq(1L, CallOptions.none().withTimeout(Duration.ofMillis(1)).timeoutMillis(), "1ms");
        eq(2L, CallOptions.none().withTimeout(Duration.ofNanos(1_000_001)).timeoutMillis(), "1.000001ms");
        eq(86_400_000L, CallOptions.none().withTimeout(Duration.ofHours(24)).timeoutMillis(), "24h");
        check(thrown(() -> CallOptions.none().withTimeout(Duration.ofHours(24).plusNanos(1))) instanceof IllegalArgumentException, ">24h");
        check(thrown(() -> CallOptions.none().withTimeout(Duration.ofMillis(-1))) instanceof IllegalArgumentException, "negative");
        check(thrown(() -> CallOptions.none().withRequestId(-1)) instanceof IllegalArgumentException, "requestId -1");
        eq(Long.MAX_VALUE, CallOptions.none().withRequestId(Long.MAX_VALUE).requestId(), "requestId max");
        check(thrown(() -> Config.none().withTemperatures(Map.of("choice", Double.NaN))) instanceof LayaArgumentException, "temp NaN");
        eq(2, statusOf(() -> Engine.open("missing.gguf", null)), "null config opens with defaults");
        eq(2, statusOf(() -> Engine.open(Path.of("missing.gguf"))), "Path overload reaches the core");
        check(thrown(() -> Engine.open((String) null)) instanceof NullPointerException, "null path");
        check(thrown(() -> Engine.openRouter((String) null)) instanceof NullPointerException, "null config");
        Path file = Files.createTempFile("laya", ".gguf");
        try (Engine router = Engine.openRouter("{\"models\":{\"x\":\"" + json(file.toString()) + "\"}}")) {
            check(thrown(() -> router.execute(null)) instanceof NullPointerException, "null request");
            check(thrown(() -> router.execute("{}", null)) instanceof LayaException e && e.status() == Status.INVALID_ARGUMENT, "null options accepted");
        } finally {
            Files.deleteIfExists(file);
        }
    }

    /** Runs a child JVM to completion within two minutes and returns its output (stdout and stderr). */
    static String runChild(ProcessBuilder pb, String what) throws Exception {
        Path log = Files.createTempFile("laya-child", ".log");
        try {
            Process p = pb.redirectErrorStream(true).redirectOutput(log.toFile()).start();
            if (!p.waitFor(120, TimeUnit.SECONDS)) {
                p.destroyForcibly().waitFor();
                throw new AssertionError(what + ": child did not finish in 120 s: " + Files.readString(log));
            }
            String out = Files.readString(log);
            eq(0, p.exitValue(), what + ": " + out);
            return out;
        } finally {
            Files.deleteIfExists(log);
        }
    }

    static List<String> childJava(String... args) {
        List<String> cmd = new ArrayList<>(List.of(ProcessHandle.current().info().command().orElse("java"), "--enable-native-access=ALL-UNNAMED"));
        cmd.addAll(List.of(args));
        return cmd;
    }

    /** Runs this class in a fresh JVM with a broken library selection; the child verifies the errors. */
    static void loadFailure() throws Exception {
        String cp = System.getProperty("java.class.path");
        // library override missing, directory override missing, relative overrides refused
        String[][] cases = {{"/nonexistent/" + Native.LIB, null}, {null, "/nonexistent"}, {"nonexistent/" + Native.LIB, null}, {null, "nonexistent"}};
        for (String[] c : cases) {
            List<String> cmd = childJava();
            cmd.addAll(List.of("-cp", cp, "io.github.adrian729.laya.EngineTest", "--load-failure"));
            ProcessBuilder pb = new ProcessBuilder(cmd);
            withoutLibraryPaths(pb);
            if (c[0] != null) pb.environment().put("DUCKTAPE_LAYA_LIBRARY", c[0]);
            if (c[1] != null) pb.environment().put("DUCKTAPE_LAYA_LIB_DIR", c[1]);
            runChild(pb, "load failure (" + c[0] + ", " + c[1] + ")");
        }
    }

    /** A load failure is not cached: the child fails, the library appears at the override, and the next call loads. */
    static void loadRetries() throws Exception {
        String libDir = System.getenv("DUCKTAPE_LAYA_LIB_DIR");
        if (libDir == null) {
            System.out.println("SKIP loadRetries (DUCKTAPE_LAYA_LIB_DIR unset)");
            return;
        }
        Path dir = Files.createTempDirectory("laya-retry");
        try {
            Path target = dir.resolve(Native.LIB);
            List<String> cmd = childJava();
            cmd.addAll(List.of("-cp", System.getProperty("java.class.path"), "io.github.adrian729.laya.EngineTest", "--retry-child",
                target.toString(), Path.of(libDir, Native.LIB).toString()));
            ProcessBuilder pb = new ProcessBuilder(cmd);
            withoutLibraryPaths(pb);
            pb.environment().put("DUCKTAPE_LAYA_LIBRARY", target.toString());
            runChild(pb, "load retry");
        } finally {
            deleteRecursively(dir);
        }
    }

    static void overridesAndVersion() {
        eq(null, Native.override(null, null), "no override");
        eq(null, Native.override("", ""), "empty override");
        Path abs = Path.of(System.getProperty("java.io.tmpdir")).toAbsolutePath();
        eq(abs.resolve("x"), Native.override(abs.resolve("x").toString(), "ignored"), "library wins over directory");
        eq(abs.resolve(Native.LIB), Native.override(null, abs.toString()), "directory names the library");
        check(thrown(() -> Native.override("rel/lib.so", null)) instanceof LayaLoadException, "relative library refused");
        check(thrown(() -> Native.override(null, "rel")) instanceof LayaLoadException, "relative directory refused");
        Native.checkVersion("{\"package_version\":\"0.3.0\",\"abi_version\":2}", "0.3.0");
        check(thrown(() -> Native.checkVersion("{\"package_version\":\"0.2.0\"}", "0.3.0")) instanceof LayaLoadException e
            && e.getMessage().contains("0.2.0") && e.status() == Status.UNSUPPORTED_PLATFORM, "version mismatch");
        check(thrown(() -> Native.checkVersion("{}", "0.3.0")) instanceof LayaLoadException, "missing version");
        eq(0, Status.OK.code(), "OK");
        eq(Status.CANCELLED, Status.of(5), "Status.of");
        eq(Status.INFERENCE_ERROR, Status.of(99), "unknown status");
        eq(8L, Native.REQUEST_ID, "request_id offset");
        eq(48L, Native.CANCEL_USER_DATA, "cancel_user_data offset");
    }

    static void routerConfigJson() {
        RouterConfig router = new RouterConfig(Map.of(
            "b", new RouterConfig.Route(Path.of("/m/b.gguf"), Config.none().withContextTokens(64)),
            "a", new RouterConfig.Route(Path.of("/m/a.gguf"))), 2, new RouterConfig.Auto("a", "b"));
        // Paths are written as the OS renders them (backslashes on Windows, escaped in JSON).
        String a = Path.of("/m/a.gguf").toString().replace("\\", "\\\\"), b = Path.of("/m/b.gguf").toString().replace("\\", "\\\\");
        eq("{\"models\":{\"a\":{\"path\":\"" + a + "\"},\"b\":{\"path\":\"" + b + "\",\"config\":{\"context_tokens\":64}}},"
            + "\"max_loaded\":2,\"auto\":{\"english\":\"a\",\"default\":\"b\"}}", router.toJson(), "router json");
        eq("{\"models\":{}}", new RouterConfig(Map.of()).toJson(), "empty router");
    }

    /** Closed beats a bad argument, which beats an already-cancelled token; a cancelled call makes no native call. */
    static void errorOrder() throws Exception {
        Path file = Files.createTempFile("laya", ".gguf");
        try {
            Engine router = Engine.openRouter(new RouterConfig(Map.of("x", new RouterConfig.Route(file))));
            CancellationToken token = new CancellationToken();
            token.cancel();
            CallOptions cancelled = CallOptions.none().withCancellation(token).withOnEvent(e -> { throw new AssertionError("native call ran"); });
            check(thrown(() -> router.execute("a\0b", cancelled)) instanceof LayaArgumentException, "bad argument beats cancelled");
            check(thrown(() -> router.execute("{}", cancelled)) instanceof LayaException e && e.status() == Status.CANCELLED, "cancelled fails at once");
            router.close();
            check(thrown(() -> router.execute("a\0b", cancelled)) instanceof EngineClosedException, "closed beats bad argument and cancelled");
        } finally {
            Files.deleteIfExists(file);
        }
    }

    /** Removes the library override and the loader search paths from a child's environment: macOS dyld
     *  searches DYLD_LIBRARY_PATH for the file name even when a library is loaded by full path, so the
     *  child would get the build's library instead of the bundled copy. */
    static void withoutLibraryPaths(ProcessBuilder pb) {
        for (String name : List.of("DUCKTAPE_LAYA_LIBRARY", "DUCKTAPE_LAYA_LIB_DIR", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"))
            pb.environment().remove(name);
    }

    static String hostPlatform() {
        return Native.platform(System.getProperty("os.name", "").toLowerCase(java.util.Locale.ROOT),
            System.getProperty("os.arch", "").toLowerCase(java.util.Locale.ROOT));
    }

    /** A jar-bundled library is loaded from a private extraction directory that is empty again
     *  right after loading (Linux/macOS), and a shared Windows-style extraction reuses its copy.
     *  A bundled file that cannot be loaded is reported as found but unloadable, not as missing. */
    static void bundledExtraction() throws Exception {
        String libDir = System.getenv("DUCKTAPE_LAYA_LIB_DIR");
        String platform = hostPlatform();
        if (libDir == null || platform == null || !Files.isRegularFile(Path.of(libDir, Native.LIB))) {
            System.out.println("SKIP bundledExtraction (no library to bundle)");
            return;
        }
        boolean windows = platform.startsWith("windows");
        for (boolean broken : new boolean[] {false, true}) {
            Path resources = Files.createTempDirectory("laya-res");
            Path tmp = Files.createTempDirectory("laya-tmp");
            try {
                Path nativeDir = Files.createDirectories(resources.resolve("native").resolve(platform));
                if (broken) Files.write(nativeDir.resolve(Native.LIB), new byte[] {'n', 'o', 't', ' ', 'a', ' ', 'l', 'i', 'b'});
                else Files.copy(Path.of(libDir, Native.LIB), nativeDir.resolve(Native.LIB));
                String cp = System.getProperty("java.class.path") + java.io.File.pathSeparator + resources;
                ProcessBuilder pb = new ProcessBuilder(childJava("-Dducktape.laya.tmpdir=" + tmp,
                    "-cp", cp, "io.github.adrian729.laya.EngineTest", broken ? "--bundled-broken-child" : "--bundled-child", platform));
                withoutLibraryPaths(pb);
                runChild(pb, broken ? "unloadable bundled child" : "bundled child");
                if (broken) continue;
                try (var entries = Files.list(tmp)) {
                    List<Path> left = entries.toList();
                    if (windows) {
                        // One shared copy in tmp/<content hash>-<owner hash>/, which only its owner can modify.
                        eq(1, left.size(), "one shared copy " + left);
                        Path copy = left.get(0).resolve(Native.LIB);
                        check(Files.isRegularFile(copy), "shared copy " + copy);
                        UserPrincipal owner = Files.getOwner(copy);
                        eq(Native.sharedName(Files.readAllBytes(copy), owner), left.get(0).getFileName().toString(), "shared directory name");
                        check(!Native.writableByOthers(left.get(0), owner) && !Native.writableByOthers(copy, owner),
                            "shared copy writable by others: " + acl(left.get(0)) + " " + acl(copy));
                    } else eq(List.<Path>of(), left, "extraction directory left behind");
                }
            } finally {
                deleteRecursively(resources);
                deleteRecursively(tmp);
            }
        }
        // An extraction directory that does not exist is an extraction error, not a load error (Linux and
        // macOS; on Windows the shared base directory is created).
        if (!windows) {
            Path resources = Files.createTempDirectory("laya-res");
            try {
                Files.copy(Path.of(libDir, Native.LIB), Files.createDirectories(resources.resolve("native").resolve(platform)).resolve(Native.LIB));
                ProcessBuilder pb = new ProcessBuilder(childJava("-Dducktape.laya.tmpdir=" + resources.resolve("no-such-dir"),
                    "-cp", System.getProperty("java.class.path") + java.io.File.pathSeparator + resources, "io.github.adrian729.laya.EngineTest",
                    "--bundled-missing-tmpdir-child", platform));
                withoutLibraryPaths(pb);
                runChild(pb, "missing tmpdir child");
            } finally {
                deleteRecursively(resources);
            }
        }
        // Shared extraction: one copy per content, reused, and a tampered copy is replaced.
        Path base = Files.createTempDirectory("laya-shared");
        try {
            byte[] bytes = {1, 2, 3, 4};
            Path first = Native.extractShared(base, bytes);
            eq(first, Native.extractShared(base, bytes), "shared copy reused");
            Files.write(first, new byte[] {9, 9});
            eq(first, Native.extractShared(base, bytes), "tampered copy replaced");
            check(java.util.Arrays.equals(bytes, Files.readAllBytes(first)), "tampered copy restored");
            // Entries other than the copy and in-progress .part files are removed; one that cannot be is refused.
            Path dir = first.getParent();
            Files.write(dir.resolve("vcruntime140.dll"), bytes);
            Files.write(dir.resolve("ducktape_laya123.part"), bytes);
            eq(first, Native.extractShared(base, bytes), "copy used after removing strays");
            check(!Files.exists(dir.resolve("vcruntime140.dll")), "stray file removed");
            check(Files.exists(dir.resolve("ducktape_laya123.part")), "in-progress copy kept");
            Files.write(Files.createDirectories(dir.resolve("nested")).resolve("x"), bytes);
            Throwable refused = thrown(() -> Native.extractShared(base, bytes));
            check(refused instanceof java.io.IOException && refused.getMessage().contains("unexpected entry"), "non-empty stray refused: " + refused);
        } finally {
            deleteRecursively(base);
        }
        // The shared directory name depends on the owner: an elevated administrator's processes (files owned
        // by the Administrators group) and the same user's ordinary processes use different directories.
        UserPrincipal user = () -> "HOST\\user", administrators = () -> "BUILTIN\\Administrators";
        byte[] bytes = {1, 2, 3};
        check(!Native.sharedName(bytes, user).equals(Native.sharedName(bytes, administrators)), "owner in the shared name");
        check(Native.sharedName(bytes, user).matches("[0-9a-f]{32}-[0-9a-f]{8}"), "shared name " + Native.sharedName(bytes, user));
        check(!Native.sharedName(bytes, user).equals(Native.sharedName(new byte[] {1, 2}, user)), "content in the shared name");
    }

    /** True when this process runs elevated (Windows high integrity level): icacls /setowner to another
     *  account needs the restore privilege, which only an elevated administrator holds. */
    private static boolean elevated() throws Exception {
        Process p = new ProcessBuilder("whoami", "/groups").redirectErrorStream(true).start();
        String out = new String(p.getInputStream().readAllBytes());
        return p.waitFor() == 0 && (out.contains("S-1-16-12288") || out.contains("S-1-16-16384"));
    }

    /** The Windows extraction scheme: a shared copy per content and owner under %LOCALAPPDATA%\ducktape-laya
     *  or -Dducktape.laya.tmpdir, owner-checked, or a fresh owner-only directory. Runs on every platform;
     *  the owner and ACL cases need Windows (icacls) and are skipped elsewhere. */
    static void windowsExtraction() throws Exception {
        String libDir = System.getenv("DUCKTAPE_LAYA_LIB_DIR");
        Path built = libDir == null ? null : Path.of(libDir, Native.LIB);
        boolean windows = System.getProperty("os.name", "").toLowerCase(java.util.Locale.ROOT).contains("windows");
        byte[] bytes = built != null && Files.isRegularFile(built) ? Files.readAllBytes(built) : new byte[] {1, 2, 3, 4};
        Path root = Files.createTempDirectory("laya-extract");
        try {
            Path probe = Files.createFile(root.resolve("owner.probe"));
            UserPrincipal owner = Files.getOwner(probe);
            Files.delete(probe);
            String name = Native.sharedName(bytes, owner);
            // Extraction and load: the copy is the bundled bytes and loads as the library.
            if (built != null && Files.isRegularFile(built)) {
                Path copy = Native.extractShared(root.resolve("load"), bytes);
                eq(root.resolve("load").resolve(name).resolve(Native.LIB), copy, "copy location");
                try (Arena arena = Arena.ofConfined()) {
                    var abi = Linker.nativeLinker().downcallHandle(SymbolLookup.libraryLookup(copy, arena).find("dtl_abi_version").orElseThrow(),
                        FunctionDescriptor.of(ValueLayout.JAVA_INT));
                    int version;
                    try {
                        version = (int) abi.invokeExact();
                    } catch (Throwable t) {
                        throw new AssertionError(t);
                    }
                    eq(2, version, "ABI of the extracted copy");
                }
            }
            // An existing copy is reused, not rewritten.
            Path base = root.resolve("reuse");
            Path first = Native.extractShared(base, bytes);
            check(!Native.writableByOthers(first.getParent(), owner), "new shared directory owner-only: " + acl(first.getParent()));
            Files.setLastModifiedTime(first, java.nio.file.attribute.FileTime.fromMillis(86_400_000L));
            eq(first, Native.extractShared(base, bytes), "shared copy reused");
            eq(86_400_000L, Files.getLastModifiedTime(first).toMillis(), "reused copy not rewritten");
            // A directory the user created beforehand is used. On Windows an ACL entry for another account
            // (SYSTEM, as %LOCALAPPDATA% passes on) is replaced by an owner-only ACL.
            Path existing = Files.createDirectories(root.resolve("existing").resolve(name));
            if (windows) {
                icacls(existing, "/grant", "*S-1-5-18:(OI)(CI)F");
                check(Native.writableByOthers(existing, owner), "precondition: SYSTEM can write " + acl(existing));
            }
            eq(existing.resolve(Native.LIB), Native.extractShared(root.resolve("existing"), bytes), "existing directory used");
            check(!Native.writableByOthers(existing, owner), "existing directory made owner-only: " + acl(existing));
            check(!Native.writableByOthers(existing.resolve(Native.LIB), owner), "copy owner-only: " + acl(existing.resolve(Native.LIB)));
            // -Dducktape.laya.tmpdir takes precedence over %LOCALAPPDATA%.
            Path local = root.resolve("local"), override = root.resolve("override");
            eq(override.resolve(name).resolve(Native.LIB), Native.extractWindows(bytes, override.toString(), local.toString(), root),
                "tmpdir override");
            check(!Files.exists(local), "LOCALAPPDATA untouched with an override");
            eq(local.resolve("ducktape-laya").resolve(name).resolve(Native.LIB), Native.extractWindows(bytes, null, local.toString(), root),
                "LOCALAPPDATA location");
            // No location: a fresh owner-only directory under java.io.tmpdir, every time.
            Path javaTmp = Files.createDirectories(root.resolve("java-tmp"));
            Path fresh = Native.extractWindows(bytes, null, null, javaTmp);
            check(fresh.getParent().getParent().equals(javaTmp) && fresh.getParent().getFileName().toString().startsWith("ducktape-laya"),
                "fresh directory " + fresh);
            check(!Native.writableByOthers(fresh.getParent(), owner), "fresh directory owner-only: " + acl(fresh.getParent()));
            check(!fresh.equals(Native.extractWindows(bytes, "", "", javaTmp)), "fresh directory is not reused");
            if (!windows) {
                System.out.println("SKIP windowsExtraction owner and ACL cases (Windows only: icacls)");
                return;
            }
            // An inheritable entry for another account (Everyone) on the base directory reaches neither a new
            // shared directory nor its copy; added after extraction, it propagates to an ordinary sibling
            // directory but not into the shared one, whose ACL is protected from inheritance.
            Path inheriting = Files.createDirectories(root.resolve("inherit"));
            icacls(inheriting, "/grant", "*S-1-1-0:(OI)(CI)F");
            Path inherited = Native.extractShared(inheriting, bytes);
            check(!Native.writableByOthers(inherited.getParent(), owner), "inheritable base entry kept out of the directory: " + acl(inherited.getParent()));
            check(!Native.writableByOthers(inherited, owner), "inheritable base entry kept out of the copy: " + acl(inherited));
            Path sibling = Files.createDirectories(inheriting.resolve("sibling"));
            icacls(inheriting, "/remove:g", "*S-1-1-0");
            java.util.Set<String> before = modifiers(sibling, owner);
            icacls(inheriting, "/grant", "*S-1-1-0:(OI)(CI)M");
            java.util.Set<String> after = modifiers(sibling, owner);
            check(after.containsAll(before) && after.size() > before.size(),
                "precondition: the new entry propagates to an unprotected sibling: " + before + " then " + after);
            check(!Native.writableByOthers(inherited.getParent(), owner), "protected directory after propagation: " + acl(inherited.getParent()));
            check(!Native.writableByOthers(inherited, owner), "copy in the protected directory after propagation: " + acl(inherited));
            // Another account may modify the directory and the copy (Everyone: full control): both are
            // made owner-only again, and the copy, checked after that, is used.
            Path shared = Native.extractShared(root.resolve("acl"), bytes);
            icacls(shared.getParent(), "/grant", "*S-1-1-0:(OI)(CI)F");
            icacls(shared, "/grant", "*S-1-1-0:F");
            check(Native.writableByOthers(shared.getParent(), owner) && Native.writableByOthers(shared, owner),
                "precondition: Everyone can write " + acl(shared.getParent()) + " " + acl(shared));
            eq(shared, Native.extractShared(root.resolve("acl"), bytes), "copy used after securing");
            check(!Native.writableByOthers(shared.getParent(), owner), "directory secured: " + acl(shared.getParent()));
            check(!Native.writableByOthers(shared, owner), "copy secured: " + acl(shared));
            if (!elevated()) {
                // Giving a file to another account (icacls /setowner) needs an elevated administrator; GitHub's
                // Windows runners are elevated, so CI runs these cases.
                System.out.println("SKIP windowsExtraction foreign-owner cases (not elevated: icacls /setowner to SYSTEM needs the restore privilege)");
                return;
            }
            // A copy owned by another account (SYSTEM) in the user's own directory is replaced.
            icacls(shared, "/setowner", "*S-1-5-18");
            check(!Files.getOwner(shared).equals(owner), "precondition: copy owned by " + Files.getOwner(shared));
            eq(shared, Native.extractShared(root.resolve("acl"), bytes), "foreign copy replaced");
            eq(owner, Files.getOwner(shared), "replaced copy owner");
            // A directory owned by another account (SYSTEM) is refused: an explicit tmpdir fails, and
            // %LOCALAPPDATA% falls back to a fresh owner-only directory.
            Path foreignDir = Files.createDirectories(root.resolve("foreign-local").resolve("ducktape-laya").resolve(name));
            icacls(foreignDir, "/setowner", "*S-1-5-18");
            check(!Files.getOwner(foreignDir).equals(owner), "precondition: directory owned by " + Files.getOwner(foreignDir));
            Throwable refused = thrown(() -> Native.extractShared(foreignDir.getParent(), bytes));
            check(refused instanceof java.io.IOException && refused.getMessage().contains("not a directory owned by the current user"), "foreign directory refused: " + refused);
            refused = thrown(() -> Native.extractWindows(bytes, foreignDir.getParent().toString(), local.toString(), javaTmp));
            check(refused instanceof java.io.IOException && refused.getMessage().contains("not a directory owned by the current user"), "explicit tmpdir refused: " + refused);
            Path fallback = Native.extractWindows(bytes, null, root.resolve("foreign-local").toString(), javaTmp);
            check(fallback.getParent().getParent().equals(javaTmp), "fallback to a fresh directory: " + fallback);
            check(!Native.writableByOthers(fallback.getParent(), owner), "fallback owner-only: " + acl(fallback.getParent()));
            check(!Files.exists(foreignDir.resolve(Native.LIB)), "nothing written to the foreign directory");
        } finally {
            deleteRecursively(root);
        }
    }

    private static void icacls(Path path, String... args) throws Exception {
        List<String> command = new ArrayList<>(List.of("icacls", path.toString()));
        command.addAll(List.of(args));
        Process p = new ProcessBuilder(command).redirectErrorStream(true).start();
        String out = new String(p.getInputStream().readAllBytes());
        eq(0, p.waitFor(), String.join(" ", command) + ": " + out);
    }

    /** The accounts other than owner that an ACL entry of path lets modify it. */
    private static java.util.Set<String> modifiers(Path path, UserPrincipal owner) throws java.io.IOException {
        var view = Files.getFileAttributeView(path, java.nio.file.attribute.AclFileAttributeView.class, java.nio.file.LinkOption.NOFOLLOW_LINKS);
        java.util.Set<String> names = new java.util.TreeSet<>();
        for (var entry : view.getAcl())
            if (entry.type() == java.nio.file.attribute.AclEntryType.ALLOW && !entry.principal().equals(owner)
                && entry.permissions().contains(java.nio.file.attribute.AclEntryPermission.WRITE_DATA))
                names.add(entry.principal().getName());
        return names;
    }

    /** The ACL entries of path, for failure messages (empty where the file system has no ACLs). */
    private static String acl(Path path) throws java.io.IOException {
        var view = Files.getFileAttributeView(path, java.nio.file.attribute.AclFileAttributeView.class, java.nio.file.LinkOption.NOFOLLOW_LINKS);
        return view == null ? "" : view.getAcl().toString();
    }

    private static void deleteRecursively(Path root) throws java.io.IOException {
        try (var walk = Files.walk(root)) {
            for (Path q : walk.sorted(java.util.Comparator.reverseOrder()).toList()) Files.deleteIfExists(q);
        }
    }

    /** In the child JVM: the bundled library loads, and on Linux and macOS its extraction directory is
     *  already gone while the library stays mapped (on Linux /proc/self/maps shows it as deleted). */
    private static void bundledChild(String platform) throws Exception {
        check(Engine.runtimeInfo().contains("\"abi_version\":2"), "bundled runtime info");
        if (platform.startsWith("windows")) return;
        Path tmp = Path.of(System.getProperty("ducktape.laya.tmpdir"));
        try (var entries = Files.list(tmp)) {
            eq(List.<Path>of(), entries.toList(), "extraction directory left behind while loaded");
        }
        if (platform.startsWith("linux")) {
            String prefix = tmp.toRealPath().resolve("ducktape-laya").toString();
            List<String> mapped = Files.readAllLines(Path.of("/proc/self/maps")).stream()
                .filter(line -> line.contains(prefix) && line.contains(Native.LIB)).toList();
            check(!mapped.isEmpty() && mapped.stream().allMatch(line -> line.endsWith("(deleted)")),
                "library mapped from the deleted extraction directory: " + mapped);
        }
    }

    private static void loadFailureChild() {
        for (int i = 0; i < 2; i++) {
            LayaException e = null;
            try { Engine.runtimeInfo(); } catch (LayaException x) { e = x; }
            check(e instanceof LayaLoadException && e.status() == Status.UNSUPPORTED_PLATFORM && e.getMessage().contains("nonexistent"), "runtimeInfo error " + e);
            try { Engine.open("m.gguf", ONE); e = null; } catch (LayaException x) { e = x; }
            check(e instanceof LayaLoadException, "open error " + e);
            // Conversions are checked before the library loads.
            check(thrown(() -> Engine.open("a\0b")) instanceof LayaArgumentException x && x.status() == Status.INVALID_ARGUMENT, "NUL path without a library");
        }
    }

    private static void retryChild(String target, String source) throws Exception {
        check(thrown(Engine::runtimeInfo) instanceof LayaLoadException, "first call fails");
        Files.copy(Path.of(source), Path.of(target), StandardCopyOption.REPLACE_EXISTING);
        check(Engine.runtimeInfo().contains("\"abi_version\":2"), "second call loads");
    }

    static void sharedAcrossThreads() throws Exception {
        Engine laya = Engine.open(MODEL, TWO);
        String baseline = laya.execute(REQUEST);
        // Two names for one file with one slot: concurrent calls evict each other.
        Engine router = Engine.openRouter("{\"models\":{\"a\":\"" + json(MODEL) + "\",\"b\":\"" + json(MODEL) + "\"},\"max_loaded\":1}", TWO);
        CancellationToken token = new CancellationToken();
        ExecutorService pool = Executors.newFixedThreadPool(6);
        try {
            List<Future<String>> plain = new ArrayList<>();
            for (int i = 0; i < 3; i++) plain.add(pool.submit(() -> laya.execute(REQUEST)));
            List<Future<String>> routed = new ArrayList<>();
            for (String name : new String[] {"a", "b"}) routed.add(pool.submit(() -> router.execute(withModel(REQUEST, name))));
            Future<String> cancelled = pool.submit(() -> laya.execute(REQUEST, CallOptions.none().withCancellation(token)));
            token.cancel();
            for (Future<String> f : plain) eq(baseline, f.get(), "concurrent execute");
            String[] names = {"a", "b"};
            for (int i = 0; i < 2; i++)
                eq(baseline.substring(0, baseline.length() - 1) + ",\"model\":\"" + names[i] + "\"}", routed.get(i).get(), "routed " + names[i]);
            try {
                eq(baseline, cancelled.get(), "cancel raced to completion");
            } catch (ExecutionException e) {
                eq(5, ((LayaException) e.getCause()).status().code(), "cancelled status");
            }
            // Close from another thread than the opener, then idempotently.
            pool.submit(() -> { laya.close(); router.close(); }).get();
            laya.close();
        } finally {
            pool.shutdownNow();
        }
    }

    @SuppressWarnings("try") // closes the resource inside its own callback on purpose
    static void closeInsideCallback() throws Exception {
        try (Engine laya = Engine.open(MODEL, TWO)) {
            String baseline = laya.execute(PRIMITIVE);
            // The running call finishes; the handle is released when it returns.
            eq(baseline, laya.execute(PRIMITIVE, CallOptions.none().withOnEvent(e -> laya.close())), "call completes");
            eq(1, statusOf(() -> laya.execute(PRIMITIVE)), "closed after the call returned");
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length > 0 && args[0].equals("--bundled-child")) {
            bundledChild(args[1]);
            System.exit(0);
        }
        if (args.length > 0 && args[0].equals("--bundled-broken-child")) {
            LayaException e = (LayaException) thrown(Engine::runtimeInfo);
            check(e != null && e.status() == Status.UNSUPPORTED_PLATFORM && e.getMessage().contains("Found the bundled library /native/" + args[1])
                && e.getMessage().contains("could not be loaded") && e.getMessage().contains(Native.loadHint(args[1])), "unloadable bundled library: " + e);
            System.exit(0);
        }
        if (args.length > 0 && args[0].equals("--bundled-missing-tmpdir-child")) {
            LayaException e = (LayaException) thrown(Engine::runtimeInfo);
            check(e != null && e.status() == Status.UNSUPPORTED_PLATFORM && e.getMessage().contains("Cannot extract bundled library"), "missing tmpdir: " + e);
            System.exit(0);
        }
        if (args.length > 0 && args[0].equals("--retry-child")) {
            retryChild(args[1], args[2]);
            System.exit(0);
        }
        if (args.length > 0 && args[0].equals("--load-failure")) {
            loadFailureChild();
            System.exit(0);
        }
        String[] names = {"diagnostics", "rejectsBadPath", "rejectsNul", "rejectsInvalidUnicode", "containsObserverFailures", "closeIsIdempotent", "argumentValidation", "packagePlatforms", "muslDetection", "configJson", "loadFailure", "bundledExtraction", "windowsExtraction", "pendingCloseFailsFast", "routerAutoValidation", "loadRetries", "overridesAndVersion", "routerConfigJson", "errorOrder"};
        Case[] cases = {EngineTest::diagnostics, EngineTest::rejectsBadPath, EngineTest::rejectsNul, EngineTest::rejectsInvalidUnicode,
            EngineTest::containsObserverFailures, EngineTest::closeIsIdempotent, EngineTest::argumentValidation, EngineTest::packagePlatforms, EngineTest::muslDetection, EngineTest::configJson, EngineTest::loadFailure, EngineTest::bundledExtraction, EngineTest::windowsExtraction,
            EngineTest::pendingCloseFailsFast, EngineTest::routerAutoValidation,
            EngineTest::loadRetries, EngineTest::overridesAndVersion, EngineTest::routerConfigJson, EngineTest::errorOrder};
        List<String> failed = new ArrayList<>();
        int ran = 0;
        for (int i = 0; i < cases.length; i++) ran += run(names[i], cases[i], failed);
        if (MODEL == null) System.out.println("SKIP real-model tests (LAYA_MODEL unset)");
        else {
            ran += run("realFeatures", EngineTest::realFeatures, failed);
            ran += run("configAndPerRequestSettings", EngineTest::configAndPerRequestSettings, failed);
            ran += run("cancellationAndDeadline", EngineTest::cancellationAndDeadline, failed);
            ran += run("sharedAcrossThreads", EngineTest::sharedAcrossThreads, failed);
            ran += run("nullOptions", EngineTest::nullOptions, failed);
            ran += run("closeInsideCallback", EngineTest::closeInsideCallback, failed);
        }
        System.out.println(ran + " run, " + failed.size() + " failed");
        System.exit(failed.isEmpty() ? 0 : 1);
    }

    private static int run(String name, Case c, List<String> failed) {
        try {
            c.run();
            System.out.println("PASS " + name);
        } catch (Throwable t) {
            failed.add(name);
            System.out.println("FAIL " + name + ": " + t);
            t.printStackTrace(System.out);
        }
        return 1;
    }
}
