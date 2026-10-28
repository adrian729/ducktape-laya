package io.github.adrian729.laya;

/** Native {@code dtl_status} values. */
public enum Status {
    OK(0),
    INVALID_ARGUMENT(1),
    MODEL_ERROR(2),
    INFERENCE_ERROR(3),
    OUT_OF_MEMORY(4),
    CANCELLED(5),
    DEADLINE_EXCEEDED(6),
    UNSUPPORTED_PLATFORM(7);

    private final int code;

    Status(int code) {
        this.code = code;
    }

    /** The native {@code dtl_status} integer. */
    public int code() {
        return code;
    }

    /** The status for a native value; a value outside the ABI maps to {@link #INFERENCE_ERROR}. */
    public static Status of(int code) {
        for (Status s : values()) if (s.code == code) return s;
        return INFERENCE_ERROR;
    }
}
