package io.github.adrian729.laya;

import java.lang.foreign.MemorySegment;
import java.lang.foreign.ValueLayout;
import java.lang.invoke.VarHandle;
import java.util.ArrayList;
import java.util.List;

/**
 * Thread-safe cancellation flag. {@link #cancel()} stops every call currently using the token and
 * every later call; a cancelled token stays cancelled, so use a new token for new calls.
 * Cancellation is cooperative: native workers poll a per-call flag, so no code runs on their threads.
 */
public final class CancellationToken {
    private static final VarHandle FLAG = ValueLayout.JAVA_INT.varHandle();

    private final List<MemorySegment> active = new ArrayList<>();
    private boolean cancelled; // guarded by this

    public void cancel() {
        synchronized (this) {
            cancelled = true;
            for (MemorySegment flag : active) FLAG.setRelease(flag, 0L, Status.CANCELLED.code());
        }
    }

    public boolean isCancelled() {
        synchronized (this) {
            return cancelled;
        }
    }

    /** Registers a call's 4-byte flag; set at once if already cancelled. */
    void register(MemorySegment flag) {
        synchronized (this) {
            if (cancelled) FLAG.setRelease(flag, 0L, Status.CANCELLED.code());
            active.add(flag);
        }
    }

    /** Must be called before the flag's memory is freed. */
    void unregister(MemorySegment flag) {
        synchronized (this) {
            active.remove(flag);
        }
    }
}
