package io.github.adrian729.laya;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.*;

/** JUnit-free test runner: exits non-zero on failure. Real-model tests need LAYA_MODEL. */
public final class LayaTest {
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
            return e.status();
        }
        throw new AssertionError("expected LayaException");
    }

    static String json(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    static String withModel(String request, String id) {
        return request.replaceFirst("\\{", "{\"model\":\"" + id + "\",");
    }

    static void diagnostics() {
        check(Laya.runtimeInfo().contains("\"abi_version\":2"), "runtime info");
        if (MODEL != null) try (Laya laya = Laya.open(MODEL, TWO)) {
            String info = laya.info();
            check(info.contains("\"decision_type\":\"laya\""), "model info");
            check(info.contains("\"context_tokens\":") && info.contains("\"trained_context_tokens\":"), "context fields " + info);
            check(!info.contains("\"max_tokens\""), "no max_tokens in info");
            check(Laya.runtimeInfo().contains("\"default_context_tokens\""), "runtime default_context_tokens");
        }
    }

    static void packagePlatforms() {
        eq("linux-x64", Native.platform("linux", "amd64"), "Linux x86-64");
        eq("linux-arm64", Native.platform("linux", "aarch64"), "Linux arm64");
        eq("macos-arm64", Native.platform("mac os x", "aarch64"), "macOS arm64");
        eq("windows-x64", Native.platform("windows server 2025", "amd64"), "Windows x64");
        eq(null, Native.platform("mac os x", "x86_64"), "Intel macOS has no package");
        eq(null, Native.platform("windows 11", "aarch64"), "Windows arm64 has no package");
        eq(null, Native.platform("freebsd", "amd64"), "FreeBSD has no package");
    }

    static void configJson() {
        check(Config.none().toJson() == null, "empty config");
        eq("{\"threads\":0}", Config.none().withThreads(0).toJson(), "threads only");
        eq("{\"threads\":2,\"context_tokens\":64,\"temperatures\":{\"choice\":1.5,\"noul.2\":2.0}}",
            Config.none().withTemperatures(Map.of("noul.2", 2.0, "choice", 1.5)).withContextTokens(64).withThreads(2).toJson(), "full config");
        eq("{\"threads\":2,\"context_tokens\":64,\"overflow\":\"scan\",\"temperatures\":{\"choice\":1.5}}",
            Config.none().withTemperatures(Map.of("choice", 1.5)).withOverflow("scan").withContextTokens(64).withThreads(2).toJson(), "overflow position");
        eq("{\"temperatures\":{\"a\\\"b\":1.0}}", Config.none().withTemperatures(Map.of("a\"b", 1.0)).toJson(), "escaped key");
    }

    static void rejectsBadPath() throws Exception {
        check(statusOf(() -> Laya.open("missing.gguf", TWO)) != 0, "bad path");
    }

    static void rejectsNul() throws Exception {
        eq(1, statusOf(() -> Laya.open("a\0b", TWO)), "open NUL");
        eq(1, statusOf(() -> Laya.openRouter("{\0}", ONE)), "router NUL");
    }

    static void rejectsInvalidUnicode() throws Exception {
        String lone = "a\ud800b";
        eq(1, statusOf(() -> Laya.open(lone, TWO)), "open lone surrogate");
        eq(1, statusOf(() -> Laya.openRouter("{\"models\":{\"x\":\"" + lone + "\"}}", ONE)), "router lone surrogate");
        eq(1, statusOf(() -> Laya.open("x", Config.none().withTemperatures(Map.of(lone, 1.0)))), "config lone surrogate");
        Path file = Files.createTempFile("laya", ".gguf");
        try (Laya router = Laya.openRouter("{\"models\":{\"x\":\"" + json(file.toString()) + "\"}}")) {
            check(thrown(() -> router.execute("{\"state\":\"" + lone + "\"}")) instanceof LayaException e && e.status() == 1
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
        try (Laya router = Laya.openRouter("{\"models\":{\"unused\":\"" + json(file.toString()) + "\"}}", ONE)) {
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
                try { router.info(); } catch (LayaException x) { nested[0] = x.status(); }
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
            Laya router = Laya.openRouter("{\"models\":{\"unused\":\"" + json(file.toString()) + "\"}}", ONE);
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
                                catch (LayaClosedException x) { return true; }
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
                check(thrown(() -> router.execute("{}")) instanceof LayaClosedException, "closed after the call returned");
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
                Throwable t = thrown(() -> Laya.openRouter("{" + models + ",\"auto\":" + c[0] + "}", ONE));
                check(t instanceof LayaException e && e.status() == 1 && e.getMessage().contains(c[1]), c[0] + " -> " + t);
            }
            try (Laya router = Laya.openRouter("{" + models + "}", ONE)) {
                Throwable noAuto = thrown(() -> router.execute(withModel(PRIMITIVE, "auto")));
                check(noAuto instanceof LayaException e && e.status() == 1 && e.getMessage().contains("requires an \"auto\" router config"), "auto without config: " + noAuto);
                Throwable lang = thrown(() -> router.execute(withModel(PRIMITIVE, "a").replaceFirst("\\{", "{\"lang\":\"en\",")));
                check(lang instanceof LayaException e && e.status() == 1 && e.getMessage().contains("lang"), "lang without auto: " + lang);
            }
        } finally {
            Files.deleteIfExists(file);
        }
    }

    static void routerAutoRoutes() throws Exception {
        String json = "{\"models\":{\"en\":\"" + json(MODEL) + "\",\"ml\":\"" + json(MODEL)
            + "\"},\"max_loaded\":1,\"auto\":{\"english\":\"en\",\"default\":\"ml\"}}";
        String english = "I was charged twice for my subscription this month and I would like my money back please.";
        try (Laya router = Laya.openRouter(json, TWO)) {
            String out = router.execute(withModel(PRIMITIVE, "auto").replace("Refund me", english));
            check(out.contains("\"model\":\"en\"") && out.contains("\"reason\":\"english_text\""), "english text " + out);
            out = router.execute(withModel(PRIMITIVE, "auto").replace("Refund me", "\u6211\u8fd9\u4e2a\u6708\u7684\u8ba2\u9605\u88ab\u6263\u4e86\u4e24\u6b21\u8d39\u7528\uff0c\u8bf7\u9000\u6b3e"));
            check(out.contains("\"model\":\"ml\"") && out.contains("\"reason\":\"non_latin_script\""), "non-Latin text " + out);
            out = router.execute(withModel(PRIMITIVE, "auto").replace("Refund me", english).replaceFirst("\\{", "{\"lang\":\"fr\","));
            check(out.contains("\"model\":\"ml\"") && out.contains("\"reason\":\"lang\""), "lang override " + out);
        }
    }

    static void closeIsIdempotent() throws Exception {
        Path file = Files.createTempFile("laya", ".gguf");
        try {
            Laya router = Laya.openRouter("{\"models\":{\"x\":\"" + json(file.toString()) + "\"}}", ONE);
            router.close();
            router.close();
            eq(1, statusOf(() -> router.execute("{}")), "closed execute");
            check(thrown(() -> router.execute("{}")) instanceof LayaClosedException, "closed exception type");
            eq(1, statusOf(router::info), "closed info");
        } finally {
            Files.deleteIfExists(file);
        }
    }

    static void realFeatures() throws Exception {
        try (Laya laya = Laya.open(MODEL, TWO)) {
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

            try (Laya router = Laya.openRouter("{\"models\":{\"english\":\"" + json(MODEL) + "\"},\"max_loaded\":1}", TWO)) {
                check(router.execute(withModel(SCHEMA, "english")).contains("\"model\":\"english\""), "routed");
                eq(1, statusOf(() -> router.execute(SCHEMA)), "router needs model");
            }
        }
    }

    static void configAndPerRequestSettings() throws Exception {
        String choice = "{\"state\":\"Please refund me.\",\"questions\":{\"q\":{\"type\":\"choice\",\"instructions\":\"Is a refund requested?\",\"criteria\":[\"yes\",\"no\"]}}}";
        String baseline;
        try (Laya laya = Laya.open(MODEL, TWO)) {
            baseline = laya.execute(choice);
            check(baseline.contains("\"answer_confidence\""), "answer_confidence " + baseline);
            check(baseline.contains("\"confidence\""), "confidence " + baseline);
            String hot = choice.replaceFirst("\\{", "{\"temperatures\":{\"choice.2\":3.0},");
            String hotResult = laya.execute(hot);
            check(!hotResult.equals(baseline), "per-request temperature changes the result: " + baseline + " vs " + hotResult);
            eq(baseline, laya.execute(choice), "per-request settings do not persist");
            eq(1, statusOf(() -> laya.execute(choice.replaceFirst("\\{", "{\"max_tokens\":0,"))), "max_tokens 0");
            eq(1, statusOf(() -> laya.execute(choice.replaceFirst("\\{", "{\"max_tokens\":100000000,"))), "max_tokens above context");
            laya.execute(choice.replaceFirst("\\{", "{\"max_tokens\":512,"));
        }
        Config hot = TWO.withContextTokens(128).withTemperatures(Map.of("choice.2", 3.0));
        try (Laya laya = Laya.open(MODEL, hot)) {
            check(laya.info().contains("\"context_tokens\":128"), "configured context tokens");
            check(!laya.execute(choice).equals(baseline), "open temperatures change the result");
        }
        eq(1, statusOf(() -> Laya.open(MODEL, TWO.withContextTokens(1))), "context_tokens below minimum");
        eq(1, statusOf(() -> Laya.open(MODEL, TWO.withOverflow("bogus"))), "invalid overflow");
        String longRequest = "{\"state\":\"" + "refund ".repeat(800) + "\",\"questions\":{\"refund\":{\"type\":\"noul\",\"instructions\":\"Is a refund requested?\"}}}";
        try (Laya laya = Laya.open(MODEL, TWO.withOverflow("truncate_end"))) {
            String out = laya.execute(longRequest);
            check(out.contains("\"state_tokens_dropped\":") && !out.contains("\"state_tokens_dropped\":0"), "truncate_end drops tokens " + out);
            eq(1, statusOf(() -> laya.execute(longRequest.replaceFirst("\\{", "{\"overflow\":\"reject\","))), "request overflow reject wins");
        }
        eq(1, statusOf(() -> Laya.open(MODEL, TWO.withTemperatures(Map.of("bogus.key", 1.5)))), "unknown temperature key");
        // Router: the open config applies to every route; a route's own config overrides it.
        String hotRoute = "{\"models\":{\"plain\":\"" + json(MODEL) + "\",\"hot\":{\"path\":\"" + json(MODEL)
            + "\",\"config\":{\"temperatures\":{\"choice.2\":3.0}}}},\"max_loaded\":1}";
        try (Laya router = Laya.openRouter(hotRoute, TWO)) {
            String plain = router.execute(withModel(choice, "plain"));
            check(plain.contains("\"model\":\"plain\""), "plain route");
            String modelHot = router.execute(withModel(choice, "hot"));
            check(!modelHot.replace("\"hot\"", "\"plain\"").equals(plain), "route config overrides: " + plain + " vs " + modelHot);
            check(router.info().contains("\"default_context_tokens\""), "router info is runtime only");
        }
    }

    static void cancellationAndDeadline() throws Exception {
        try (Laya laya = Laya.open(MODEL, TWO)) {
            String baseline = laya.execute(REQUEST);
            CancellationToken token = new CancellationToken();
            token.cancel();
            CallOptions cancelled = CallOptions.none().withCancellation(token);
            eq(5, statusOf(() -> laya.execute(REQUEST, cancelled)), "pre-cancelled execute");
            eq(6, statusOf(() -> laya.execute(REQUEST, CallOptions.none().withTimeout(Duration.ofMillis(1)))), "deadline");
            eq(baseline, laya.execute(REQUEST), "unchanged after stop");
            token.reset();
            eq(baseline, laya.execute(REQUEST, cancelled), "uncancelled token");
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
            live.reset();
            eq(baseline, laya.execute(REQUEST, CallOptions.none().withCancellation(live)), "reset token runs");
        }
    }

    static void nullOptions() throws Exception {
        try (Laya laya = Laya.open(MODEL)) {
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
        check(thrown(() -> Config.none().withThreads(-1)) instanceof IllegalArgumentException, "threads -1");
        check(thrown(() -> Config.none().withThreads(257)) instanceof IllegalArgumentException, "threads 257");
        check(thrown(() -> Config.none().withContextTokens(0)) instanceof IllegalArgumentException, "context 0");
        check(thrown(() -> Config.none().withTemperatures(Map.of("choice", 0.0))) instanceof IllegalArgumentException, "temp 0");
        check(thrown(() -> Config.none().withTemperatures(Map.of("choice", Double.NaN))) instanceof IllegalArgumentException, "temp NaN");
        check(thrown(() -> Config.none().withTemperatures(Map.of("", 1.0))) instanceof IllegalArgumentException, "temp empty key");
        check(thrown(() -> Laya.open("x", null)) instanceof NullPointerException, "null config");
        check(thrown(() -> Laya.open(null)) instanceof NullPointerException, "null path");
        check(thrown(() -> Laya.openRouter(null)) instanceof NullPointerException, "null config");
        Path file = Files.createTempFile("laya", ".gguf");
        try (Laya router = Laya.openRouter("{\"models\":{\"x\":\"" + json(file.toString()) + "\"}}")) {
            check(thrown(() -> router.execute(null)) instanceof NullPointerException, "null request");
            check(thrown(() -> router.execute("{}", null)) instanceof LayaException e && e.status() == 1, "null options accepted");
        } finally {
            Files.deleteIfExists(file);
        }
    }

    /** Runs this class in a fresh JVM with a broken library selection; the child verifies the errors. */
    static void loadFailure() throws Exception {
        String java = ProcessHandle.current().info().command().orElse("java");
        String cp = System.getProperty("java.class.path");
        for (boolean viaProperty : new boolean[] {true, false}) {
            List<String> cmd = new ArrayList<>(List.of(java, "--enable-native-access=ALL-UNNAMED"));
            if (viaProperty) cmd.add("-Dducktape.laya.library=/nonexistent/libducktape_laya.so");
            cmd.addAll(List.of("-cp", cp, "io.github.adrian729.laya.LayaTest", "--load-failure"));
            ProcessBuilder pb = new ProcessBuilder(cmd).redirectErrorStream(true);
            pb.environment().put("DUCKTAPE_LAYA_LIB_DIR", viaProperty ? System.getenv().getOrDefault("DUCKTAPE_LAYA_LIB_DIR", "/x") : "/nonexistent");
            Process p = pb.start();
            String out = new String(p.getInputStream().readAllBytes());
            eq(0, p.waitFor(), "child exit (viaProperty=" + viaProperty + "): " + out);
        }
    }

    private static void loadFailureChild() {
        for (int i = 0; i < 2; i++) {
            LayaException e = null;
            try { Laya.runtimeInfo(); } catch (LayaException x) { e = x; }
            check(e != null && e.status() == LayaException.UNSUPPORTED_PLATFORM && e.getMessage().contains("nonexistent"), "runtimeInfo error " + e);
            try { Laya.open("m.gguf", ONE); e = null; } catch (LayaException x) { e = x; }
            check(e != null && e.status() == LayaException.UNSUPPORTED_PLATFORM, "open error " + e);
        }
    }

    static void sharedAcrossThreads() throws Exception {
        Laya laya = Laya.open(MODEL, TWO);
        String baseline = laya.execute(REQUEST);
        // Two names for one file with one slot: concurrent calls evict each other.
        Laya router = Laya.openRouter("{\"models\":{\"a\":\"" + json(MODEL) + "\",\"b\":\"" + json(MODEL) + "\"},\"max_loaded\":1}", TWO);
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
                eq(5, ((LayaException) e.getCause()).status(), "cancelled status");
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
        try (Laya laya = Laya.open(MODEL, TWO)) {
            String baseline = laya.execute(PRIMITIVE);
            // The running call finishes; the handle is released when it returns.
            eq(baseline, laya.execute(PRIMITIVE, CallOptions.none().withOnEvent(e -> laya.close())), "call completes");
            eq(1, statusOf(() -> laya.execute(PRIMITIVE)), "closed after the call returned");
        }
    }

    public static void main(String[] args) {
        if (args.length > 0 && args[0].equals("--load-failure")) {
            loadFailureChild();
            System.exit(0);
        }
        String[] names = {"diagnostics", "rejectsBadPath", "rejectsNul", "rejectsInvalidUnicode", "containsObserverFailures", "closeIsIdempotent", "argumentValidation", "packagePlatforms", "configJson", "loadFailure", "pendingCloseFailsFast", "routerAutoValidation"};
        Case[] cases = {LayaTest::diagnostics, LayaTest::rejectsBadPath, LayaTest::rejectsNul, LayaTest::rejectsInvalidUnicode,
            LayaTest::containsObserverFailures, LayaTest::closeIsIdempotent, LayaTest::argumentValidation, LayaTest::packagePlatforms, LayaTest::configJson, LayaTest::loadFailure,
            LayaTest::pendingCloseFailsFast, LayaTest::routerAutoValidation};
        List<String> failed = new ArrayList<>();
        int ran = 0;
        for (int i = 0; i < cases.length; i++) ran += run(names[i], cases[i], failed);
        if (MODEL == null) System.out.println("SKIP real-model tests (LAYA_MODEL unset)");
        else {
            ran += run("routerAutoRoutes", LayaTest::routerAutoRoutes, failed);
            ran += run("realFeatures", LayaTest::realFeatures, failed);
            ran += run("configAndPerRequestSettings", LayaTest::configAndPerRequestSettings, failed);
            ran += run("cancellationAndDeadline", LayaTest::cancellationAndDeadline, failed);
            ran += run("sharedAcrossThreads", LayaTest::sharedAcrossThreads, failed);
            ran += run("nullOptions", LayaTest::nullOptions, failed);
            ran += run("closeInsideCallback", LayaTest::closeInsideCallback, failed);
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
