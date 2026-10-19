using System;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace Ducktape.Laya;

/// <summary>A local Laya model engine or router. Thread-safe; native calls on one handle serialize.</summary>
public sealed unsafe class LayaEngine : IDisposable
{
    readonly void* _handle;
    readonly bool _router;
    // Calls hold the read lock; Dispose takes the write lock and so waits for in-flight calls.
    // Recursion is supported so observers may reenter. Do not dispose from inside a callback.
    readonly ReaderWriterLockSlim _lock = new(LockRecursionPolicy.SupportsRecursion);
    bool _disposed;

    static LayaEngine() => Native.Init();

    LayaEngine(void* handle, bool router) { _handle = handle; _router = router; }

    /// <summary>Runtime identity and CPU capabilities without loading a model.</summary>
    public static string RuntimeInfo() => ReadInfo(null);

    /// <param name="threads">0 selects automatically; otherwise 1..256.</param>
    public static LayaEngine Open(string modelPath, int threads = 0)
    {
        var path = Encode(modelPath);
        var options = Options(threads);
        void* raw = null; byte* error = null;
        int status;
        fixed (byte* p = path) status = Native.dtl_open(p, &options, &raw, &error);
        Check(status, error);
        return raw == null ? throw new LayaException(2, "Native engine returned null") : new LayaEngine(raw, false);
    }

    /// <summary>Opens a bounded, lazy local model registry. See docs/API.md for the config.</summary>
    public static LayaEngine OpenRouter(string configJson, int threads = 0)
    {
        var config = Encode(configJson);
        var options = Options(threads);
        void* raw = null; byte* error = null;
        int status;
        fixed (byte* p = config) status = Native.dtl_router_open(p, &options, &raw, &error);
        Check(status, error);
        return raw == null ? throw new LayaException(2, "Native router returned null") : new LayaEngine(raw, true);
    }

    /// <summary>Model metadata for a single engine; routers report common runtime capabilities.</summary>
    public string Info()
    {
        EnterRead();
        try { return ReadInfo(_router ? null : _handle); }
        finally { _lock.ExitReadLock(); }
    }

    /// <summary>Legacy primitive request semantics (unrelated top-level metadata is ignored).</summary>
    public string Decide(string request) => Run(request, null, default, true);
    public string Decide(string request, LayaCallOptions? options, CancellationToken cancellationToken = default) => Run(request, options, cancellationToken, true);

    /// <summary>Executes one request or a bounded JSON array of requests.</summary>
    public string Execute(string request) => Run(request, null, default, false);
    public string Execute(string request, LayaCallOptions? options, CancellationToken cancellationToken = default) => Run(request, options, cancellationToken, false);

    /// <summary>Runs <see cref="Execute(string, LayaCallOptions?, CancellationToken)"/> on the thread pool. Cancellation surfaces as a LayaException with status 5.</summary>
    public Task<string> ExecuteAsync(string request, LayaCallOptions? options = null, CancellationToken cancellationToken = default) =>
        Task.Run(() => Run(request, options, cancellationToken, false), CancellationToken.None);

    public Task<string> DecideAsync(string request, LayaCallOptions? options = null, CancellationToken cancellationToken = default) =>
        Task.Run(() => Run(request, options, cancellationToken, true), CancellationToken.None);

    /// <summary>Idempotent; waits for in-flight calls. Later calls throw <see cref="ObjectDisposedException"/>.
    /// Disposing from inside one of this engine's own event callbacks throws <see cref="InvalidOperationException"/>.</summary>
    public void Dispose()
    {
        if (_lock.IsReadLockHeld)
            throw new InvalidOperationException("Dispose() called from inside an active call on this engine");
        _lock.EnterWriteLock();
        try
        {
            if (_disposed) return;
            _disposed = true;
            if (_router) Native.dtl_router_close(_handle); else Native.dtl_close(_handle);
        }
        finally { _lock.ExitWriteLock(); }
    }

    void EnterRead()
    {
        _lock.EnterReadLock();
        if (!_disposed) return;
        _lock.ExitReadLock();
        throw new ObjectDisposedException(nameof(LayaEngine));
    }

    sealed class FlagBox(int* flag) { public void Set() => Volatile.Write(ref *flag, 1); }

