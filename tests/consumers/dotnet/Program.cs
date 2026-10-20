using Ducktape.Laya;

if (args.Length < 1)
{
    Console.Error.WriteLine("Usage: Consumer model.gguf [config.json] < request.json");
    return 1;
}
try
{
    string request;
    using (var input = new StreamReader(Console.OpenStandardInput(), new System.Text.UTF8Encoding(false)))
        request = input.ReadToEnd();
    // Optional second argument: open config JSON (threads, context_tokens, overflow, temperatures).
    using var config = System.Text.Json.JsonDocument.Parse(args.Length > 1 ? args[1] : "{\"threads\":4}");
    var root = config.RootElement;
    using var engine = LayaEngine.Open(args[0], new LayaConfig
    {
        Threads = root.TryGetProperty("threads", out var threads) ? threads.GetInt32() : 0,
        ContextTokens = root.TryGetProperty("context_tokens", out var context) ? context.GetInt32() : null,
        Overflow = root.TryGetProperty("overflow", out var overflow) ? overflow.GetString() : null,
        Temperatures = root.TryGetProperty("temperatures", out var temps)
            ? temps.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetDouble()) : null,
    });
    var output = new System.Text.UTF8Encoding(false).GetBytes(engine.Execute(request) + "\n");
    using var stdout = Console.OpenStandardOutput();
    stdout.Write(output);
    return 0;
}
catch (Exception e)
{
    Console.Error.WriteLine(e.Message);
    return 1;
}
