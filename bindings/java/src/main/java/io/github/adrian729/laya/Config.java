package io.github.adrian729.laya;

import java.util.Map;
import java.util.TreeMap;

/**
 * Immutable open configuration, serialized to the native config JSON. Unset fields are omitted so
 * the library defaults apply; keys are written in a fixed order (threads, context_tokens, overflow,
 * then temperatures sorted by key). {@code threads} is 0 (automatic: at most 4 and at most the CPUs the process may use) to 256, {@code contextTokens} is
 * positive (the library enforces its own minimum and the model's trained length, status 1), and
 * each temperature is finite and positive; violations throw {@link IllegalArgumentException}.
 */
public record Config(Integer threads, Integer contextTokens, String overflow, Map<String, Double> temperatures) {
    private static final Config NONE = new Config(null, null, null, Map.of());

    public Config {
        if (threads != null && (threads < 0 || threads > 256))
            throw new IllegalArgumentException("threads must be between 0 and 256");
        if (contextTokens != null && contextTokens <= 0)
            throw new IllegalArgumentException("contextTokens must be positive");
        TreeMap<String, Double> copy = new TreeMap<>();
        if (temperatures != null) {
            for (Map.Entry<String, Double> e : temperatures.entrySet()) {
                String key = e.getKey();
                Double value = e.getValue();
                if (key == null || key.isEmpty()) throw new IllegalArgumentException("temperature key must not be empty");
                if (value == null || !Double.isFinite(value) || value <= 0)
                    throw new IllegalArgumentException("temperature for '" + key + "' must be finite and > 0");
                copy.put(key, value);
            }
        }
        temperatures = java.util.Collections.unmodifiableMap(copy);
    }

    /** No settings: library defaults. */
    public static Config none() {
        return NONE;
    }

    public Config withThreads(int value) {
        return new Config(value, contextTokens, overflow, temperatures);
    }

    public Config withContextTokens(int value) {
        return new Config(threads, value, overflow, temperatures);
    }

    /** Engine-wide overflow policy: reject, truncate_end, truncate_start or scan (null = unset). The library validates it. */
    public Config withOverflow(String value) {
        return new Config(threads, contextTokens, value, temperatures);
    }

    /** Replaces the temperature overrides, keyed like the GGUF metadata (for example {@code "choice.3_5"}). */
    public Config withTemperatures(Map<String, Double> value) {
        return new Config(threads, contextTokens, overflow, value);
    }

    /** The config JSON object, or {@code null} when nothing is set. */
    String toJson() {
        StringBuilder sb = new StringBuilder("{");
        if (threads != null) sb.append("\"threads\":").append(threads);
        if (contextTokens != null) sb.append(sb.length() > 1 ? "," : "").append("\"context_tokens\":").append(contextTokens);
        if (overflow != null) {
            sb.append(sb.length() > 1 ? "," : "").append("\"overflow\":");
            quote(sb, overflow);
        }
        if (!temperatures.isEmpty()) {
            sb.append(sb.length() > 1 ? "," : "").append("\"temperatures\":{");
            boolean first = true;
            for (Map.Entry<String, Double> e : temperatures.entrySet()) {
                if (!first) sb.append(',');
                first = false;
                quote(sb, e.getKey()).append(':').append(e.getValue());
            }
            sb.append('}');
        }
        return sb.length() == 1 ? null : sb.append('}').toString();
    }

    private static StringBuilder quote(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\') sb.append('\\').append(c);
            else if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
            else sb.append(c);
        }
        return sb.append('"');
    }
}
