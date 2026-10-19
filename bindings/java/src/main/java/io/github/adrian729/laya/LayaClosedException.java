package io.github.adrian729.laya;

/** A call was made after {@link Laya#close()}. Its status is {@link #INVALID_ARGUMENT}. */
public final class LayaClosedException extends LayaException {
    public LayaClosedException() {
        super(INVALID_ARGUMENT, "Laya handle is closed");
    }
}
