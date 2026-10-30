package io.github.adrian729.laya;

import static io.github.adrian729.laya.Native.*;

import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.lang.invoke.MethodHandles;
import java.lang.invoke.MethodType;
import java.lang.invoke.VarHandle;
import java.lang.ref.Cleaner;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Duration;
import java.util.HashSet;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.locks.ReentrantReadWriteLock;
import java.util.function.Consumer;

/**
 * A loaded model or a bounded local model router. Thread-safe: native calls on one handle
 * serialize, and {@link #close()} waits for in-flight calls. Inference blocks the calling thread.
 * Closing a handle from inside its own event callback releases it once the running call finishes.
 */
public final class Engine implements AutoCloseable {
    private static final VarHandle FLAG = ValueLayout.JAVA_INT.varHandle();
    private static final MethodHandle EVENT_TARGET;
    private static final Cleaner CLEANER = Cleaner.create();
    /** Runs {@link #executeAsync} calls: blocking native calls must not occupy the common pool. */
    private static final ExecutorService ASYNC = Executors.newCachedThreadPool(task -> {
        Thread thread = new Thread(task, "laya-async");
        thread.setDaemon(true);
        return thread;
    });

    static {
        try {
            EVENT_TARGET = MethodHandles.lookup().findStatic(Engine.class, "event",
                MethodType.methodType(void.class, Consumer.class, MemorySegment.class, MemorySegment.class));
        } catch (ReflectiveOperationException e) {
            throw new ExceptionInInitializerError(e);
        }
    }

    /** Owns the native handle; deliberately holds no reference to the Engine so the Cleaner can run. */
    private static final class State implements Runnable {
        private final MemorySegment handle;
        private final MethodHandle close;

        State(MemorySegment handle, MethodHandle close) {
            this.handle = handle;
            this.close = close;
        }

        @Override
        public void run() {
            try {
                close.invokeExact(handle);
            } catch (Throwable t) {
                throw sneaky(t);
            }
        }
    }

    private final ReentrantReadWriteLock lock = new ReentrantReadWriteLock();
    private final MemorySegment handle;
    private final Cleaner.Cleanable cleanable;
    private volatile boolean closing;
    private boolean released; // guarded by the write lock
    private final Set<MemorySegment> flags = new HashSet<>(); // cancel flags of the calls in flight; guarded by itself
    private boolean cancelAll; // guarded by flags

    private Engine(MemorySegment handle, MethodHandle close) {
        this.handle = handle;
        this.cleanable = CLEANER.register(this, new State(handle, close));
    }

    /** Runtime identity/capabilities without loading a model. No telemetry or input data. */
    public static String runtimeInfo() {
        return readInfo(MemorySegment.NULL);
    }

    /** Opens a model with the library defaults. */
    public static Engine open(String modelPath) {
        return open(modelPath, Config.none());
    }

    /**
     * Opens a model. A null path throws {@link NullPointerException}; a null config means
     * {@link Config#none()}. Call {@link #close()} to release it promptly; a Cleaner releases it if
     * the object is dropped without closing.
     */
    public static Engine open(String modelPath, Config config) {
        return create(false, Objects.requireNonNull(modelPath, "modelPath"), config);
    }

    public static Engine open(Path modelPath) {
        return open(modelPath, Config.none());
    }

    public static Engine open(Path modelPath, Config config) {
        return open(Objects.requireNonNull(modelPath, "modelPath").toString(), config);
    }

    /** Opens a router with the library defaults. */
    public static Engine openRouter(String routerJson) {
        return openRouter(routerJson, Config.none());
    }

    /**
     * Opens a bounded, lazy local model registry; see docs/API.md for the router JSON. The config
     * applies to every route; a route's own "config" overrides it key by key.
     */
    public static Engine openRouter(String routerJson, Config config) {
        return create(true, Objects.requireNonNull(routerJson, "routerJson"), config);
    }

    public static Engine openRouter(RouterConfig router) {
        return openRouter(router, Config.none());
    }

    public static Engine openRouter(RouterConfig router, Config config) {
        return openRouter(Objects.requireNonNull(router, "router").toJson(), config);
    }

    /** Model metadata for a single model; a router reports the runtime only. */
    public String info() {
        return withHandle(h -> readInfo(h));
    }

    /**
     * Executes one request or a bounded JSON array of them (per-request "max_tokens" and
     * "temperatures" go in the request JSON). {@code onEvent} runs on the calling thread. Null options mean {@link CallOptions#none()}.
     * The timeout counts from this call.
     */
    public String execute(String request) {
        return run(request, CallOptions.none(), System.nanoTime(), null);
    }

    public String execute(String request, CallOptions options) {
        return run(request, options, System.nanoTime(), null);
    }

