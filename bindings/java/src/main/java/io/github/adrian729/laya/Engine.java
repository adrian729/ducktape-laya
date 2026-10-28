package io.github.adrian729.laya;

import static io.github.adrian729.laya.Native.*;

import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.lang.invoke.MethodHandles;
import java.lang.invoke.MethodType;
import java.lang.ref.Cleaner;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Objects;
import java.util.concurrent.locks.ReentrantReadWriteLock;
import java.util.function.Consumer;

/**
 * A loaded model or a bounded local model router. Thread-safe: native calls on one handle
 * serialize, and {@link #close()} waits for in-flight calls. Inference blocks the calling thread.
 * Closing a handle from inside its own event callback releases it once the running call finishes.
 */
public final class Engine implements AutoCloseable {
    private static final MethodHandle EVENT_TARGET;
    private static final Cleaner CLEANER = Cleaner.create();

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
     */
    public String execute(String request) {
        return run(request, CallOptions.none());
    }

    public String execute(String request, CallOptions options) {
        return run(request, options);
    }

    /**
     * Waits for in-flight calls, then releases the native handle. Idempotent. Called from inside one
     * of this handle's own calls (an event callback), it cannot wait for that call: it rejects new
     * calls and returns, and the last running call releases the handle when it finishes.
     */
    @Override
    public void close() {
        closing = true;
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

    private String run(String request, CallOptions options) {
        if (closing) throw new EngineClosedException();
        Objects.requireNonNull(request, "request");
        if (options == null) options = CallOptions.none();
        checkInput(request, "request");
        CallOptions opts = options;
        CancellationToken token = opts.cancellation();
        // Order: closed, conversions (above), cancelled, then the native call.
        if (token != null && token.isCancelled())
            throw new LayaException(Status.CANCELLED, "The call was cancelled before it started");
        return withHandle(h -> {
            Native n = Native.get();
            try (Arena arena = Arena.ofShared()) {
                MemorySegment flag = null;
                MemorySegment call = (MemorySegment) n.DEFAULT_CALL_OPTIONS.invoke((SegmentAllocator) arena);
                call.set(ValueLayout.JAVA_LONG, REQUEST_ID, opts.requestId());
                call.set(ValueLayout.JAVA_LONG, TIMEOUT_MS, opts.timeoutMillis());
                if (opts.onEvent() != null)
                    call.set(PTR, ON_EVENT, Linker.nativeLinker().upcallStub(
                        MethodHandles.insertArguments(EVENT_TARGET, 0, opts.onEvent()),
                        FunctionDescriptor.ofVoid(PTR, PTR), arena));
                if (token != null) {
                    flag = arena.allocate(4, 4);
                    flag.set(ValueLayout.JAVA_INT, 0, 0);
                    call.set(PTR, SHOULD_CANCEL, n.CANCEL_FLAG);
                    call.set(PTR, CANCEL_USER_DATA, flag);
                    token.register(flag);
                }
                MemorySegment result = arena.allocate(PTR);
                MemorySegment error = arena.allocate(PTR);
                try {
                    int status = (int) n.EXECUTE.invokeExact(h, arena.allocateFrom(request), call, result, error);
                    return finish(status, result, error);
                } finally {
                    if (flag != null) token.unregister(flag); // before the arena frees it
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
    private static void event(Consumer<String> callback, MemorySegment text, MemorySegment user) {
        try {
            callback.accept(text(text));
        } catch (Throwable ignored) {
            // observer failures never change inference results
        }
    }
}
