package io.github.adrian729.laya;

/** Failure reported by the native library (or rejected before reaching it). */
public class LayaException extends RuntimeException {
    private final Status status;

    public LayaException(Status status, String message) {
        super(message);
        this.status = status;
    }

    public LayaException(Status status, String message, Throwable cause) {
        super(message, cause);
        this.status = status;
    }

    /** The native status. */
    public Status status() {
        return status;
    }
}