    /**
     * {@link #execute(String, CallOptions)} on a binding-owned pool of daemon threads. The timeout
     * counts from this call. Failures (a closed engine, argument errors, a cancelled token, native
     * errors) complete the future exceptionally with the exception {@code execute} would throw.
     * {@code future.cancel(...)} (either value) cancels this call only, never a token shared through
     * the options: the native call stops at its next safe point and the future completes with a
     * {@link java.util.concurrent.CancellationException}; {@link #close()} still waits for the native call.
     */
    public CompletableFuture<String> executeAsync(String request) {
        return executeAsync(request, CallOptions.none(), ASYNC);
    }

    public CompletableFuture<String> executeAsync(String request, CallOptions options) {
        return executeAsync(request, options, ASYNC);
    }

    public CompletableFuture<String> executeAsync(String request, CallOptions options, Executor executor) {
        long entry = System.nanoTime();
        Objects.requireNonNull(request, "request");
        Objects.requireNonNull(executor, "executor");
        CancellationToken scoped = new CancellationToken();
        CompletableFuture<String> future = new CompletableFuture<>() {
            @Override
            public boolean cancel(boolean mayInterruptIfRunning) {
                boolean cancelled = super.cancel(mayInterruptIfRunning);
                if (cancelled) scoped.cancel();
                return cancelled;
            }
        };
        try {
            executor.execute(() -> {
                try {
                    future.complete(run(request, options, entry, scoped));
                } catch (Throwable t) {
                    future.completeExceptionally(t);
                }
            });
        } catch (Throwable t) {
            future.completeExceptionally(t);
        }
        return future;
    }

    /** Waits for in-flight calls, then releases the native handle; see {@link #close(boolean)}. */
    @Override
    public void close() {
        close(false);
    }

    /**
     * Waits for in-flight calls, then releases the native handle. Idempotent. With {@code cancelPending},
     * every call that has entered and not yet returned is cancelled first (it fails with
     * {@link Status#CANCELLED}), so the wait is short. Called from inside one of this handle's own
     * calls (an event callback), it cannot wait for that call: it rejects new calls, cancels the
     * running ones if asked and returns, and the last running call releases the handle when it finishes.
     */
    public void close(boolean cancelPending) {
        closing = true;
        if (cancelPending) {
            synchronized (flags) {
                cancelAll = true;
                for (MemorySegment flag : flags) FLAG.setRelease(flag, 0L, Status.CANCELLED.code());
            }
        }
        // The write lock waits for every read hold, including this thread's own.
        if (lock.getReadHoldCount() > 0) return;
        lock.writeLock().lock();
        release();
    }

    /** Called with the write lock held; unlocks it. */
    private void release() {
        try {
            if (released) return;
            released = true;
            cleanable.clean();
        } finally {
            lock.writeLock().unlock();
        }
    }

