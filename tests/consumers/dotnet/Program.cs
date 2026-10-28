// Usage: Consumer model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json
// With --requests: one engine for all files (one per file with --fresh-engine), one result line per file.
using Ducktape.Laya;

int at = args.Length > 1 && !args[1].StartsWith("--") ? 2 : 1;
List<string>? files = null;
bool fresh = false, usage = args.Length < 1;
for (; at < args.Length; at++)
{
    if (args[at] == "--fresh-engine") fresh = true;
    else if (args[at] == "--requests" && files == null)
    {
        files = new List<string>();
        while (at + 1 < args.Length && !args[at + 1].StartsWith("--")) files.Add(args[++at]);
        usage |= files.Count == 0;
    }
    else usage = true;
}
if (usage || (fresh && files == null))
{
    Console.Error.WriteLine("Usage: Consumer model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json");
    return 1;
}
try
{
    var utf8 = new System.Text.UTF8Encoding(false);
    // Optional second argument: open config JSON (threads, context_tokens, overflow, max_windows_per_call, temperatures).
    using var config = System.Text.Json.JsonDocument.Parse(args.Length > 1 && !args[1].StartsWith("--") ? args[1] : "{\"threads\":4}");
    var root = config.RootElement;
    var options = new Config
    {
        Threads = root.TryGetProperty("threads", out var threads) ? threads.GetInt32() : null,
        ContextTokens = root.TryGetProperty("context_tokens", out var context) ? context.GetInt32() : null,
        MaxWindowsPerCall = root.TryGetProperty("max_windows_per_call", out var ceiling) ? ceiling.GetInt32() : null,
        Overflow = root.TryGetProperty("overflow", out var overflow) ? overflow.GetString() : null,
        Temperatures = root.TryGetProperty("temperatures", out var temps)
            ? temps.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetDouble()) : null,
    };
    using var stdout = Console.OpenStandardOutput();
    if (files == null)
    {
        string request;
        using (var input = new StreamReader(Console.OpenStandardInput(), utf8))
            request = input.ReadToEnd();
        using var engine = Engine.Open(args[0], options);
        stdout.Write(utf8.GetBytes(engine.Execute(request) + "\n"));
        return 0;
    }
    Engine? shared = null;
    try
    {
        foreach (var file in files)
        {
            var request = File.ReadAllText(file, utf8);
            shared ??= Engine.Open(args[0], options);
            stdout.Write(utf8.GetBytes(shared.Execute(request) + "\n"));
            stdout.Flush();
            if (fresh) { shared.Dispose(); shared = null; }
        }
    }
    finally { shared?.Dispose(); }
    return 0;
}
catch (Exception e)
{
    Console.Error.WriteLine(e.Message);
    return 1;
}
