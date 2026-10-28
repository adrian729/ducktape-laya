package io.github.adrian729.laya;

/** The binding rejected an argument it cannot convert (NUL byte, invalid Unicode, non-finite number, out-of-unit option). Its status is {@link Status#INVALID_ARGUMENT}. */
public class LayaArgumentException extends IllegalArgumentException {
    public LayaArgumentException(String message) {
        super(message);
    }

    public Status status() {
        return Status.INVALID_ARGUMENT;
    }
}
