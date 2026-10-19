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
            int[] refused = {0};
            CallOptions closing = CallOptions.none().withOnEvent(e -> {
                try { router.close(); } catch (IllegalStateException expected) { refused[0]++; }
            });
            eq(1, statusOf(() -> router.execute("{}", closing)), "close-in-callback request status");
            eq(2, refused[0], "close refused inside callbacks");
            eq(1, statusOf(() -> router.execute("{}")), "handle still open");
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
            eq(1, statusOf(() -> CallOptions.none().withTimeout(Duration.ofDays(2))), "timeout range");
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

    public static void main(String[] args) {
        String[] names = {"diagnostics", "rejectsBadPath", "rejectsNul", "containsObserverFailures", "closeIsIdempotent"};
        Case[] cases = {LayaTest::diagnostics, LayaTest::rejectsBadPath, LayaTest::rejectsNul,
            LayaTest::containsObserverFailures, LayaTest::closeIsIdempotent};
        List<String> failed = new ArrayList<>();
        int ran = 0;
        for (int i = 0; i < cases.length; i++) ran += run(names[i], cases[i], failed);
        if (MODEL == null) System.out.println("SKIP real-model tests (LAYA_MODEL unset)");
        else {
            ran += run("realFeatures", LayaTest::realFeatures, failed);
            ran += run("cancellationAndDeadline", LayaTest::cancellationAndDeadline, failed);
            ran += run("sharedAcrossThreads", LayaTest::sharedAcrossThreads, failed);
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
