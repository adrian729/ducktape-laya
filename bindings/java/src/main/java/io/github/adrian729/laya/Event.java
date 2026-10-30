package io.github.adrian729.laya;

/**
 * A passive call event, delivered to {@link CallOptions#onEvent()}. {@code requestId} carries the
 * unsigned 64-bit id of {@link CallOptions#requestId()} ({@link Long#toUnsignedString(long)} prints
 * it); {@code status} is {@link Status#OK} on start and end; {@code elapsedMs} is 0 on start. Events
 * contain no state, prompts, answers or paths.
 */
public record Event(EventKind kind, long requestId, Status status, double elapsedMs) {
    /** Parses the native event JSON ({@code {"event":..,"request_id":..,"status":..,"elapsed_ms":..}}); null if it is malformed. */
    static Event parse(String json) {
        try {
            String kind = string(json, "event");
            EventKind k = switch (kind) {
                case "start" -> EventKind.START;
                case "end" -> EventKind.END;
                case "error" -> EventKind.ERROR;
                default -> null;
            };
            if (k == null) return null;
            return new Event(k, Long.parseUnsignedLong(number(json, "request_id")), Status.of(Integer.parseInt(number(json, "status"))),
                Double.parseDouble(number(json, "elapsed_ms")));
        } catch (RuntimeException e) {
            return null;
        }
    }

    private static int valueStart(String json, String key) {
        int at = json.indexOf("\"" + key + "\"");
        if (at < 0) throw new IllegalArgumentException("missing " + key);
        at = json.indexOf(':', at) + 1;
        while (at < json.length() && json.charAt(at) == ' ') at++;
        return at;
    }

    private static String string(String json, String key) {
        int from = valueStart(json, key);
        if (json.charAt(from) != '"') throw new IllegalArgumentException(key);
        return json.substring(from + 1, json.indexOf('"', from + 1));
    }

    private static String number(String json, String key) {
        int from = valueStart(json, key), to = from;
        while (to < json.length() && ",} ".indexOf(json.charAt(to)) < 0) to++;
        return json.substring(from, to);
    }
}
