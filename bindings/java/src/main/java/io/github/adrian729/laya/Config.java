package io.github.adrian729.laya;

import java.util.Map;
import java.util.Objects;
import java.util.TreeMap;

/**
 * Immutable open configuration, serialized to the native config JSON. Unset (null) fields are omitted
 * so the library defaults apply; keys are written in a fixed order (threads, context_tokens, overflow,
 * max_windows_per_call, then temperatures sorted by key). Values are sent as given and the library
 * validates them (status 1); the binding rejects only what JSON cannot carry: a non-finite
 * temperature ({@link LayaArgumentException}) and a null temperature key or value ({@link NullPointerException}).
 */
public record Config(Integer threads, Integer contextTokens, String overflow, Integer maxWindowsPerCall,
                     Map<String, Double> temperatures) {
    private static final Config NONE = new Config(null, null, null, null, Map.of());

    public Config {
        TreeMap<String, Double> copy = new TreeMap<>();
        if (temperatures != null) {
            for (Map.Entry<String, Double> e : temperatures.entrySet()) {
                String key = Objects.requireNonNull(e.getKey(), "temperature key");
                Double value = Objects.requireNonNull(e.getValue(), "temperature value");
                if (!Double.isFinite(value))
                    throw new LayaArgumentException("temperature for '" + key + "' must be finite");
                copy.put(key, value);
            }
        }
        temperatures = java.util.Collections.unmodifiableMap(copy);
    }

    /** No settings: library defaults. */
    public static Config none() {
        return NONE;
    }

    public Config withThreads(Integer value) {
        return new Config(value, contextTokens, overflow, maxWindowsPerCall, temperatures);
    }

    public Config withContextTokens(Integer value) {
        return new Config(threads, value, overflow, maxWindowsPerCall, temperatures);
    }

    /** Engine-wide overflow policy: reject, truncate_end, truncate_start or scan (null = unset). */
    public Config withOverflow(String value) {
        return new Config(threads, contextTokens, value, maxWindowsPerCall, temperatures);
    }

    /** The most forward passes one call may plan on this engine (null = library default). */
    public Config withMaxWindowsPerCall(Integer value) {
        return new Config(threads, contextTokens, overflow, value, temperatures);
    }

    /** Replaces the temperature overrides, keyed like the GGUF metadata (for example {@code "choice.3_5"}). */
    public Config withTemperatures(Map<String, Double> value) {
        return new Config(threads, contextTokens, overflow, maxWindowsPerCall, value);
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
        if (maxWindowsPerCall != null) sb.append(sb.length() > 1 ? "," : "").append("\"max_windows_per_call\":").append(maxWindowsPerCall);
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

    static StringBuilder quote(StringBuilder sb, String s) {
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
