package io.github.adrian729.laya;

/** The native library is missing, cannot be loaded or does not match this package. Its status is {@link Status#UNSUPPORTED_PLATFORM}. */
public class LayaLoadException extends LayaException {
    public LayaLoadException(String message) {
        super(Status.UNSUPPORTED_PLATFORM, message);
    }

    public LayaLoadException(String message, Throwable cause) {
        super(Status.UNSUPPORTED_PLATFORM, message, cause);
    }
}
