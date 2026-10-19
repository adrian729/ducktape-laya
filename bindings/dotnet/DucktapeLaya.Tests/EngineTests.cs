using Ducktape.Laya;
using Xunit;

namespace DucktapeLaya.Tests;

public class EngineTests
{
    const string Primitive = """{"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}""";
    const string Schema = """{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}""";

    static string? Model => Environment.GetEnvironmentVariable("LAYA_MODEL") is { Length: > 0 } m ? m : null;
    static string Quote(string path) => path.Replace("\\", "\\\\").Replace("\"", "\\\"");
    static string Config(params (string Name, string Path)[] models) =>
        "{\"models\":{" + string.Join(",", models.Select(m => $"\"{m.Name}\":\"{Quote(m.Path)}\"")) + "},\"max_loaded\":1}";
    static string WithKey(string json, string key, string value) => "{" + $"\"{key}\":\"{value}\"," + json[1..];

    [Fact]
    public void RejectsBadPath() => Assert.Throws<LayaException>(() => LayaEngine.Open("missing.gguf", 2));

    [Fact]
    public void RejectsNul()
    {
        Assert.Equal(1, Assert.Throws<LayaException>(() => LayaEngine.Open("a\0b", 2)).Status);
        Assert.Equal(LayaStatus.InvalidArgument, Assert.Throws<LayaException>(() => LayaEngine.OpenRouter("{\0}")).Code);
    }

    [Fact]
    public void ValidatesArguments()
    {
        Assert.Throws<ArgumentNullException>(() => LayaEngine.Open(null!));
        Assert.Throws<ArgumentNullException>(() => LayaEngine.OpenRouter(null!));
        Assert.Throws<ArgumentOutOfRangeException>(() => LayaEngine.Open("missing.gguf", -1));
        Assert.Throws<ArgumentOutOfRangeException>(() => LayaEngine.Open("missing.gguf", 257));
        Assert.Throws<ArgumentOutOfRangeException>(() => LayaEngine.OpenRouter("{}", 257));
        // 256 is accepted by the binding; the missing file fails natively.
        Assert.Throws<LayaException>(() => LayaEngine.Open("missing.gguf", 256));
    }

    [Fact]
    public async Task AsyncSnapshotsOptions()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = LayaEngine.OpenRouter(Config(("unused", file)), 1);
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
    public void RuntimeDiagnostics()
    {
        Assert.Contains("\"abi_version\":1", LayaEngine.RuntimeInfo());
        if (Model is { } model)
        {
            using var engine = LayaEngine.Open(model, 2);
            Assert.Contains("\"decision_type\":\"laya\"", engine.Info());
        }
    }

    [Fact]
    public void ObserverExceptionsAreContained()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = LayaEngine.OpenRouter(Config(("unused", file)), 1);
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
            var engine = LayaEngine.OpenRouter(Config(("unused", file)), 1);
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
            using var engine = LayaEngine.OpenRouter(Config(("unused", file)), 1);
            Assert.Throws<ArgumentOutOfRangeException>(() => engine.Execute("{}", new LayaCallOptions { Timeout = TimeSpan.FromDays(2) }));
            Assert.Throws<ArgumentOutOfRangeException>(() => engine.Execute("{}", new LayaCallOptions { Timeout = TimeSpan.FromSeconds(-1) }));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void DisposeInsideCallback()
    {
        if (Model is not { } path) return;
        var engine = LayaEngine.Open(path, 2);
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
        using var engine = LayaEngine.Open(path, 2);
        var tagged = WithKey(Primitive, "metadata", "caller tag");
        Assert.Equal(engine.Decide(Primitive), engine.Decide(tagged));
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

        using var router = LayaEngine.OpenRouter(Config(("english", path)), 2);
        Assert.Contains("\"model\":\"english\"", router.Execute(WithKey(Schema, "model", "english")));
        Assert.Equal(1, Assert.Throws<LayaException>(() => router.Execute(Schema)).Status);
    }

    [Fact]
    public async Task CancellationAndDeadline()
    {
        if (Model is not { } path) return;
        using var engine = LayaEngine.Open(path, 2);
        var baseline = engine.Execute(Primitive);
        using var cancelled = new CancellationTokenSource();
        cancelled.Cancel();
        var sync = Assert.Throws<OperationCanceledException>(() => engine.Execute(Primitive, null, cancelled.Token));
        Assert.Equal(5, Assert.IsType<LayaException>(sync.InnerException).Status);
        Assert.Equal(cancelled.Token, sync.CancellationToken);
        Assert.Throws<OperationCanceledException>(() => engine.Decide(Primitive, null, cancelled.Token));
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
        var engine = LayaEngine.Open(path, 2);
        var baseline = engine.Execute(Primitive);
        // Two names for one file with one slot: concurrent calls evict each other.
        var router = LayaEngine.OpenRouter(Config(("a", path), ("b", path)), 2);
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
}
