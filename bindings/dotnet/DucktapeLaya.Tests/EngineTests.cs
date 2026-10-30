using System.Diagnostics;
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
        // The one status-propagation check: a native status-1 failure arrives as a LayaException with status 1.
        var native = Assert.Throws<LayaException>(() => Engine.Open("missing.gguf", new Config { ContextTokens = 0 }));
        Assert.IsType<LayaException>(native);
        Assert.Equal(Status.InvalidArgument, native.Status);
    }

    [Fact]
    public async Task AsyncSnapshotsOptions()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = Engine.OpenRouter(RouterJson(("unused", file)), One);
            var seen = new List<Event>();
            var options = new CallOptions { RequestId = 7, OnEvent = seen.Add };
            var task = engine.ExecuteAsync("{}", options);
            options.OnEvent = _ => throw new Exception("must not be used");
            options.RequestId = 8;
            options.Timeout = TimeSpan.FromSeconds(-5);
            Assert.Equal(Status.InvalidArgument, (await Assert.ThrowsAsync<LayaException>(() => task)).Status);
            Assert.Equal(2, seen.Count);
            Assert.All(seen, e => Assert.Equal(7UL, e.RequestId));
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
    public void RuntimeDiagnostics()
    {
        Assert.Contains("\"abi_version\":2", Engine.RuntimeInfo());
        Assert.Contains("\"default_context_tokens\":", Engine.RuntimeInfo());
    }

    [Fact]
    public void ModelInfo()
    {
        if (Model is not { } model) return;
        using var engine = Engine.Open(model, Two);
        var info = engine.Info();
        Assert.Contains("\"decision_type\":\"laya\"", info);
        Assert.Contains("\"context_tokens\":", info);
        Assert.Contains("\"trained_context_tokens\":", info);
        Assert.DoesNotContain("\"max_tokens\":", info);
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
            var events = new List<Event>();
            var options = new CallOptions { OnEvent = events.Add, Timeout = TimeSpan.FromSeconds(-1) };
            using var cancelled = new CancellationTokenSource();
            cancelled.Cancel();
            // A bad argument beats an already-cancelled token; the cancelled token then fails at once with no native call.
            Assert.Throws<ArgumentOutOfRangeException>(() => engine.Execute("{}", options, cancelled.Token));
            Assert.Throws<LayaArgumentException>(() => engine.Execute("a\0b", null, cancelled.Token));
            options.Timeout = null;
            var oce = Assert.Throws<OperationCanceledException>(() => engine.Execute("{}", options, cancelled.Token));
            Assert.Equal(cancelled.Token, oce.CancellationToken);
            Assert.Equal(Status.Cancelled, Assert.IsType<LayaException>(oce.InnerException).Status);
            Assert.Empty(events);
            // The cancelled token beats an expired deadline, which in turn beats the native call.
            var expired = Stopwatch.GetTimestamp() - 10 * Stopwatch.Frequency;
            Assert.IsType<OperationCanceledException>(Record.Exception(() => engine.Run("{}", expired, 0, TimeSpan.FromSeconds(1), events.Add, cancelled.Token)));
            Assert.Empty(events);
            // Closed beats everything.
            engine.Dispose();
            options.Timeout = TimeSpan.FromSeconds(-1);
            Assert.Throws<EngineClosedException>(() => engine.Execute("a\0b", options, cancelled.Token));
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public async Task AsyncWithCancelledTokenIsCancelled()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = Engine.OpenRouter(RouterJson(("unused", file)), One);
            using var cancelled = new CancellationTokenSource();
            cancelled.Cancel();
            var task = engine.ExecuteAsync("{}", null, cancelled.Token);
            await Assert.ThrowsAnyAsync<OperationCanceledException>(() => task);
            Assert.True(task.IsCanceled);
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
    public void TimeoutRangeAndDeadlineFromEntry()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = Engine.OpenRouter(RouterJson(("unused", file)), One);
            Assert.Throws<ArgumentOutOfRangeException>(() => engine.Execute("{}", new CallOptions { Timeout = TimeSpan.FromSeconds(-1) }));
            Assert.Throws<ArgumentOutOfRangeException>(() => engine.Execute("{}", new CallOptions { Timeout = TimeSpan.FromTicks(-1) }));
            // No deadline (null, zero, infinite) and no upper bound: all reach the native call (the empty request is status 1).
            foreach (TimeSpan? timeout in new TimeSpan?[] { null, TimeSpan.Zero, Timeout.InfiniteTimeSpan, TimeSpan.FromDays(365), TimeSpan.MaxValue })
                Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => engine.Execute("{}", new CallOptions { Timeout = timeout })).Status);
            // Rounded up to whole milliseconds, saturating nowhere near the unsigned range.
            Assert.Equal(1UL, Engine.Milliseconds(TimeSpan.FromTicks(1)));
            Assert.Equal(2UL, Engine.Milliseconds(TimeSpan.FromTicks(TimeSpan.TicksPerMillisecond + 1)));
            Assert.Equal((ulong)(TimeSpan.MaxValue.Ticks / TimeSpan.TicksPerMillisecond) + 1, Engine.Milliseconds(TimeSpan.MaxValue));
            // The clock starts at entry: an entry long past fails with status 6, no native call and no events.
            var events = new List<Event>();
            var expired = Stopwatch.GetTimestamp() - 10 * Stopwatch.Frequency;
            var error = Assert.Throws<LayaException>(() => engine.Run("{}", expired, 0, TimeSpan.FromSeconds(1), events.Add, default));
            Assert.Equal(Status.DeadlineExceeded, error.Status);
            Assert.Empty(events);
            Assert.Equal(Status.InvalidArgument, Assert.Throws<LayaException>(() => engine.Run("{}", expired, 0, TimeSpan.FromHours(1), events.Add, default)).Status);
            Assert.Equal(2, events.Count);
        }
        finally { File.Delete(file); }
    }

    [Fact]
    public void EventsAreTyped()
    {
        var file = Path.GetTempFileName();
        try
        {
            using var engine = Engine.OpenRouter(RouterJson(("unused", file)), One);
            var events = new List<Event>();
            Assert.Throws<LayaException>(() => engine.Execute("{}", new CallOptions { RequestId = ulong.MaxValue, OnEvent = events.Add }));
            Assert.Equal(2, events.Count);
            Assert.Equal(new Event(EventKind.Start, ulong.MaxValue, Status.Ok, 0), events[0]);
            Assert.Equal(EventKind.Error, events[1].Kind);
            Assert.Equal(ulong.MaxValue, events[1].RequestId);
            Assert.Equal(Status.InvalidArgument, events[1].Status);
            Assert.True(events[1].ElapsedMs >= 0);
            // The default id is 0.
            events.Clear();
            Assert.Throws<LayaException>(() => engine.Execute("{}", new CallOptions { OnEvent = events.Add }));
            Assert.All(events, e => Assert.Equal(0UL, e.RequestId));
            // An event that does not parse is dropped.
            Assert.True(Event.TryParse("""{"event":"end","request_id":18446744073709551615,"status":0,"elapsed_ms":1.5}""", out var end));
            Assert.Equal(new Event(EventKind.End, ulong.MaxValue, Status.Ok, 1.5), end);
            foreach (var bad in new[] { "", "[]", "{}", """{"event":"other","request_id":1,"status":0,"elapsed_ms":0}""", """{"event":"end","request_id":-1,"status":0,"elapsed_ms":0}""" })
                Assert.False(Event.TryParse(bad, out _));
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

    // Takes seconds uncancelled: a long state scanned in windows, with every question asked in each.
    static readonly string Long = "{\"state\":\"" + string.Concat(Enumerable.Repeat("The customer writes about a late parcel and asks for money back. ", 8)) +
        "\",\"overflow\":\"scan\",\"questions\":{" + string.Join(",", Enumerable.Range(0, 2).Select(i => $"\"q{i}\":{{\"type\":\"noul\",\"instructions\":\"Is a refund requested ({i})?\"}}")) + "}}";

    // The uncancelled run time of Long, measured once on its own engine.
    static readonly Lazy<TimeSpan> LongTime = new(() =>
    {
        using var engine = Engine.Open(Model!, Two);
        engine.Execute(Primitive);
        var clock = Stopwatch.StartNew();
        engine.Execute(Long);
        return clock.Elapsed;
    });

    [Fact]
    public void RealFeatures()
    {
        if (Model is not { } path) return;
        using var engine = Engine.Open(path, Two);
        var single = engine.Execute(Schema);
        Assert.Contains("\"values\":{\"refund\":true}", single);
        var events = new List<Event>();
        var result = engine.Execute($"[{Schema},{Schema}]", new CallOptions { RequestId = ulong.MaxValue, OnEvent = events.Add });
        Assert.Equal($"[{single},{single}]", result);
        Assert.Equal(2, events.Count);
        Assert.Equal(new Event(EventKind.Start, ulong.MaxValue, Status.Ok, 0), events[0]);
        Assert.Equal(EventKind.End, events[1].Kind);
        Assert.Equal(ulong.MaxValue, events[1].RequestId);
        Assert.Equal(Status.Ok, events[1].Status);
        Assert.True(events[1].ElapsedMs > 0);

        // Reentry on the same engine from an observer.
        engine.Execute(Schema, new CallOptions { RequestId = 23, OnEvent = _ => engine.Execute(Schema) });
        // Observer failures never change the result.
        Assert.Equal(single, engine.Execute(Schema, new CallOptions { OnEvent = _ => throw new Exception("contained") }));

        using var router = Engine.OpenRouter(RouterJson(("english", path)), Two);
        Assert.Contains("\"model\":\"english\"", router.Execute(WithKey(Schema, "model", "english")));
    }

    [Fact]
    public async Task CancellationAndDeadline()
    {
        if (Model is not { } path) return;
        using var engine = Engine.Open(path, Two);
        var baseline = engine.Execute(Primitive);
        var fullTime = LongTime.Value;
        var clock = new Stopwatch();
        // A token cancelled from the start event stops the native call early: status 5, far below the uncancelled run.
        using var mid = new CancellationTokenSource();
        var events = new List<Event>();
        clock.Restart();
        var sync = Assert.Throws<OperationCanceledException>(() => engine.Execute(Long, new CallOptions
        {
            OnEvent = e => { events.Add(e); if (e.Kind == EventKind.Start) mid.Cancel(); }
        }, mid.Token));
        Assert.True(clock.Elapsed < fullTime / 2, $"cancelled run took {clock.Elapsed}, uncancelled {fullTime}");
        Assert.Equal(Status.Cancelled, Assert.IsType<LayaException>(sync.InnerException).Status);
        Assert.Equal(mid.Token, sync.CancellationToken);
        Assert.Equal([EventKind.Start, EventKind.Error], events.Select(e => e.Kind));
        Assert.Equal(Status.Cancelled, events[1].Status);
        // The same through ExecuteAsync.
        using var asyncMid = new CancellationTokenSource();
        clock.Restart();
        var running = engine.ExecuteAsync(Long, new CallOptions { OnEvent = e => { if (e.Kind == EventKind.Start) asyncMid.Cancel(); } }, asyncMid.Token);
        var canceled = await Assert.ThrowsAnyAsync<OperationCanceledException>(() => running);
        Assert.True(running.IsCanceled);
        Assert.True(clock.Elapsed < fullTime / 2, $"cancelled run took {clock.Elapsed}, uncancelled {fullTime}");
        Assert.Equal(Status.Cancelled, Assert.IsType<LayaException>(canceled.InnerException).Status);
        // A native deadline stops the run with status 6, and no upper bound keeps a long one running.
        clock.Restart();
        Assert.Equal(Status.DeadlineExceeded, Assert.Throws<LayaException>(() => engine.Execute(Long, new CallOptions { Timeout = TimeSpan.FromMilliseconds(1) })).Status);
        Assert.True(clock.Elapsed < fullTime / 2, $"deadline run took {clock.Elapsed}, uncancelled {fullTime}");
        Assert.Equal(baseline, engine.Execute(Primitive, new CallOptions { Timeout = TimeSpan.MaxValue }));
        using var live = new CancellationTokenSource();
        Assert.Equal(baseline, await engine.ExecuteAsync(Primitive, null, live.Token));
    }

    [Fact]
    public async Task ClosePendingCancelsRunningCalls()
    {
        if (Model is not { } path) return;
        var engine = Engine.Open(path, Two);
        var fullTime = LongTime.Value;
        var clock = new Stopwatch();
        // From another thread: wait for the start event, then Close(true) cancels the call and returns once it has.
        using var started = new ManualResetEventSlim();
        using var shared = new CancellationTokenSource();
        var call = Task.Run(() => engine.Execute(Long, new CallOptions { OnEvent = e => { if (e.Kind == EventKind.Start) started.Set(); } }, shared.Token));
        Assert.True(started.Wait(TimeSpan.FromSeconds(60)));
        clock.Restart();
        engine.Close(cancelPending: true);
        var error = await Assert.ThrowsAsync<OperationCanceledException>(() => call);
        Assert.Equal(Status.Cancelled, Assert.IsType<LayaException>(error.InnerException).Status);
        Assert.True(clock.Elapsed < fullTime / 2, $"close took {clock.Elapsed}, uncancelled call {fullTime}");
        // The caller's own token is not cancelled by it.
        Assert.False(shared.IsCancellationRequested);
        Assert.Throws<EngineClosedException>(() => engine.Execute(Primitive));

        // From the call's own event callback: it cancels that call and returns at once.
        using var other = Engine.Open(path, Two);
        clock.Restart();
        var inner = Assert.Throws<OperationCanceledException>(() => other.Execute(Long, new CallOptions { OnEvent = e => { if (e.Kind == EventKind.Start) other.Close(true); } }));
        Assert.Equal(Status.Cancelled, Assert.IsType<LayaException>(inner.InnerException).Status);
        Assert.True(clock.Elapsed < fullTime / 2);
        Assert.Throws<EngineClosedException>(() => other.Execute(Primitive));
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
