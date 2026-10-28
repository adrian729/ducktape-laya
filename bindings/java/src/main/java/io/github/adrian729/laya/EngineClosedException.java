package io.github.adrian729.laya;

/** A call was made after {@link Engine#close()}. Its status is {@link Status#INVALID_ARGUMENT}. */
public final class EngineClosedException extends LayaException {
    public EngineClosedException() {
        super(Status.INVALID_ARGUMENT, "Engine is closed");
    }
}