    private static Engine create(boolean router, String input, Config config) {
        if (config == null) config = Config.none();
        checkInput(input, router ? "router config" : "model path");
        String configJson = config.toJson();
        if (configJson != null) checkInput(configJson, "config");
        Native n = Native.get();
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment out = arena.allocate(PTR);
            MemorySegment error = arena.allocate(PTR);
            MemorySegment cfg = configJson == null ? MemorySegment.NULL : arena.allocateFrom(configJson);
            MemorySegment in = arena.allocateFrom(input);
            int status = router ? (int) n.ROUTER_OPEN.invokeExact(in, cfg, out, error)
                                : (int) n.OPEN.invokeExact(in, cfg, out, error);
            check(status, error);
            MemorySegment raw = out.get(PTR, 0);
            if (raw.equals(MemorySegment.NULL))
                throw new LayaException(Status.MODEL_ERROR, "Native engine returned null");
            return new Engine(raw, n.CLOSE);
        } catch (Throwable t) {
            throw sneaky(t);
        }
    }

    private static void checkInput(String s, String what) {
        if (s.indexOf('\0') >= 0) throw new LayaArgumentException("Input contains a NUL byte");
        // allocateFrom would silently turn lone surrogates into '?'.
        try {
            StandardCharsets.UTF_8.newEncoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).encode(CharBuffer.wrap(s));
        } catch (CharacterCodingException e) {
            throw new LayaArgumentException(what + " is not valid Unicode");
        }
    }

    private interface Body<T> {
        T apply(MemorySegment handle) throws Throwable;
    }

    private <T> T withHandle(Body<T> body) {
        // Fail fast once close is pending rather than queueing behind the writer, which would block
        // (or deadlock a callback waiting on another thread's call); re-checked under the lock.
        if (closing) throw new EngineClosedException();
        lock.readLock().lock();
        try {
            if (closing || released) throw new EngineClosedException();
            return body.apply(handle);
        } catch (Throwable t) {
            throw sneaky(t);
        } finally {
            lock.readLock().unlock();
            // A close() deferred from inside a callback is completed by whichever call exits last.
            if (closing && lock.getReadHoldCount() == 0 && lock.writeLock().tryLock()) release();
        }
    }

    private String run(String request, CallOptions options, long entry, CancellationToken scoped) {
        if (closing) throw new EngineClosedException();
        Objects.requireNonNull(request, "request");
        if (options == null) options = CallOptions.none();
        checkInput(request, "request");
        CallOptions opts = options;
        CancellationToken token = opts.cancellation();
        // Order: closed, conversions (above), cancelled, expired, then the native call.
        if ((token != null && token.isCancelled()) || (scoped != null && scoped.isCancelled()))
            throw new LayaException(Status.CANCELLED, "The call was cancelled before it started");
        return withHandle(h -> {
            Native n = Native.get();
            long timeoutMs = 0;
            if (!opts.timeout().isZero()) {
                Duration remaining = opts.timeout().minusNanos(System.nanoTime() - entry);
                if (remaining.isZero() || remaining.isNegative())
                    throw new LayaException(Status.DEADLINE_EXCEEDED, "The deadline passed before the call started");
                timeoutMs = CallOptions.millis(remaining);
            }
            try (Arena arena = Arena.ofShared()) {
                MemorySegment call = (MemorySegment) n.DEFAULT_CALL_OPTIONS.invoke((SegmentAllocator) arena);
                call.set(ValueLayout.JAVA_LONG, REQUEST_ID, opts.requestId());
                call.set(ValueLayout.JAVA_LONG, TIMEOUT_MS, timeoutMs);
                if (opts.onEvent() != null)
                    call.set(PTR, ON_EVENT, Linker.nativeLinker().upcallStub(
                        MethodHandles.insertArguments(EVENT_TARGET, 0, opts.onEvent()),
                        FunctionDescriptor.ofVoid(PTR, PTR), arena));
                // A flag of this call alone, which close(true) and the tokens set; a token is linked to it, never passed as the flag.
                MemorySegment flag = arena.allocate(4, 4);
                flag.set(ValueLayout.JAVA_INT, 0, 0);
                call.set(PTR, SHOULD_CANCEL, n.CANCEL_FLAG);
                call.set(PTR, CANCEL_USER_DATA, flag);
                synchronized (flags) {
                    if (cancelAll) FLAG.setRelease(flag, 0L, Status.CANCELLED.code());
                    flags.add(flag);
                }
                if (token != null) token.register(flag);
                if (scoped != null) scoped.register(flag);
                MemorySegment result = arena.allocate(PTR);
                MemorySegment error = arena.allocate(PTR);
                try {
                    int status = (int) n.EXECUTE.invokeExact(h, arena.allocateFrom(request), call, result, error);
                    return finish(status, result, error);
                } finally {
                    // Before the arena frees the flag.
                    if (scoped != null) scoped.unregister(flag);
                    if (token != null) token.unregister(flag);
                    synchronized (flags) {
                        flags.remove(flag);
                    }
                }
            }
        });
    }

    private static String readInfo(MemorySegment engine) {
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment result = arena.allocate(PTR);
            MemorySegment error = arena.allocate(PTR);
            int status = (int) Native.get().INFO.invokeExact(engine, result, error);
            return finish(status, result, error);
        } catch (Throwable t) {
            throw sneaky(t);
        }
    }

    /** Frees both out-pointers; returns the result string on success, otherwise throws. */
    private static String finish(int status, MemorySegment result, MemorySegment error) {
        MemorySegment out = result.get(PTR, 0);
        try {
            check(status, error);
            if (out.equals(MemorySegment.NULL))
                throw new LayaException(Status.INFERENCE_ERROR, "Native result was null");
            return text(out);
        } finally {
            if (!out.equals(MemorySegment.NULL)) Native.free(out);
        }
    }

    private static void check(int status, MemorySegment errorSlot) {
        MemorySegment error = errorSlot.get(PTR, 0);
        String message = null;
        if (!error.equals(MemorySegment.NULL)) {
            try {
                message = text(error);
            } finally {
                Native.free(error);
            }
        }
        if (status != 0) throw new LayaException(Status.of(status), message != null ? message : "Engine operation failed");
    }

    private static String text(MemorySegment cString) {
        return cString.reinterpret(Long.MAX_VALUE).getString(0);
    }

    // Upcalls: must never throw, or the JVM aborts.
    private static void event(Consumer<Event> callback, MemorySegment text, MemorySegment user) {
        try {
            Event event = Event.parse(text(text));
            if (event != null) callback.accept(event);
        } catch (Throwable ignored) {
            // observer failures never change inference results
        }
    }
}
