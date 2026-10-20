import io.github.adrian729.laya.Config;
import io.github.adrian729.laya.Laya;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class Consumer {
    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("Usage: Consumer model.gguf [config.json] < request.json");
            System.exit(1);
        }
        String request = new String(System.in.readAllBytes(), StandardCharsets.UTF_8);
        try (Laya laya = Laya.open(args[0], config(args.length > 1 ? args[1] : "{\"threads\":4}"))) {
            System.out.write((laya.execute(request) + "\n").getBytes(StandardCharsets.UTF_8));
            System.out.flush();
        } catch (RuntimeException e) {
            System.err.println(e.getMessage());
            System.exit(1);
        }
    }

    // Optional second argument: open config JSON (threads, context_tokens, overflow, temperatures); flat, so a regex suffices.
    private static Config config(String json) {
        Config config = Config.none();
        Matcher m = Pattern.compile("\"threads\"\\s*:\\s*(\\d+)").matcher(json);
        if (m.find()) config = config.withThreads(Integer.parseInt(m.group(1)));
        m = Pattern.compile("\"context_tokens\"\\s*:\\s*(\\d+)").matcher(json);
        if (m.find()) config = config.withContextTokens(Integer.parseInt(m.group(1)));
        m = Pattern.compile("\"overflow\"\\s*:\\s*\"([a-z_]+)\"").matcher(json);
        if (m.find()) config = config.withOverflow(m.group(1));
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
