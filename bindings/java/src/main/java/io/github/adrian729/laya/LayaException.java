package io.github.adrian729.laya;

/** Failure reported by the native library (or rejected before reaching it). */
public final class LayaException extends RuntimeException {
    public static final int INVALID_ARGUMENT = 1;
    public static final int MODEL_ERROR = 2;
    public static final int INFERENCE_ERROR = 3;
    public static final int OUT_OF_MEMORY = 4;
    public static final int CANCELLED = 5;
    public static final int DEADLINE_EXCEEDED = 6;
    public static final int UNSUPPORTED_PLATFORM = 7;

    private final int status;

    public LayaException(int status, String message) {
        super(message);
        this.status = status;
    }

    public LayaException(int status, String message, Throwable cause) {
        super(message, cause);
        this.status = status;
    }

    /** Native {@code dtl_status} value; see the constants above. */
    public int status() {
        return status;
    }
}
