import io.github.adrian729.laya.Config;
import io.github.adrian729.laya.Engine;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class Consumer {
    // Usage: Consumer model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json
    // With --requests: one engine for all files (one per file with --fresh-engine), one result line per file.
    public static void main(String[] args) throws Exception {
        int at = args.length > 1 && !args[1].startsWith("--") ? 2 : 1;
        String configText = at == 2 ? args[1] : "{\"threads\":4}";
        List<String> files = null;
        boolean fresh = false, usage = args.length < 1;
        for (; at < args.length; at++) {
            if (args[at].equals("--fresh-engine")) fresh = true;
            else if (args[at].equals("--requests") && files == null) {
                files = new ArrayList<>();
                while (at + 1 < args.length && !args[at + 1].startsWith("--")) files.add(args[++at]);
                usage |= files.isEmpty();
            } else usage = true;
        }
        if (usage || (fresh && files == null)) {
            System.err.println("Usage: Consumer model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json");
            System.exit(1);
        }
        try {
            if (files == null) {
                String request = new String(System.in.readAllBytes(), StandardCharsets.UTF_8);
                try (Engine laya = Engine.open(args[0], config(configText))) {
                    System.out.write((laya.execute(request) + "\n").getBytes(StandardCharsets.UTF_8));
                    System.out.flush();
                }
                return;
            }
            Engine laya = null;
            try {
                for (String file : files) {
                    String request = Files.readString(Path.of(file), StandardCharsets.UTF_8);
                    if (laya == null) laya = Engine.open(args[0], config(configText));
                    System.out.write((laya.execute(request) + "\n").getBytes(StandardCharsets.UTF_8));
                    System.out.flush();
                    if (fresh) { laya.close(); laya = null; }
                }
            } finally {
                if (laya != null) laya.close();
            }
        } catch (RuntimeException e) {
            System.err.println(e.getMessage());
            System.exit(1);
        }
    }

    // Optional second argument: open config JSON (threads, context_tokens, overflow, max_windows_per_call, temperatures); flat, so a regex suffices.
    private static Config config(String json) {
        Config config = Config.none();
        Matcher m = Pattern.compile("\"threads\"\\s*:\\s*(\\d+)").matcher(json);
        if (m.find()) config = config.withThreads(Integer.parseInt(m.group(1)));
        m = Pattern.compile("\"context_tokens\"\\s*:\\s*(\\d+)").matcher(json);
        if (m.find()) config = config.withContextTokens(Integer.parseInt(m.group(1)));
        m = Pattern.compile("\"overflow\"\\s*:\\s*\"([a-z_]+)\"").matcher(json);
        if (m.find()) config = config.withOverflow(m.group(1));
        m = Pattern.compile("\"max_windows_per_call\"\\s*:\\s*(\\d+)").matcher(json);
        if (m.find()) config = config.withMaxWindowsPerCall(Integer.parseInt(m.group(1)));
        m = Pattern.compile("\"temperatures\"\\s*:\\s*\\{([^}]*)\\}").matcher(json);
        if (m.find()) {
            Map<String, Double> temperatures = new LinkedHashMap<>();
            Matcher entry = Pattern.compile("\"([^\"]+)\"\\s*:\\s*([-0-9.eE+]+)").matcher(m.group(1));
            while (entry.find()) temperatures.put(entry.group(1), Double.parseDouble(entry.group(2)));
            config = config.withTemperatures(temperatures);
        }
        return config;
    }
}
