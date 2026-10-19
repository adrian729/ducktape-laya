import io.github.adrian729.laya.Laya;
import java.nio.charset.StandardCharsets;

public final class Consumer {
    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("Usage: Consumer model.gguf < request.json");
            System.exit(1);
        }
        String request = new String(System.in.readAllBytes(), StandardCharsets.UTF_8);
        try (Laya laya = Laya.open(args[0], 4)) {
            System.out.write((laya.execute(request) + "\n").getBytes(StandardCharsets.UTF_8));
            System.out.flush();
        } catch (RuntimeException e) {
            System.err.println(e.getMessage());
            System.exit(1);
        }
    }
}
