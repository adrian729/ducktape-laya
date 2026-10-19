package io.github.adrian729.laya;

import java.time.Duration;
import java.util.function.Consumer;

/**
 * Per-call controls. {@code timeout} is zero for none, otherwise up to 24 hours (rounded up to whole
 * milliseconds, 1 ms minimum); negative or longer values throw {@link IllegalArgumentException}. {@code onEvent} runs synchronously on the calling thread; exceptions
 * it throws are discarded. Keep the token alive for the call.
 */
public record CallOptions(long requestId, Duration timeout, CancellationToken cancellation, Consumer<String> onEvent) {
    private static final long MAX_TIMEOUT_MS = 86_400_000L;
    private static final CallOptions NONE = new CallOptions(0, Duration.ZERO, null, null);

    public CallOptions {
        if (timeout == null) timeout = Duration.ZERO;
        if (timeout.isNegative() || timeout.compareTo(Duration.ofMillis(MAX_TIMEOUT_MS)) > 0)
            throw new IllegalArgumentException("timeout must be between 0 and 24 hours");
    }

    public static CallOptions none() {
        return NONE;
    }

    public CallOptions withRequestId(long id) {
        return new CallOptions(id, timeout, cancellation, onEvent);
    }

    public CallOptions withTimeout(Duration value) {
        return new CallOptions(requestId, value, cancellation, onEvent);
    }

    public CallOptions withCancellation(CancellationToken token) {
        return new CallOptions(requestId, timeout, token, onEvent);
    }

    public CallOptions withOnEvent(Consumer<String> callback) {
        return new CallOptions(requestId, timeout, cancellation, callback);
    }

    long timeoutMillis() {
        if (timeout.isZero()) return 0;
        return Math.max(1, (timeout.toNanos() + 999_999) / 1_000_000);
    }
}
