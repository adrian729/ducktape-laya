package io.github.adrian729.laya;

import static io.github.adrian729.laya.Native.*;

import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.lang.invoke.MethodHandles;
import java.lang.invoke.MethodType;
import java.util.concurrent.locks.ReentrantReadWriteLock;
import java.util.function.Consumer;

/**
 * A loaded Laya model or a bounded local model router. Thread-safe: native calls on one handle
 * serialize, and {@link #close()} waits for in-flight calls. Inference blocks the calling thread.
 * Closing a handle from inside its own event callback throws {@link IllegalStateException}.
 */
public final class Laya implements AutoCloseable {
    private static final MethodHandle EVENT_TARGET;
    private static final MethodHandle CANCEL_TARGET;

    static {
        try {
            var lookup = MethodHandles.lookup();
            EVENT_TARGET = lookup.findStatic(Laya.class, "event",
                MethodType.methodType(void.class, Consumer.class, MemorySegment.class, MemorySegment.class));
            CANCEL_TARGET = lookup.findStatic(Laya.class, "cancelled",
                MethodType.methodType(int.class, CancellationToken.class, MemorySegment.class));
        } catch (ReflectiveOperationException e) {
            throw new ExceptionInInitializerError(e);
        }
    }

    private final ReentrantReadWriteLock lock = new ReentrantReadWriteLock();
    private final boolean router;
    private MemorySegment handle;

    private Laya(MemorySegment handle, boolean router) {
        this.handle = handle;
        this.router = router;
    }

    /** Runtime identity/capabilities without loading a model. No telemetry or input data. */
    public static String runtimeInfo() {
        return readInfo(MemorySegment.NULL);
    }

    public static Laya open(String modelPath, int threads) {
        return create(OPEN, modelPath, threads, false);
    }

    /** Opens a bounded, lazy local model registry; see docs/API.md for the config. */
    public static Laya openRouter(String configJson, int threads) {
        return create(ROUTER_OPEN, configJson, threads, true);
    }

    /** Model metadata for a single model; routers report common runtime capabilities. */
    public String info() {
        return withHandle(h -> readInfo(router ? MemorySegment.NULL : h));
    }

    /** Legacy primitive request semantics: unrelated top-level metadata is ignored. */
    public String decide(String request) {
        return run(request, CallOptions.none(), true);
    }

    public String decide(String request, CallOptions options) {
        return run(request, options, true);
    }

    /** Executes one primitive/structured request or a bounded JSON array of them. */
    public String execute(String request) {
        return run(request, CallOptions.none(), false);
    }

    public String execute(String request, CallOptions options) {
        return run(request, options, false);
    }

    @Override
    public void close() {
        // The write lock waits for every read hold, including this thread's own.
        if (lock.getReadHoldCount() > 0)
            throw new IllegalStateException("close() called from inside an active call on this handle");
        lock.writeLock().lock();
        try {
            if (handle == null) return;
            try {
                (router ? ROUTER_CLOSE : CLOSE).invokeExact(handle);
            } catch (Throwable t) {
                throw sneaky(t);
            } finally {
                handle = null;
            }
        } finally {
            lock.writeLock().unlock();
        }
    }

    private static Laya create(MethodHandle open, String input, int threads, boolean router) {
        checkInput(input);
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment options = (MemorySegment) DEFAULT_OPTIONS.invoke((SegmentAllocator) arena);
            options.set(ValueLayout.JAVA_INT, 8, threads);
            MemorySegment out = arena.allocate(PTR);
            MemorySegment error = arena.allocate(PTR);
            int status = (int) open.invokeExact(arena.allocateFrom(input), options, out, error);
            check(status, error);
            MemorySegment raw = out.get(PTR, 0);
            if (raw.equals(MemorySegment.NULL))
                throw new LayaException(LayaException.MODEL_ERROR, "Native engine returned null");
            return new Laya(raw, router);
        } catch (Throwable t) {
            throw sneaky(t);
        }
    }

    private static void checkInput(String s) {
        if (s == null) throw new LayaException(LayaException.INVALID_ARGUMENT, "Input is null");
        if (s.indexOf('\0') >= 0) throw new LayaException(LayaException.INVALID_ARGUMENT, "Input contains a NUL byte");
    }

    private interface Body<T> {
        T apply(MemorySegment handle) throws Throwable;
    }

    private <T> T withHandle(Body<T> body) {
        lock.readLock().lock();
        try {
            if (handle == null) throw new LayaException(LayaException.INVALID_ARGUMENT, "Laya handle is closed");
            return body.apply(handle);
        } catch (Throwable t) {
            throw sneaky(t);
        } finally {
            lock.readLock().unlock();
        }
    }

    private String run(String request, CallOptions options, boolean legacy) {
        checkInput(request);
        return withHandle(h -> {
            try (Arena arena = Arena.ofConfined()) {
                MemorySegment observer = arena.allocate(OBSERVER_SIZE, 8);
                observer.set(ValueLayout.JAVA_LONG, 0, options.requestId());
                if (options.onEvent() != null)
                    observer.set(PTR, 8, Linker.nativeLinker().upcallStub(
                        MethodHandles.insertArguments(EVENT_TARGET, 0, options.onEvent()),
                        FunctionDescriptor.ofVoid(PTR, PTR), arena));
                MemorySegment native_ = MemorySegment.NULL;
                if (options.timeoutMillis() != 0 || options.cancellation() != null) {
                    native_ = (MemorySegment) DEFAULT_CALL_OPTIONS.invoke((SegmentAllocator) arena);
                    native_.set(ValueLayout.JAVA_LONG, 8, options.timeoutMillis());
                    if (options.cancellation() != null)
                        native_.set(PTR, 16, Linker.nativeLinker().upcallStub(
                            MethodHandles.insertArguments(CANCEL_TARGET, 0, options.cancellation()),
                            FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR), arena));
                }
                MemorySegment result = arena.allocate(PTR);
                MemorySegment error = arena.allocate(PTR);
                MethodHandle target = router ? ROUTER_EXECUTE : legacy ? DECIDE : EXECUTE;
                int status = (int) target.invokeExact(h, arena.allocateFrom(request), observer, native_, result, error);
                return finish(status, result, error);
            }
        });
    }

    private static String readInfo(MemorySegment engine) {
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment result = arena.allocate(PTR);
            MemorySegment error = arena.allocate(PTR);
            int status = (int) INFO.invokeExact(engine, result, error);
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

    private static int cancelled(CancellationToken token, MemorySegment user) {
        try {
            return token.isCancelled() ? LayaException.CANCELLED : 0;
        } catch (Throwable t) {
            return LayaException.CANCELLED;
        }
    }
}
