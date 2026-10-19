package io.github.adrian729.laya;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
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

    interface Case {
        void run() throws Exception;
    }

    static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    static void eq(Object expected, Object actual, String what) {
        if (!expected.equals(actual)) throw new AssertionError(what + ": expected <" + expected + "> but was <" + actual + ">");
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
        check(Laya.runtimeInfo().contains("\"abi_version\":1"), "runtime info");
        if (MODEL != null) try (Laya laya = Laya.open(MODEL, 2)) {
            check(laya.info().contains("\"decision_type\":\"laya\""), "model info");
        }
    }

    static void rejectsBadPath() throws Exception {
        check(statusOf(() -> Laya.open("missing.gguf", 2)) != 0, "bad path");
    }

    static void rejectsNul() throws Exception {
        eq(1, statusOf(() -> Laya.open("a\0b", 2)), "open NUL");
        eq(1, statusOf(() -> Laya.openRouter("{\0}", 1)), "router NUL");
    }

    @SuppressWarnings("try") // closes the resource inside its own callback on purpose
    static void containsObserverFailures() throws Exception {
        Path file = Files.createTempFile("laya", ".gguf");
        try (Laya router = Laya.openRouter("{\"models\":{\"unused\":\"" + json(file.toString()) + "\"}}", 1)) {
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

    static void closeIsIdempotent() throws Exception {
        Path file = Files.createTempFile("laya", ".gguf");
        try {
            Laya router = Laya.openRouter("{\"models\":{\"x\":\"" + json(file.toString()) + "\"}}", 1);
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
        try (Laya laya = Laya.open(MODEL, 2)) {
            String tagged = PRIMITIVE.replaceFirst("\\{", "{\"metadata\":\"caller tag\",");
            eq(laya.decide(PRIMITIVE), laya.decide(tagged), "legacy metadata ignored");
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

            try (Laya router = Laya.openRouter("{\"models\":{\"english\":\"" + json(MODEL) + "\"},\"max_loaded\":1}", 2)) {
                check(router.execute(withModel(SCHEMA, "english")).contains("\"model\":\"english\""), "routed");
                eq(1, statusOf(() -> router.execute(SCHEMA)), "router needs model");
            }
        }
    }

    static void cancellationAndDeadline() throws Exception {
        try (Laya laya = Laya.open(MODEL, 2)) {
            String baseline = laya.execute(REQUEST);
            CancellationToken token = new CancellationToken();
            token.cancel();
            CallOptions cancelled = CallOptions.none().withCancellation(token);
            eq(5, statusOf(() -> laya.execute(REQUEST, cancelled)), "pre-cancelled execute");
            eq(5, statusOf(() -> laya.decide(REQUEST, cancelled)), "pre-cancelled decide");
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
            eq(laya.decide(PRIMITIVE), laya.decide(PRIMITIVE, null), "null options decide");
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
        check(thrown(() -> Laya.open("x", -1)) instanceof IllegalArgumentException, "threads -1");
        check(thrown(() -> Laya.open("x", 257)) instanceof IllegalArgumentException, "threads 257");
        check(thrown(() -> Laya.openRouter("{}", 257)) instanceof IllegalArgumentException, "router threads 257");
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
            try { Laya.open("m.gguf", 1); e = null; } catch (LayaException x) { e = x; }
            check(e != null && e.status() == LayaException.UNSUPPORTED_PLATFORM, "open error " + e);
        }
    }

    static void sharedAcrossThreads() throws Exception {
        Laya laya = Laya.open(MODEL, 2);
        String baseline = laya.execute(REQUEST);
        // Two names for one file with one slot: concurrent calls evict each other.
        Laya router = Laya.openRouter("{\"models\":{\"a\":\"" + json(MODEL) + "\",\"b\":\"" + json(MODEL) + "\"},\"max_loaded\":1}", 2);
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
        try (Laya laya = Laya.open(MODEL, 2)) {
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
        String[] names = {"diagnostics", "rejectsBadPath", "rejectsNul", "containsObserverFailures", "closeIsIdempotent", "argumentValidation", "loadFailure"};
        Case[] cases = {LayaTest::diagnostics, LayaTest::rejectsBadPath, LayaTest::rejectsNul,
            LayaTest::containsObserverFailures, LayaTest::closeIsIdempotent, LayaTest::argumentValidation, LayaTest::loadFailure};
        List<String> failed = new ArrayList<>();
        int ran = 0;
        for (int i = 0; i < cases.length; i++) ran += run(names[i], cases[i], failed);
        if (MODEL == null) System.out.println("SKIP real-model tests (LAYA_MODEL unset)");
        else {
            ran += run("realFeatures", LayaTest::realFeatures, failed);
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
