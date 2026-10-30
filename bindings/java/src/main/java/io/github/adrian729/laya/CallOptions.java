package io.github.adrian729.laya;

import java.time.Duration;
import java.util.function.Consumer;

/**
 * Per-call controls. {@code requestId} is an unsigned 64-bit id (default 0) that only appears in
 * events; a negative {@code long} is an id at or above 2^63. {@code timeout} is zero for none,
 * otherwise any positive length, counted from the call's entry (rounded up to whole milliseconds,
 * 1 ms minimum, saturating at the native limit, which never expires); a negative one throws
 * {@link LayaArgumentException}. {@code onEvent} runs synchronously on the calling thread; exceptions
 * it throws are discarded. Keep the token alive for the call.
 */
public record CallOptions(long requestId, Duration timeout, CancellationToken cancellation, Consumer<Event> onEvent) {
    private static final CallOptions NONE = new CallOptions(0, Duration.ZERO, null, null);
    /** Seconds whose milliseconds still fit an unsigned 64-bit count; a longer timeout becomes UINT64_MAX. */
    private static final long MAX_SECONDS = 18_000_000_000_000_000L;

    public CallOptions {
        if (timeout == null) timeout = Duration.ZERO;
        if (timeout.isNegative()) throw new LayaArgumentException("timeout must not be negative");
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

    public CallOptions withOnEvent(Consumer<Event> callback) {
        return new CallOptions(requestId, timeout, cancellation, callback);
    }

    /** A positive length as the native unsigned millisecond count: rounded up, at least 1, saturating at UINT64_MAX (-1L). */
    static long millis(Duration positive) {
        if (positive.getSeconds() > MAX_SECONDS) return -1L;
        // The sum is read as unsigned; a positive length is at least 1 ms after rounding up.
        return positive.getSeconds() * 1000 + (positive.getNano() + 999_999) / 1_000_000;
    }
}
