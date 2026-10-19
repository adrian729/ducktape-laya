package io.github.adrian729.laya;

/** Thread-safe cancellation flag. Cancellation is cooperative and latched per call. */
public final class CancellationToken {
    private volatile boolean cancelled;

    public void cancel() {
        cancelled = true;
    }

    public void reset() {
        cancelled = false;
    }

    public boolean isCancelled() {
        return cancelled;
    }
}
