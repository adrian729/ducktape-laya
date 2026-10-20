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
    // Calls hold the read lock; Dispose takes the write lock and so waits for in-flight calls.
    // Recursion is supported so observers may reenter.
    readonly ReaderWriterLockSlim _lock = new(LockRecursionPolicy.SupportsRecursion);
    volatile bool _closing;
    int _released;

    LayaEngine(void* handle) { _handle = handle; }

    /// <summary>Runtime identity and CPU capabilities without loading a model.</summary>
    public static string RuntimeInfo() { Native.Ensure(); return ReadInfo(null); }

    /// <summary>Opens one model.</summary>
    /// <exception cref="ArgumentNullException"><paramref name="modelPath"/> is null.</exception>
    /// <exception cref="ArgumentOutOfRangeException">A <paramref name="config"/> value is invalid (see <see cref="LayaConfig"/>).</exception>
    /// <exception cref="LayaException">Native failure, or the path contains a NUL character (status 1).</exception>
    /// <exception cref="DllNotFoundException">The native library cannot be loaded.</exception>
    public static LayaEngine Open(string modelPath, LayaConfig? config = null)
    {
        var cfg = config?.ToJson();
        Native.Ensure();
        var path = Encode(modelPath, "model path");
        var json = cfg is null ? null : Encode(cfg, "config");
        void* raw = null; byte* error = null;
        int status;
        fixed (byte* p = path) fixed (byte* c = json) status = Native.dtl_open(p, c, &raw, &error);
        Check(status, error);
        return raw == null ? throw new LayaException(2, "Native engine returned null") : new LayaEngine(raw);
    }

    /// <summary>Opens a bounded, lazy local model registry. <paramref name="config"/> applies to every route; a route's own config overrides it. See docs/API.md.</summary>
    public static LayaEngine OpenRouter(string routerJson, LayaConfig? config = null)
    {
        var cfg = config?.ToJson();
        Native.Ensure();
        var router = Encode(routerJson, "router config");
        var json = cfg is null ? null : Encode(cfg, "config");
        void* raw = null; byte* error = null;
        int status;
        fixed (byte* p = router) fixed (byte* c = json) status = Native.dtl_open_router(p, c, &raw, &error);
        Check(status, error);
        return raw == null ? throw new LayaException(2, "Native router returned null") : new LayaEngine(raw);
    }

    /// <summary>Model metadata and calibration for a single engine; routers report the runtime only.</summary>
    public string Info()
    {
        EnterRead();
        try { return ReadInfo(_handle); }
        finally { ExitRead(); }
    }

    /// <summary>Executes one request or a bounded JSON array of requests.</summary>
    public string Execute(string request) => Run(request, 0, null, null, default);
    /// <inheritdoc cref="Execute(string)"/>
    /// <exception cref="OperationCanceledException">The token was cancelled and ended the call (native status 5); the inner exception is a <see cref="LayaException"/>. An exceeded <c>Timeout</c> is a <see cref="LayaException"/> with status 6.</exception>
    /// <exception cref="ArgumentOutOfRangeException"><c>Timeout</c> is negative or over 24 hours.</exception>
    public string Execute(string request, LayaCallOptions? options, CancellationToken cancellationToken = default) =>
        options is null ? Run(request, 0, null, null, cancellationToken) : Run(request, options.RequestId, options.Timeout, options.OnEvent, cancellationToken);

    /// <summary>Runs <see cref="Execute(string, LayaCallOptions?, CancellationToken)"/> on a thread-pool thread, which the call occupies for the whole inference;
    /// <c>OnEvent</c> runs on that thread. The options are snapshotted when called, so the object may be reused or changed afterwards.
    /// Cancellation through the token yields a canceled task (<see cref="OperationCanceledException"/> wrapping the status-5 <see cref="LayaException"/>).</summary>
    public Task<string> ExecuteAsync(string request, LayaCallOptions? options = null, CancellationToken cancellationToken = default)
    {
        var (id, timeout, onEvent) = Snapshot(options);
        return Task.Run(() => Run(request, id, timeout, onEvent, cancellationToken), cancellationToken);
    }

    static (ulong, TimeSpan?, Action<string>?) Snapshot(LayaCallOptions? o) => o is null ? (0, null, null) : (o.RequestId, o.Timeout, o.OnEvent);

    /// <summary>Releases the native handle if <see cref="Dispose"/> was never called. Dispose is still required for prompt release.</summary>
    ~LayaEngine() => CloseNative();

    /// <summary>Idempotent; waits for in-flight calls. Later calls throw <see cref="ObjectDisposedException"/>.
    /// Called from inside one of this engine's own event callbacks, it cannot wait for that call: it
    /// rejects new calls and returns, and the last running call releases the engine when it finishes.</summary>
    public void Dispose()
    {
        _closing = true;
        GC.SuppressFinalize(this);
        if (_lock.IsReadLockHeld) return;
        _lock.EnterWriteLock();
        Release();
    }

    // Called with the write lock held; exits it.
    void Release()
    {
        try
        {
            CloseNative();
        }
        finally { _lock.ExitWriteLock(); }
    }

    // Exactly once, from Dispose or the finalizer.
    void CloseNative()
    {
        if (Interlocked.Exchange(ref _released, 1) != 0) return;
        Native.dtl_close(_handle);
    }

    void EnterRead()
    {
        _lock.EnterReadLock();
        if (!_closing) return;
        ExitRead();
        throw new ObjectDisposedException(nameof(LayaEngine));
    }

    void ExitRead()
    {
        _lock.ExitReadLock();
        // A Dispose deferred from inside a callback is completed by whichever call exits last.
        if (_closing && !_lock.IsReadLockHeld && _lock.TryEnterWriteLock(0)) Release();
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

    string Run(string request, ulong requestId, TimeSpan? timeout, Action<string>? onEvent, CancellationToken token)
    {
        var bytes = Encode(request, "request");
        var native = Native.dtl_default_call_options();
        native.RequestId = requestId;
        native.TimeoutMs = TimeoutMs(timeout);
        int* flag = null;
        CancellationTokenRegistration registration = default;
        GCHandle hook = default;
        EnterRead();
        try
        {
            if (onEvent is not null)
            {
                hook = GCHandle.Alloc(new Hook { Callback = onEvent });
                native.OnEvent = &Notify;
                native.EventUserData = (void*)GCHandle.ToIntPtr(hook);
            }
            if (token.CanBeCanceled)
            {
                // Native memory read by dtl_cancel_flag on backend worker threads, which never enter managed code.
                flag = (int*)NativeMemory.AllocZeroed((nuint)sizeof(int));
                native.ShouldCancel = Native.CancelFlag;
                native.CancelUserData = flag;
                registration = token.UnsafeRegister(static state => ((FlagBox)state!).Set(), new FlagBox(flag));
            }
            byte* result = null, error = null;
            int status;
            fixed (byte* p = bytes)
            {
                status = Native.dtl_execute(_handle, p, &native, &result, &error);
            }
            try
            {
                try { Check(status, error); }
                catch (LayaException ex) when (ex.Status == (int)LayaStatus.Cancelled && token.IsCancellationRequested)
                {
                    throw new OperationCanceledException(ex.Message, ex, token);
                }
                if (result == null) throw new LayaException(3, "Native result was null");
                return Marshal.PtrToStringUTF8((IntPtr)result) ?? "";
            }
            finally { Native.dtl_free(result); }
        }
        finally
        {
            GC.KeepAlive(this);
            ExitRead();
            registration.Dispose();
            if (flag != null) NativeMemory.Free(flag);
            if (hook.IsAllocated) hook.Free();
        }
    }

    static ulong TimeoutMs(TimeSpan? timeout)
    {
        if (timeout is not { } t || t == TimeSpan.Zero) return 0;
        var ms = Math.Ceiling(t.TotalMilliseconds);
        if (ms < 1 || ms > 86_400_000) throw new ArgumentOutOfRangeException(nameof(LayaCallOptions.Timeout), t, "Timeout must be between 1 ms and 24 hours");
        return (ulong)ms;
    }

    // Strict: lone surrogates would otherwise be silently replaced with U+FFFD.
    static readonly UTF8Encoding StrictUtf8 = new(false, throwOnInvalidBytes: true);

    static byte[] Encode(string text, string what = "input")
    {
        ArgumentNullException.ThrowIfNull(text);
        if (text.Contains('\0')) throw new LayaException(1, "Input contains a NUL byte");
        try
        {
            var bytes = new byte[StrictUtf8.GetByteCount(text) + 1];
            StrictUtf8.GetBytes(text, bytes);
            return bytes;
        }
        catch (EncoderFallbackException)
        {
            throw new LayaException(1, what + " is not valid Unicode");
        }
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