    sealed class Hook { public Action<string> Callback = null!; }

    [UnmanagedCallersOnly]
    static void Notify(byte* text, void* user)
    {
        try
        {
            var hook = (Hook)GCHandle.FromIntPtr((IntPtr)user).Target!;
            hook.Callback(Marshal.PtrToStringUTF8((IntPtr)text) ?? "");
        }
        catch { }
    }

    [UnmanagedCallersOnly]
    static uint ShouldCancel(void* flag)
    {
        try { return Volatile.Read(ref *(int*)flag) != 0 ? 5u : 0u; }
        catch { return 5; }
    }

    string Run(string request, LayaCallOptions? options, CancellationToken token, bool legacy)
    {
        var bytes = Encode(request);
        var native = Native.dtl_default_call_options();
        var observer = new DtlObserver();
        if (options is not null)
        {
            observer.RequestId = options.RequestId;
            native.TimeoutMs = TimeoutMs(options.Timeout);
        }
        int* flag = null;
        CancellationTokenRegistration registration = default;
        GCHandle hook = default;
        EnterRead();
        try
        {
            if (options?.OnEvent is { } onEvent)
            {
                hook = GCHandle.Alloc(new Hook { Callback = onEvent });
                observer.Callback = &Notify;
                observer.UserData = (void*)GCHandle.ToIntPtr(hook);
            }
            if (token.CanBeCanceled)
            {
                // Native memory: the callback may run on backend worker threads and must stay cheap and nonblocking.
                flag = (int*)NativeMemory.AllocZeroed((nuint)sizeof(int));
                native.ShouldCancel = &ShouldCancel;
                native.UserData = flag;
                registration = token.UnsafeRegister(static state => ((FlagBox)state!).Set(), new FlagBox(flag));
            }
            byte* result = null, error = null;
            int status;
            fixed (byte* p = bytes)
            {
                var h = _handle;
                status = !_router ? (legacy
                        ? Native.dtl_decide_with_options(h, p, &observer, &native, &result, &error)
                        : Native.dtl_execute_with_options(h, p, &observer, &native, &result, &error))
                    : Native.dtl_router_execute_with_options(h, p, &observer, &native, &result, &error);
            }
            try
            {
                Check(status, error);
                if (result == null) throw new LayaException(3, "Native result was null");
                return Marshal.PtrToStringUTF8((IntPtr)result) ?? "";
            }
            finally { Native.dtl_free(result); }
        }
        finally
        {
            _lock.ExitReadLock();
            registration.Dispose();
            if (flag != null) NativeMemory.Free(flag);
            if (hook.IsAllocated) hook.Free();
        }
    }

    static ulong TimeoutMs(TimeSpan? timeout)
    {
        if (timeout is not { } t || t == TimeSpan.Zero) return 0;
        var ms = Math.Ceiling(t.TotalMilliseconds);
        if (ms < 1 || ms > 86_400_000) throw new LayaException(1, "Timeout must be between 1 ms and 24 hours");
        return (ulong)ms;
    }

    static DtlOptions Options(int threads)
    {
        if (threads < 0) throw new LayaException(1, "Thread count must not be negative");
        var options = Native.dtl_default_options();
        options.Threads = (uint)threads;
        return options;
    }

    static byte[] Encode(string text)
    {
        ArgumentNullException.ThrowIfNull(text);
        if (text.Contains('\0')) throw new LayaException(1, "Input contains a NUL byte");
        var bytes = new byte[Encoding.UTF8.GetByteCount(text) + 1];
        Encoding.UTF8.GetBytes(text, bytes);
        return bytes;
    }

    static string ReadInfo(void* engine)
    {
        byte* result = null, error = null;
        var status = Native.dtl_info(engine, &result, &error);
        try
        {
            Check(status, error);
            if (result == null) throw new LayaException(3, "Native result was null");
            return Marshal.PtrToStringUTF8((IntPtr)result) ?? "";
        }
        finally { Native.dtl_free(result); }
    }

    static void Check(int status, byte* error)
    {
        try
        {
            if (status == 0) return;
            throw new LayaException(status, error == null ? "Laya operation failed" : Marshal.PtrToStringUTF8((IntPtr)error) ?? "Laya operation failed");
        }
        finally { Native.dtl_free(error); }
    }
}
