using Ducktape.Laya;
using Xunit;

namespace DucktapeLaya.Tests;

public class EngineTests
{
    const string Primitive = """{"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}""";
    const string Schema = """{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}""";

    static readonly Config Two = new() { Threads = 2 };
    static readonly Config One = new() { Threads = 1 };
    static string? Model => Environment.GetEnvironmentVariable("LAYA_MODEL") is { Length: > 0 } m ? m : null;
    static string Quote(string path) => path.Replace("\\", "\\\\").Replace("\"", "\\\"");
    static string RouterJson(params (string Name, string Path)[] models) =>
        "{\"models\":{" + string.Join(",", models.Select(m => $"\"{m.Name}\":\"{Quote(m.Path)}\"")) + "},\"max_loaded\":1}";
    static string WithRaw(string json, string members) => "{" + members + "," + json[1..];
    static string WithKey(string json, string key, string value) => "{" + $"\"{key}\":\"{value}\"," + json[1..];

    [Fact]
    public void MissingExplicitLibraryDoesNotFallBack()
    {
        // The real library is loadable by name (DUCKTAPE_LAYA_LIB_DIR in the tests), yet an explicit
        // path to a missing file must not resolve to it through the loader's search path.
        var missing = Path.Combine(Path.GetTempPath(), "ducktape-laya-missing", Native.FileName);
        var error = Assert.Throws<LayaLoadException>(() => Native.LoadFile(missing, "The bundled library", Native.LoadHint()));
        Assert.Equal(Status.UnsupportedPlatform, error.Status);
        Assert.Contains("does not exist", error.Message);
        Assert.IsType<DllNotFoundException>(error.InnerException);
        var real = Environment.GetEnvironmentVariable("DUCKTAPE_LAYA_LIB_DIR");
        if (!string.IsNullOrEmpty(real)) Assert.NotEqual(IntPtr.Zero, Native.LoadFile(Path.Combine(real, Native.FileName), "test", null));
    }

    [Fact]
    public void MacOSRefusesAnImageOtherThanTheRequestedFile()
    {
        // macOS only: dyld may substitute a library of the same leaf name from DYLD_LIBRARY_PATH.
        var real = Environment.GetEnvironmentVariable("DUCKTAPE_LAYA_LIB_DIR");
        if (!OperatingSystem.IsMacOS() || string.IsNullOrEmpty(real)) return;
        var file = Path.GetFullPath(Path.Combine(real, Native.FileName));
        var handle = System.Runtime.InteropServices.NativeLibrary.Load(file);
        Assert.True(System.Runtime.InteropServices.NativeLibrary.TryGetExport(handle, "dtl_abi_version", out var abi));
        Native.RequireImage(abi, file);
        var other = Path.Combine(Path.GetTempPath(), "ducktape-laya-other", Native.FileName);
        var error = Assert.Throws<LayaLoadException>(() => Native.RequireImage(abi, other));
        Assert.Equal(Status.UnsupportedPlatform, error.Status);
        Assert.Contains("DYLD_LIBRARY_PATH", error.Message);
        Assert.Contains(other, error.Message);
    }

