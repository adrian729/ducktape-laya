package io.github.adrian729.laya;

import java.nio.file.Path;
import java.util.Map;
import java.util.Objects;
import java.util.TreeMap;

/**
 * A bounded, lazy local model registry; see docs/API.md. Serialized to the native router JSON with
 * routes sorted by id. {@code maxLoaded} null means the library default. Values are sent as given
 * and the library validates them (status 1).
 *
 * @param models route id to route
 * @param maxLoaded models kept loaded at once, or null
 * @param auto routes for {@code "model": "auto"}, or null
 */
public record RouterConfig(Map<String, Route> models, Integer maxLoaded, Auto auto) {
    /** One route: a model file and an optional config that overrides the shared one key by key. */
    public record Route(Path path, Config config) {
        public Route {
            Objects.requireNonNull(path, "path");
        }

        public Route(Path path) {
            this(path, null);
        }
    }

    /** The routes {@code "model": "auto"} chooses between by language. */
    public record Auto(String english, String defaultRoute) {
        public Auto {
            Objects.requireNonNull(english, "english");
            Objects.requireNonNull(defaultRoute, "defaultRoute");
        }
    }

    public RouterConfig {
        TreeMap<String, Route> copy = new TreeMap<>();
        for (Map.Entry<String, Route> e : Objects.requireNonNull(models, "models").entrySet())
            copy.put(Objects.requireNonNull(e.getKey(), "route id"), Objects.requireNonNull(e.getValue(), "route"));
        models = java.util.Collections.unmodifiableMap(copy);
    }

    public RouterConfig(Map<String, Route> models) {
        this(models, null, null);
    }

    String toJson() {
        StringBuilder sb = new StringBuilder("{\"models\":{");
        boolean first = true;
        for (Map.Entry<String, Route> e : models.entrySet()) {
            if (!first) sb.append(',');
            first = false;
            Config.quote(sb, e.getKey()).append(":{\"path\":");
            Config.quote(sb, e.getValue().path().toString());
            String config = e.getValue().config() == null ? null : e.getValue().config().toJson();
            if (config != null) sb.append(",\"config\":").append(config);
            sb.append('}');
        }
        sb.append('}');
        if (maxLoaded != null) sb.append(",\"max_loaded\":").append(maxLoaded);
        if (auto != null) {
            sb.append(",\"auto\":{\"english\":");
            Config.quote(sb, auto.english()).append(",\"default\":");
            Config.quote(sb, auto.defaultRoute()).append('}');
        }
        return sb.append('}').toString();
    }
}
