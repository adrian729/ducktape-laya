using Ducktape.Laya;
using Xunit;

namespace DucktapeLaya.Tests;

public class EngineTests
{
    const string Primitive = """{"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}""";
    const string Schema = """{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}""";

    static readonly LayaConfig Two = new() { Threads = 2 };
    static readonly LayaConfig One = new() { Threads = 1 };
    static string? Model => Environment.GetEnvironmentVariable("LAYA_MODEL") is { Length: > 0 } m ? m : null;
    static string Quote(string path) => path.Replace("\\", "\\\\").Replace("\"", "\\\"");
    static string Config(params (string Name, string Path)[] models) =>
        "{\"models\":{" + string.Join(",", models.Select(m => $"\"{m.Name}\":\"{Quote(m.Path)}\"")) + "},\"max_loaded\":1}";
    static string WithRaw(string json, string members) => "{" + members + "," + json[1..];
    static string WithKey(string json, string key, string value) => "{" + $"\"{key}\":\"{value}\"," + json[1..];

    [Fact]
    public void RejectsBadPath() => Assert.Throws<LayaException>(() => LayaEngine.Open("missing.gguf", Two));

    [Fact]
    public void RejectsInvalidUnicode()
    {
        const string lone = "a\ud800b";
        var e = Assert.Throws<LayaException>(() => LayaEngine.Open(lone, Two));
        Assert.Equal(1, e.Status);
        Assert.Contains("model path is not valid Unicode", e.Message);
        Assert.Contains("router config is not valid Unicode", Assert.Throws<LayaException>(() => LayaEngine.OpenRouter("{\"models\":{\"x\":\"" + lone + "\"}}")).Message);
        Assert.Equal(1, Assert.Throws<LayaException>(() => LayaEngine.Open("x", new LayaConfig { Temperatures = new Dictionary<string, double> { [lone] = 1 } })).Status);
        var file = Path.GetTempFileName();
        try
        {
            using var router = LayaEngine.OpenRouter("{\"models\":{\"x\":" + System.Text.Json.JsonSerializer.Serialize(file) + "}}");
            var bad = Assert.Throws<LayaException>(() => router.Execute("{\"state\":\"" + lone + "\"}"));
            Assert.Equal(1, bad.Status);
            Assert.Equal("request is not valid Unicode", bad.Message);
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void RejectsNul()
    {
        Assert.Equal(1, Assert.Throws<LayaException>(() => LayaEngine.Open("a\0b", Two)).Status);
        Assert.Equal(LayaStatus.InvalidArgument, Assert.Throws<LayaException>(() => LayaEngine.OpenRouter("{\0}")).Code);
    }

    [Fact]
    public void ValidatesArguments()
    {
        Assert.Throws<ArgumentNullException>(() => LayaEngine.Open(null!));
        Assert.Throws<ArgumentNullException>(() => LayaEngine.OpenRouter(null!));
        Assert.Throws<ArgumentOutOfRangeException>(() => LayaEngine.Open("missing.gguf", new LayaConfig { Threads = -1 }));
        Assert.Throws<ArgumentOutOfRangeException>(() => LayaEngine.Open("missing.gguf", new LayaConfig { Threads = 257 }));
        Assert.Throws<ArgumentOutOfRangeException>(() => LayaEngine.OpenRouter("{}", new LayaConfig { Threads = 257 }));
        Assert.Throws<ArgumentOutOfRangeException>(() => LayaEngine.Open("missing.gguf", new LayaConfig { ContextTokens = 0 }));
        Assert.Throws<ArgumentOutOfRangeException>(() => LayaEngine.Open("missing.gguf", new LayaConfig { ContextTokens = -5 }));
        foreach (var bad in new[] { 0.0, -1.0, double.NaN, double.PositiveInfinity })
            Assert.Throws<ArgumentOutOfRangeException>(() => LayaEngine.Open("missing.gguf", new LayaConfig { Temperatures = new Dictionary<string, double> { ["choice"] = bad } }));
        // 256 is accepted by the binding; the missing file fails natively.
        Assert.Throws<LayaException>(() => LayaEngine.Open("missing.gguf", new LayaConfig { Threads = 256 }));
    }

    [Fact]
    public async Task AsyncSnapshotsOptions()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = LayaEngine.OpenRouter(Config(("unused", file)), One);
            var seen = new List<string>();
            var options = new LayaCallOptions { RequestId = 7, OnEvent = seen.Add };
            var task = engine.ExecuteAsync("{}", options);
            options.OnEvent = _ => throw new Exception("must not be used");
            options.RequestId = 8;
            options.Timeout = TimeSpan.FromDays(9);
            Assert.Equal(1, (await Assert.ThrowsAsync<LayaException>(() => task)).Status);
            Assert.Equal(2, seen.Count);
            Assert.All(seen, e => Assert.Contains("\"request_id\":7", e));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void ConfigSerializesDeterministically()
    {
        Assert.Equal("{}", new LayaConfig().ToJson());
        var config = new LayaConfig
        {
            Threads = 3,
            ContextTokens = 256,
            Temperatures = new Dictionary<string, double> { ["noul.2"] = 2, ["choice"] = 1.5, ["choice.3_5"] = 1.7 },
        };
        Assert.Equal("""{"threads":3,"context_tokens":256,"temperatures":{"choice":1.5,"choice.3_5":1.7,"noul.2":2}}""", config.ToJson());
        Assert.Equal("""{"threads":3,"context_tokens":256,"overflow":"scan"}""", new LayaConfig { Threads = 3, ContextTokens = 256, Overflow = "scan" }.ToJson());
    }

    [Fact]
    public void OverflowPolicyAppliesAndIsValidated()
    {
        if (Model is not { } path) return;
        var request = "{\"state\":\"" + string.Concat(Enumerable.Repeat("refund ", 800)) + "\",\"questions\":{\"refund\":{\"type\":\"noul\",\"instructions\":\"Is a refund requested?\"}}}";
        using (var engine = LayaEngine.Open(path, new LayaConfig { Threads = 2, Overflow = "truncate_end" }))
        {
            var result = engine.Execute(request);
            Assert.Matches("\"state_tokens_dropped\":[1-9]", result);
            var rejected = Assert.Throws<LayaException>(() => engine.Execute(request.Insert(1, "\"overflow\":\"reject\",")));
            Assert.Equal(1, rejected.Status);
        }
        Assert.Equal(1, Assert.Throws<LayaException>(() => LayaEngine.Open(path, new LayaConfig { Threads = 2, Overflow = "bogus" })).Status);
    }

    [Fact]
    public void NativeRejectsBadConfig()
    {
        if (Model is not { } path) return;
        // Below the native minimum of 32.
        Assert.Equal(1, Assert.Throws<LayaException>(() => LayaEngine.Open(path, new LayaConfig { Threads = 2, ContextTokens = 16 })).Status);
        // Not a valid temperature key.
        Assert.Equal(1, Assert.Throws<LayaException>(() => LayaEngine.Open(path, new LayaConfig { Threads = 2, Temperatures = new Dictionary<string, double> { ["bogus"] = 1.5 } })).Status);
    }

    [Fact]
    public void RuntimeDiagnostics()
    {
        Assert.Contains("\"abi_version\":2", LayaEngine.RuntimeInfo());
        if (Model is { } model)
        {
            using var engine = LayaEngine.Open(model, Two);
            var info = engine.Info();
            Assert.Contains("\"decision_type\":\"laya\"", info);
            Assert.Contains("\"context_tokens\":", info);
            Assert.Contains("\"trained_context_tokens\":", info);
            Assert.DoesNotContain("\"max_tokens\":", info);
            using var custom = LayaEngine.Open(model, new LayaConfig { Threads = 2, ContextTokens = 128 });
            Assert.Contains("\"context_tokens\":128", custom.Info());
            Assert.Contains("\"default_context_tokens\":", LayaEngine.RuntimeInfo());
        }
    }

    [Fact]
    public void ObserverExceptionsAreContained()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = LayaEngine.OpenRouter(Config(("unused", file)), One);
            var calls = 0;
            var options = new LayaCallOptions { RequestId = 1, OnEvent = _ => { calls++; throw new InvalidOperationException("boom"); } };
            Assert.Equal(1, Assert.Throws<LayaException>(() => engine.Execute("{}", options)).Status);
            Assert.Equal(2, calls);
            var nested = false;
            var closing = new LayaCallOptions
            {
                OnEvent = _ => { engine.Dispose(); nested = Assert.Throws<ObjectDisposedException>(() => engine.Info()) != null; }
            };
            Assert.Equal(1, Assert.Throws<LayaException>(() => engine.Execute("{}", closing)).Status);
            Assert.True(nested);
            Assert.Throws<ObjectDisposedException>(() => engine.Execute("{}"));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void DisposeIsIdempotentAndCallsThrow()
    {
        var file = Path.GetTempFileName();
        try
        {
            var engine = LayaEngine.OpenRouter(Config(("unused", file)), One);
            engine.Dispose();
            engine.Dispose();
            Assert.Throws<ObjectDisposedException>(() => engine.Execute("{}"));
            Assert.Throws<ObjectDisposedException>(() => engine.Info());
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void InvalidTimeoutIsRejected()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = LayaEngine.OpenRouter(Config(("unused", file)), One);
            Assert.Throws<ArgumentOutOfRangeException>(() => engine.Execute("{}", new LayaCallOptions { Timeout = TimeSpan.FromDays(2) }));
            Assert.Throws<ArgumentOutOfRangeException>(() => engine.Execute("{}", new LayaCallOptions { Timeout = TimeSpan.FromSeconds(-1) }));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void DisposeInsideCallback()
    {
        if (Model is not { } path) return;
        var engine = LayaEngine.Open(path, Two);
        var baseline = engine.Execute(Primitive);
        // The running call finishes; the engine is released when it returns.
        Assert.Equal(baseline, engine.Execute(Primitive, new LayaCallOptions { OnEvent = _ => engine.Dispose() }));
        Assert.Throws<ObjectDisposedException>(() => engine.Execute(Primitive));
        engine.Dispose();
    }

    [Fact]
    public void RealFeatures()
    {
        if (Model is not { } path) return;
        using var engine = LayaEngine.Open(path, Two);
        var tagged = WithKey(Primitive, "metadata", "caller tag");
        Assert.Equal(1, Assert.Throws<LayaException>(() => engine.Execute(tagged)).Status);

        var single = engine.Execute(Schema);
        Assert.Contains("\"values\":{\"refund\":true}", single);
        var events = new List<string>();
        var result = engine.Execute($"[{Schema},{Schema}]", new LayaCallOptions { RequestId = 21, OnEvent = events.Add });
        Assert.Equal($"[{single},{single}]", result);
        Assert.Equal(2, events.Count);
        Assert.Contains("\"event\":\"start\"", events[0]);
        Assert.Contains("\"request_id\":21", events[1]);

        Assert.Throws<LayaException>(() => engine.Execute("{}", new LayaCallOptions { RequestId = 22, OnEvent = _ => throw new Exception("contained") }));
        // Reentry on the same engine from an observer.
        engine.Execute(Schema, new LayaCallOptions { RequestId = 23, OnEvent = _ => engine.Execute(Schema) });

        // Per-request settings pass straight through; answers carry both confidence fields.
        var limited = engine.Execute(WithRaw(Primitive, "\"max_tokens\":64,\"temperatures\":{\"noul\":1.5}"));
        Assert.Contains("\"answer_confidence\":", limited);
        Assert.Contains("\"confidence\":", limited);
        Assert.Equal(1, Assert.Throws<LayaException>(() => engine.Execute(WithRaw(Primitive, "\"max_tokens\":0"))).Status);
        Assert.Equal(1, Assert.Throws<LayaException>(() => engine.Execute(WithRaw(Primitive, "\"max_tokens\":100000"))).Status);
        Assert.Equal(1, Assert.Throws<LayaException>(() => engine.Execute(WithRaw(Primitive, "\"temperatures\":{\"noul\":-1}"))).Status);

        using var router = LayaEngine.OpenRouter(Config(("english", path)), Two);
        Assert.Contains("\"model\":\"english\"", router.Execute(WithKey(Schema, "model", "english")));
        Assert.Equal(1, Assert.Throws<LayaException>(() => router.Execute(Schema)).Status);
    }

    [Fact]
    public async Task CancellationAndDeadline()
    {
        if (Model is not { } path) return;
        using var engine = LayaEngine.Open(path, Two);
        var baseline = engine.Execute(Primitive);
        using var cancelled = new CancellationTokenSource();
        cancelled.Cancel();
        var sync = Assert.Throws<OperationCanceledException>(() => engine.Execute(Primitive, null, cancelled.Token));
        Assert.Equal(5, Assert.IsType<LayaException>(sync.InnerException).Status);
        Assert.Equal(cancelled.Token, sync.CancellationToken);
        var task = engine.ExecuteAsync(Primitive, null, cancelled.Token);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => task);
        Assert.True(task.IsCanceled);
        // Cancelled while running (the token is only cancelled by the first event).
        using var mid = new CancellationTokenSource();
        var running = engine.ExecuteAsync(Primitive, new LayaCallOptions { OnEvent = _ => mid.Cancel() }, mid.Token);
        try { await running; } catch (OperationCanceledException) { Assert.True(running.IsCanceled); }
        var timeout = new LayaCallOptions { Timeout = TimeSpan.FromMilliseconds(1) };
        Assert.Equal(6, Assert.Throws<LayaException>(() => engine.Execute(Primitive, timeout)).Status);
        Assert.Equal(baseline, engine.Execute(Primitive));
        using var live = new CancellationTokenSource();
        Assert.Equal(baseline, await engine.ExecuteAsync(Primitive, null, live.Token));
    }

    [Fact]
    public async Task SharedAcrossThreads()
    {
        if (Model is not { } path) return;
        var engine = LayaEngine.Open(path, Two);
        var baseline = engine.Execute(Primitive);
        // Two names for one file with one slot: concurrent calls evict each other.
        var router = LayaEngine.OpenRouter(Config(("a", path), ("b", path)), Two);
        using var cts = new CancellationTokenSource();
        var plain = Enumerable.Range(0, 3).Select(_ => Task.Run(() => engine.Execute(Primitive))).ToArray();
        var routed = new[] { "a", "b" }.Select(name => Task.Run(() => (name, router.Execute(WithKey(Primitive, "model", name))))).ToArray();
        var cancelled = Task.Run(() =>
        {
            try { return engine.Execute(Primitive, null, cts.Token); }
            catch (OperationCanceledException e) { Assert.Equal(5, Assert.IsType<LayaException>(e.InnerException).Status); return baseline; }
        });
        cts.Cancel();
        foreach (var task in plain) Assert.Equal(baseline, await task);
        foreach (var task in routed)
        {
            var (name, result) = await task;
            Assert.Equal($"{baseline[..^1]},\"model\":\"{name}\"}}", result);
        }
        Assert.Equal(baseline, await cancelled);
        // Dispose from another thread waits for nothing in flight and is safe to repeat.
        await Task.Run(() => { engine.Dispose(); router.Dispose(); engine.Dispose(); });
    }

    [Fact]
    public void RouterRouteConfig()
    {
        if (Model is not { } path) return;
        var q = Quote(path);
        var json = "{\"models\":{\"small\":{\"path\":\"" + q + "\",\"config\":{\"context_tokens\":64}},\"plain\":\"" + q + "\"},\"max_loaded\":1}";
        using var router = LayaEngine.OpenRouter(json, new LayaConfig { Threads = 2, ContextTokens = 256 });
        // The route's own context_tokens (64) overrides the open config (256), so 100 tokens exceed it.
        var request = WithRaw(Primitive, "\"max_tokens\":100");
        Assert.Equal(1, Assert.Throws<LayaException>(() => router.Execute(WithKey(request, "model", "small"))).Status);
        Assert.Contains("\"model\":\"plain\"", router.Execute(WithKey(request, "model", "plain")));
        Assert.Contains("\"model\":\"small\"", router.Execute(WithKey(WithRaw(Primitive, "\"max_tokens\":60"), "model", "small")));
    }
}