    [Fact]
    public void UnloadableLibraryIsReportedAsFoundWithTheOsReason()
    {
        var dir = Path.Combine(Path.GetTempPath(), "ducktape-laya-broken-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        try
        {
            // Not the library's file name: macOS dyld would find a library of that name on DYLD_LIBRARY_PATH
            // (which the macOS CI job sets) instead of loading this file.
            var path = Path.Combine(dir, "broken-" + Native.FileName);
            File.WriteAllBytes(path, "not a library"u8.ToArray());
            var error = Assert.Throws<LayaLoadException>(() => Native.LoadFile(path, "The bundled library", Native.LoadHint()));
            Assert.Equal(Status.UnsupportedPlatform, error.Status);
            Assert.Contains($"The bundled library {path} was found but could not be loaded: ", error.Message);
            Assert.EndsWith($"({Native.LoadHint()})", error.Message);
            Assert.DoesNotContain("not found", error.Message.Split(": ")[0]);
            Assert.NotNull(error.InnerException);
            Assert.Contains(error.InnerException!.Message.Trim(), error.Message);
        }
        finally { Directory.Delete(dir, true); }
    }

    [Fact]
    public void MuslIsDetectedFromTheDynamicLoaders()
    {
        var root = Path.Combine(Path.GetTempPath(), "ducktape-laya-musl-" + Guid.NewGuid().ToString("N"));
        string Dir(string name, params string[] files)
        {
            var dir = Directory.CreateDirectory(Path.Combine(root, name)).FullName;
            foreach (var file in files) File.WriteAllBytes(Path.Combine(dir, file), []);
            return dir;
        }
        try
        {
            Assert.True(Native.IsMusl(Dir("alpine", "ld-musl-x86_64.so.1", "libc.musl-x86_64.so.1")));
            Assert.False(Native.IsMusl(Dir("gcompat", "ld-musl-aarch64.so.1", "ld-linux-aarch64.so.1")));
            Assert.False(Native.IsMusl(Dir("glibc", "ld-linux-x86-64.so.2")));
            Assert.False(Native.IsMusl(Path.Combine(root, "absent")));
            // This host is not musl (the suite runs on glibc, macOS and Windows).
            Assert.False(Native.IsMusl());
            var error = Native.MuslUnsupported();
            Assert.Equal(Status.UnsupportedPlatform, error.Status);
            Assert.Contains("needs glibc, and this looks like a musl system", error.Message);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void BundledLibraryIsFoundBeforeTheSystemLoader()
    {
        var rid = Native.RuntimeId();
        Assert.NotNull(rid);
        var root = Path.Combine(Path.GetTempPath(), "ducktape-laya-bundled-" + Guid.NewGuid().ToString("N"));
        try
        {
            Assert.Null(Native.BundledPath(root));
            var nuget = Path.Combine(root, "runtimes", rid!, "native");
            Directory.CreateDirectory(nuget);
            File.WriteAllBytes(Path.Combine(root, Native.FileName), new byte[1]);
            Assert.Equal(Path.Combine(root, Native.FileName), Native.BundledPath(root));
            File.WriteAllBytes(Path.Combine(nuget, Native.FileName), new byte[1]);
            Assert.Equal(Path.Combine(nuget, Native.FileName), Native.BundledPath(root));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void RejectsBadPath() => Assert.Throws<LayaException>(() => Engine.Open("missing.gguf", Two));

    [Fact]
    public void RejectsInvalidUnicode()
    {
        const string lone = "a\ud800b";
        var e = Assert.Throws<LayaArgumentException>(() => Engine.Open(lone, Two));
        Assert.Equal(Status.InvalidArgument, e.Status);
        Assert.Contains("model path is not valid Unicode", e.Message);
        Assert.Contains("router config is not valid Unicode", Assert.Throws<LayaArgumentException>(() => Engine.OpenRouter("{\"models\":{\"x\":\"" + lone + "\"}}")).Message);
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaArgumentException>(() => Engine.Open("x", new Config { Temperatures = new Dictionary<string, double> { [lone] = 1 } })).Status);
        var file = Path.GetTempFileName();
        try
        {
            using var router = Engine.OpenRouter("{\"models\":{\"x\":" + System.Text.Json.JsonSerializer.Serialize(file) + "}}");
            var bad = Assert.Throws<LayaArgumentException>(() => router.Execute("{\"state\":\"" + lone + "\"}"));
            Assert.Equal(Status.InvalidArgument, bad.Status);
            Assert.Equal("request is not valid Unicode", bad.Message);
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void RejectsNul()
    {
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaArgumentException>(() => Engine.Open("a\0b", Two)).Status);
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaArgumentException>(() => Engine.OpenRouter("{\0}")).Status);
    }

    [Fact]
    public void ValidatesArguments()
    {
        Assert.Throws<ArgumentNullException>(() => Engine.Open(null!));
        Assert.Throws<ArgumentNullException>(() => Engine.OpenRouter((string)null!));
        foreach (var bad in new[] { double.NaN, double.PositiveInfinity })
            Assert.Throws<LayaArgumentException>(() => Engine.Open("missing.gguf", new Config { Temperatures = new Dictionary<string, double> { ["choice"] = bad } }));
        // Config values go to the core unchecked: it rejects invalid ones with status 1 and a valid one reaches the missing file (status 2).
        Assert.Equal(Status.ModelError, Assert.Throws<LayaException>(() => Engine.Open("missing.gguf", new Config { Threads = 256 })).Status);
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => Engine.Open("missing.gguf", new Config { ContextTokens = 0 })).Status);
    }

    [Fact]
    public async Task AsyncSnapshotsOptions()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = Engine.OpenRouter(RouterJson(("unused", file)), One);
            var seen = new List<string>();
            var options = new CallOptions { RequestId = 7, OnEvent = seen.Add };
            var task = engine.ExecuteAsync("{}", options);
            options.OnEvent = _ => throw new Exception("must not be used");
            options.RequestId = 8;
            options.Timeout = TimeSpan.FromDays(9);
            Assert.Equal(Status.InvalidArgument, (await Assert.ThrowsAsync<LayaException>(() => task)).Status);
            Assert.Equal(2, seen.Count);
            Assert.All(seen, e => Assert.Contains("\"request_id\":7", e));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void ConfigSerializesDeterministically()
    {
        Assert.Equal("{}", new Config().ToJson());
        var config = new Config
        {
            Threads = 3,
            ContextTokens = 256,
            Temperatures = new Dictionary<string, double> { ["noul.2"] = 2, ["choice"] = 1.5, ["choice.3_5"] = 1.7 },
        };
        Assert.Equal("""{"threads":3,"context_tokens":256,"temperatures":{"choice":1.5,"choice.3_5":1.7,"noul.2":2}}""", config.ToJson());
        Assert.Equal("""{"threads":3,"context_tokens":256,"overflow":"scan","max_windows_per_call":64}""",
            new Config { Threads = 3, ContextTokens = 256, Overflow = "scan", MaxWindowsPerCall = 64 }.ToJson());
        Assert.Equal("""{"threads":0,"max_windows_per_call":0}""", new Config { Threads = 0, MaxWindowsPerCall = 0 }.ToJson());
    }

    [Fact]
    public void OverflowPolicyReachesNativeAndIsValidated()
    {
        if (Model is not { } path) return;
        var request = "{\"state\":\"" + string.Concat(Enumerable.Repeat("refund ", 800)) + "\",\"questions\":{\"refund\":{\"type\":\"noul\",\"instructions\":\"Is a refund requested?\"}}}";
        // The policy reaches the engine: the default (reject) would refuse this request.
        using (var engine = Engine.Open(path, new Config { Threads = 2, Overflow = "truncate_end" }))
            Assert.Contains("\"answers\"", engine.Execute(request));
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => Engine.Open(path, new Config { Threads = 2, Overflow = "bogus" })).Status);
    }

