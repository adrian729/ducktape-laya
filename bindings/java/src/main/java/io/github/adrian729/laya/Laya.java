package io.github.adrian729.laya;

import static io.github.adrian729.laya.Native.*;

import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.lang.invoke.MethodHandles;
import java.lang.invoke.MethodType;
import java.lang.ref.Cleaner;
import java.util.Objects;
import java.util.concurrent.locks.ReentrantReadWriteLock;
import java.util.function.Consumer;

/**
 * A loaded Laya model or a bounded local model router. Thread-safe: native calls on one handle
 * serialize, and {@link #close()} waits for in-flight calls. Inference blocks the calling thread.
 * Closing a handle from inside its own event callback releases it once the running call finishes.
 */
public final class Laya implements AutoCloseable {
    private static final MethodHandle EVENT_TARGET;
    private static final Cleaner CLEANER = Cleaner.create();

    static {
        try {
            EVENT_TARGET = MethodHandles.lookup().findStatic(Laya.class, "event",
                MethodType.methodType(void.class, Consumer.class, MemorySegment.class, MemorySegment.class));
        } catch (ReflectiveOperationException e) {
            throw new ExceptionInInitializerError(e);
        }
    }

    /** Owns the native handle; deliberately holds no reference to the Laya so the Cleaner can run. */
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
    private final boolean router;
    private final MemorySegment handle;
    private final Cleaner.Cleanable cleanable;
    private volatile boolean closing;
    private boolean released; // guarded by the write lock

    private Laya(MemorySegment handle, boolean router, MethodHandle close) {
        this.handle = handle;
        this.router = router;
        this.cleanable = CLEANER.register(this, new State(handle, close));
    }

    /** Runtime identity/capabilities without loading a model. No telemetry or input data. */
    public static String runtimeInfo() {
        return readInfo(MemorySegment.NULL);
    }

    /** Opens a model with an automatic thread count. */
    public static Laya open(String modelPath) {
        return open(modelPath, 0);
    }

    /**
     * Opens a model. {@code threads} is 0 (automatic) to 256, otherwise {@link IllegalArgumentException}.
     * A null path throws {@link NullPointerException}. Call {@link #close()} to release it promptly;
     * a Cleaner releases it if the object is dropped without closing.
     */
    public static Laya open(String modelPath, int threads) {
        return create(false, modelPath, threads);
    }

    /** Opens a router with an automatic thread count. */
    public static Laya openRouter(String configJson) {
        return openRouter(configJson, 0);
    }

    /** Opens a bounded, lazy local model registry; see docs/API.md for the config. Arguments as for {@link #open(String, int)}. */
    public static Laya openRouter(String configJson, int threads) {
        return create(true, configJson, threads);
    }

    /** Model metadata for a single model; routers report common runtime capabilities. */
    public String info() {
        return withHandle(h -> readInfo(router ? MemorySegment.NULL : h));
    }

    /**
     * Legacy primitive request semantics: unrelated top-level metadata is ignored. On a router,
     * {@code decide} uses the strict {@link #execute} semantics: it always requires {@code model}
     * and rejects unrelated metadata. Null options mean {@link CallOptions#none()}.
     */
    public String decide(String request) {
        return run(request, CallOptions.none(), true);
    }

    public String decide(String request, CallOptions options) {
        return run(request, options, true);
    }

    /**
     * Executes one primitive/structured request or a bounded JSON array of them. {@code onEvent}
     * runs on the calling thread. Null options mean {@link CallOptions#none()}.
     */
    public String execute(String request) {
        return run(request, CallOptions.none(), false);
    }

    public String execute(String request, CallOptions options) {
        return run(request, options, false);
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

    private static Laya create(boolean router, String input, int threads) {
        Objects.requireNonNull(input, router ? "configJson" : "modelPath");
        if (threads < 0 || threads > 256) throw new IllegalArgumentException("threads must be between 0 and 256");
        checkInput(input);
        Native n = Native.get();
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment options = (MemorySegment) n.DEFAULT_OPTIONS.invoke((SegmentAllocator) arena);
            options.set(ValueLayout.JAVA_INT, 8, threads);
            MemorySegment out = arena.allocate(PTR);
            MemorySegment error = arena.allocate(PTR);
            int status = (int) (router ? n.ROUTER_OPEN : n.OPEN).invokeExact(arena.allocateFrom(input), options, out, error);
            check(status, error);
            MemorySegment raw = out.get(PTR, 0);
            if (raw.equals(MemorySegment.NULL))
                throw new LayaException(LayaException.MODEL_ERROR, "Native engine returned null");
            return new Laya(raw, router, router ? n.ROUTER_CLOSE : n.CLOSE);
        } catch (Throwable t) {
            throw sneaky(t);
        }
    }

    private static void checkInput(String s) {
        if (s.indexOf('\0') >= 0) throw new LayaException(LayaException.INVALID_ARGUMENT, "Input contains a NUL byte");
    }

    private interface Body<T> {
        T apply(MemorySegment handle) throws Throwable;
    }

    private <T> T withHandle(Body<T> body) {
        lock.readLock().lock();
        try {
            if (closing || released) throw new LayaClosedException();
            return body.apply(handle);
        } catch (Throwable t) {
            throw sneaky(t);
        } finally {
            lock.readLock().unlock();
            // A close() deferred from inside a callback is completed by whichever call exits last.
            if (closing && lock.getReadHoldCount() == 0 && lock.writeLock().tryLock()) release();
        }
    }

    private String run(String request, CallOptions options, boolean legacy) {
        Objects.requireNonNull(request, "request");
        if (options == null) options = CallOptions.none();
        checkInput(request);
        CallOptions opts = options;
        Native n = Native.get();
        return withHandle(h -> {
            CancellationToken token = opts.cancellation();
            try (Arena arena = Arena.ofShared()) {
                MemorySegment flag = null;
                MemorySegment observer = arena.allocate(OBSERVER_SIZE, 8);
                observer.set(ValueLayout.JAVA_LONG, 0, opts.requestId());
                if (opts.onEvent() != null)
                    observer.set(PTR, 8, Linker.nativeLinker().upcallStub(
                        MethodHandles.insertArguments(EVENT_TARGET, 0, opts.onEvent()),
                        FunctionDescriptor.ofVoid(PTR, PTR), arena));
                MemorySegment native_ = MemorySegment.NULL;
                if (opts.timeoutMillis() != 0 || token != null) {
                    native_ = (MemorySegment) n.DEFAULT_CALL_OPTIONS.invoke((SegmentAllocator) arena);
                    native_.set(ValueLayout.JAVA_LONG, 8, opts.timeoutMillis());
                    if (token != null) {
                        flag = arena.allocate(4, 4);
                        flag.set(ValueLayout.JAVA_INT, 0, 0);
                        native_.set(PTR, 16, n.CANCEL_FLAG);
                        native_.set(PTR, 24, flag);
                        token.register(flag);
                    }
                }
                MemorySegment result = arena.allocate(PTR);
                MemorySegment error = arena.allocate(PTR);
                MethodHandle target = router ? n.ROUTER_EXECUTE : legacy ? n.DECIDE : n.EXECUTE;
                try {
                    int status = (int) target.invokeExact(h, arena.allocateFrom(request), observer, native_, result, error);
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
                throw new LayaException(LayaException.INFERENCE_ERROR, "Native result was null");
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
        if (status != 0) throw new LayaException(status, message != null ? message : "Laya operation failed");
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