    [Fact]
    public void NativeRejectsBadConfig()
    {
        if (Model is not { } path) return;
        // Below the native minimum of 32.
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => Engine.Open(path, new Config { Threads = 2, ContextTokens = 16 })).Status);
        // Not a valid temperature key.
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => Engine.Open(path, new Config { Threads = 2, Temperatures = new Dictionary<string, double> { ["bogus"] = 1.5 } })).Status);
    }

    [Fact]
    public void RuntimeDiagnostics()
    {
        Assert.Contains("\"abi_version\":2", Engine.RuntimeInfo());
        if (Model is { } model)
        {
            using var engine = Engine.Open(model, Two);
            var info = engine.Info();
            Assert.Contains("\"decision_type\":\"laya\"", info);
            Assert.Contains("\"context_tokens\":", info);
            Assert.Contains("\"trained_context_tokens\":", info);
            Assert.DoesNotContain("\"max_tokens\":", info);
            using var custom = Engine.Open(model, new Config { Threads = 2, ContextTokens = 128 });
            Assert.Contains("\"context_tokens\":128", custom.Info());
            Assert.Contains("\"default_context_tokens\":", Engine.RuntimeInfo());
        }
    }

    [Fact]
    public void ObserverExceptionsAreContained()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = Engine.OpenRouter(RouterJson(("unused", file)), One);
            var calls = 0;
            var options = new CallOptions { RequestId = 1, OnEvent = _ => { calls++; throw new InvalidOperationException("boom"); } };
            Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => engine.Execute("{}", options)).Status);
            Assert.Equal(2, calls);
            var nested = false;
            var closing = new CallOptions
            {
                OnEvent = _ => { engine.Dispose(); nested = Assert.Throws<EngineClosedException>(() => engine.Info()) != null; }
            };
            Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => engine.Execute("{}", closing)).Status);
            Assert.True(nested);
            Assert.Throws<EngineClosedException>(() => engine.Execute("{}"));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void DisposeIsIdempotentAndCallsThrow()
    {
        var file = Path.GetTempFileName();
        try
        {
            var engine = Engine.OpenRouter(RouterJson(("unused", file)), One);
            engine.Dispose();
            engine.Dispose();
            Assert.Throws<EngineClosedException>(() => engine.Execute("{}"));
            Assert.Equal(Status.InvalidArgument, Assert.Throws<EngineClosedException>(() => engine.Info()).Status);
            Assert.IsAssignableFrom<ObjectDisposedException>(Assert.Throws<EngineClosedException>(() => engine.Execute("{}")));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void CheckVersionComparesThePackageVersion()
    {
        Native.CheckVersion("""{"abi_version":2,"package_version":"0.3.0"}""", "0.3.0");
        var error = Assert.Throws<LayaLoadException>(() => Native.CheckVersion("""{"package_version":"0.2.0"}""", "0.3.0"));
        Assert.Equal(Status.UnsupportedPlatform, error.Status);
        Assert.Contains("0.2.0", error.Message);
        Assert.Throws<LayaLoadException>(() => Native.CheckVersion("{}", "0.3.0"));
        Assert.Throws<LayaLoadException>(() => Native.CheckVersion("not json", "0.3.0"));
        Assert.Matches(@"^\d+\.\d+\.\d+", Native.PackageVersion());
    }

    [Fact]
    public void OverridesMustBeAbsolute()
    {
        Assert.Null(Native.OverridePath(null, null));
        Assert.Null(Native.OverridePath("", ""));
        var abs = Path.GetFullPath(Path.GetTempPath());
        Assert.Equal(Path.Combine(abs, "x"), Native.OverridePath(Path.Combine(abs, "x"), "ignored"));
        Assert.Equal(Path.Combine(abs, Native.FileName), Native.OverridePath(null, abs));
        Assert.Throws<LayaLoadException>(() => Native.OverridePath("rel/lib.so", null));
        Assert.Throws<LayaLoadException>(() => Native.OverridePath(null, "rel"));
    }

    [Fact]
    public void RouterConfigSerializesDeterministically()
    {
        var router = new RouterConfig
        {
            Models = new Dictionary<string, Route>
            {
                ["b"] = new Route { Path = "/m/b.gguf", Config = new Config { ContextTokens = 64 } },
                ["a"] = new Route { Path = "/m/a.gguf" },
            },
            MaxLoaded = 2,
            Auto = new AutoRoutes { English = "a", Default = "b" },
        };
        Assert.Equal("""{"models":{"a":{"path":"/m/a.gguf"},"b":{"path":"/m/b.gguf","config":{"context_tokens":64}}},"max_loaded":2,"auto":{"english":"a","default":"b"}}""",
            router.ToJson());
        Assert.Equal("""{"models":{}}""", new RouterConfig { Models = new Dictionary<string, Route>() }.ToJson());
    }

    [Fact]
    public void ErrorOrder()
    {
        var file = Path.GetTempFileName();
        try
        {
            var engine = Engine.OpenRouter(new RouterConfig { Models = new Dictionary<string, Route> { ["unused"] = new Route { Path = file } } }, One);
            var events = new List<string>();
            var options = new CallOptions { OnEvent = events.Add, Timeout = TimeSpan.FromDays(2) };
            using var cancelled = new CancellationTokenSource();
            cancelled.Cancel();
            // A bad argument beats an already-cancelled token; the cancelled token then fails at once with no native call.
            Assert.Throws<ArgumentOutOfRangeException>(() => engine.Execute("{}", options, cancelled.Token));
            Assert.Throws<LayaArgumentException>(() => engine.Execute("a\0b", null, cancelled.Token));
            options.Timeout = null;
            var oce = Assert.Throws<OperationCanceledException>(() => engine.Execute("{}", options, cancelled.Token));
            Assert.Equal(cancelled.Token, oce.CancellationToken);
            Assert.Empty(events);
            // Closed beats everything.
            engine.Dispose();
            options.Timeout = TimeSpan.FromDays(2);
            Assert.Throws<EngineClosedException>(() => engine.Execute("a\0b", options, cancelled.Token));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public async Task PendingDisposeFailsNewCallsFast()
    {
        var file = Path.GetTempFileName();
        try
        {
            var engine = Engine.OpenRouter(RouterJson(("unused", file)), One);
            Task? closer = null;
            var failedFast = false;
            var options = new CallOptions
            {
                OnEvent = _ =>
                {
                    if (closer is not null) return;
                    // Dispose from another thread blocks until this call finishes; calls made from other
                    // threads meanwhile must fail instead of queueing behind it (this callback waits for them).
                    closer = Task.Run(engine.Dispose);
                    var deadline = DateTime.UtcNow.AddSeconds(30);
                    while (!failedFast && DateTime.UtcNow < deadline)
                    {
                        var probe = Task.Run(() =>
                        {
                            try { engine.Execute("{}"); return false; }
                            catch (ObjectDisposedException) { return true; }
                            catch (LayaException) { return false; }
                        });
#pragma warning disable xUnit1031 // the callback must block on the probe
                        Assert.True(probe.Wait(TimeSpan.FromSeconds(10)), "a new call blocked behind the pending Dispose");
                        failedFast = probe.Result;
#pragma warning restore xUnit1031
                    }
                }
            };
            Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => engine.Execute("{}", options)).Status);
            Assert.True(failedFast);
            await closer!.WaitAsync(TimeSpan.FromSeconds(10));
            Assert.Throws<EngineClosedException>(() => engine.Execute("{}"));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void ConfigRejectsInvalidUnicode()
    {
        const string lone = "a\ud800b";
        foreach (var config in new[]
        {
            new Config { Overflow = lone },
            new Config { Temperatures = new Dictionary<string, double> { [lone] = 1 } },
        })
        {
            var e = Assert.Throws<LayaArgumentException>(() => Engine.Open("missing.gguf", config));
            Assert.Equal(Status.InvalidArgument, e.Status);
            Assert.Equal("config is not valid Unicode", e.Message);
            Assert.Equal("config is not valid Unicode", Assert.Throws<LayaArgumentException>(() => Engine.OpenRouter("{}", config)).Message);
        }
    }

    [Fact]
    public void RouterAutoConfigIsValidated()
    {
        var file = Path.GetTempFileName();
        try
        {
            var models = "\"models\":{\"a\":" + System.Text.Json.JsonSerializer.Serialize(file) + "}";
            foreach (var (auto, text) in new[]
            {
                ("{\"english\":\"a\"}", "auto.default must name a route"),
                ("{\"english\":\"zz\",\"default\":\"a\"}", "auto.english must name a route"),
                ("[]", "auto must be an object"),
                ("{\"english\":\"a\",\"default\":\"a\",\"x\":\"a\"}", "auto: unsupported key x"),
            })
            {
                var e = Assert.Throws<LayaException>(() => Engine.OpenRouter("{" + models + ",\"auto\":" + auto + "}", One));
                Assert.Equal(Status.InvalidArgument, e.Status);
                Assert.Contains(text, e.Message);
            }
            using var plain = Engine.OpenRouter("{" + models + "}", One);
            var noAuto = Assert.Throws<LayaException>(() => plain.Execute(WithKey(Primitive, "model", "auto")));
            Assert.Equal(Status.InvalidArgument, noAuto.Status);
            Assert.Contains("requires an \"auto\" router config", noAuto.Message);
            var lang = Assert.Throws<LayaException>(() => plain.Execute(WithRaw(WithKey(Primitive, "model", "a"), "\"lang\":\"en\"")));
            Assert.Equal(Status.InvalidArgument, lang.Status);
            Assert.Contains("lang", lang.Message);
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void InvalidTimeoutIsRejected()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = Engine.OpenRouter(RouterJson(("unused", file)), One);
            Assert.Throws<ArgumentOutOfRangeException>(() => engine.Execute("{}", new CallOptions { Timeout = TimeSpan.FromDays(2) }));
            Assert.Throws<ArgumentOutOfRangeException>(() => engine.Execute("{}", new CallOptions { Timeout = TimeSpan.FromSeconds(-1) }));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void DisposeInsideCallback()
    {
        if (Model is not { } path) return;
        var engine = Engine.Open(path, Two);
        var baseline = engine.Execute(Primitive);
        // The running call finishes; the engine is released when it returns.
        Assert.Equal(baseline, engine.Execute(Primitive, new CallOptions { OnEvent = _ => engine.Dispose() }));
        Assert.Throws<EngineClosedException>(() => engine.Execute(Primitive));
        engine.Dispose();
    }

    [Fact]
    public void RealFeatures()
    {
        if (Model is not { } path) return;
        using var engine = Engine.Open(path, Two);
        var tagged = WithKey(Primitive, "metadata", "caller tag");
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => engine.Execute(tagged)).Status);

        var single = engine.Execute(Schema);
        Assert.Contains("\"values\":{\"refund\":true}", single);
        var events = new List<string>();
        var result = engine.Execute($"[{Schema},{Schema}]", new CallOptions { RequestId = 21, OnEvent = events.Add });
        Assert.Equal($"[{single},{single}]", result);
        Assert.Equal(2, events.Count);
        Assert.Contains("\"event\":\"start\"", events[0]);
        Assert.Contains("\"request_id\":21", events[1]);

        Assert.Throws<LayaException>(() => engine.Execute("{}", new CallOptions { RequestId = 22, OnEvent = _ => throw new Exception("contained") }));
        // Reentry on the same engine from an observer.
        engine.Execute(Schema, new CallOptions { RequestId = 23, OnEvent = _ => engine.Execute(Schema) });

        // Per-request settings pass straight through; answers carry both confidence fields.
        var limited = engine.Execute(WithRaw(Primitive, "\"max_tokens\":64,\"temperatures\":{\"noul\":1.5}"));
        Assert.Contains("\"answer_confidence\":", limited);
        Assert.Contains("\"confidence\":", limited);
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => engine.Execute(WithRaw(Primitive, "\"max_tokens\":0"))).Status);
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => engine.Execute(WithRaw(Primitive, "\"max_tokens\":100000"))).Status);
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => engine.Execute(WithRaw(Primitive, "\"temperatures\":{\"noul\":-1}"))).Status);

        using var router = Engine.OpenRouter(RouterJson(("english", path)), Two);
        Assert.Contains("\"model\":\"english\"", router.Execute(WithKey(Schema, "model", "english")));
        Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => router.Execute(Schema)).Status);
    }

    [Fact]
    public async Task CancellationAndDeadline()
    {
        if (Model is not { } path) return;
        using var engine = Engine.Open(path, Two);
        var baseline = engine.Execute(Primitive);
        using var cancelled = new CancellationTokenSource();
        cancelled.Cancel();
        var sync = Assert.Throws<OperationCanceledException>(() => engine.Execute(Primitive, null, cancelled.Token));
        Assert.Equal(Status.Cancelled, Assert.IsType<LayaException>(sync.InnerException).Status);
        Assert.Equal(cancelled.Token, sync.CancellationToken);
        var task = engine.ExecuteAsync(Primitive, null, cancelled.Token);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => task);
        Assert.True(task.IsCanceled);
        // Cancelled while running (the token is only cancelled by the first event).
        using var mid = new CancellationTokenSource();
        var running = engine.ExecuteAsync(Primitive, new CallOptions { OnEvent = _ => mid.Cancel() }, mid.Token);
        try { await running; } catch (OperationCanceledException) { Assert.True(running.IsCanceled); }
        var timeout = new CallOptions { Timeout = TimeSpan.FromMilliseconds(1) };
        Assert.Equal(Status.DeadlineExceeded, Assert.Throws<LayaException>(() => engine.Execute(Primitive, timeout)).Status);
        Assert.Equal(baseline, engine.Execute(Primitive));
        using var live = new CancellationTokenSource();
        Assert.Equal(baseline, await engine.ExecuteAsync(Primitive, null, live.Token));
    }

    [Fact]
    public async Task SharedAcrossThreads()
    {
        if (Model is not { } path) return;
        var engine = Engine.Open(path, Two);
        var baseline = engine.Execute(Primitive);
        // Two names for one file with one slot: concurrent calls evict each other.
        var router = Engine.OpenRouter(RouterJson(("a", path), ("b", path)), Two);
        using var cts = new CancellationTokenSource();
        var plain = Enumerable.Range(0, 3).Select(_ => Task.Run(() => engine.Execute(Primitive))).ToArray();
        var routed = new[] { "a", "b" }.Select(name => Task.Run(() => (name, router.Execute(WithKey(Primitive, "model", name))))).ToArray();
        var cancelled = Task.Run(() =>
        {
            try { return engine.Execute(Primitive, null, cts.Token); }
            catch (OperationCanceledException e) { Assert.Equal(Status.Cancelled, Assert.IsType<LayaException>(e.InnerException).Status); return baseline; }
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
