using System;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Threading;

[assembly: DisableRuntimeMarshalling]

namespace Ducktape.Laya;

[StructLayout(LayoutKind.Sequential)]
internal unsafe struct DtlCallOptions
{
    public uint StructSize, Reserved;
    public ulong RequestId, TimeoutMs;
    public delegate* unmanaged<byte*, void*, void> OnEvent;
    public void* EventUserData;
    public delegate* unmanaged<void*, uint> ShouldCancel;
    public void* CancelUserData;
}

internal static unsafe partial class Native
{
    const string Lib = "ducktape_laya";
    const string FileName = "libducktape_laya.so";

    static readonly object Gate = new();
    static bool _resolverSet, _ready;
    static IntPtr _handle;

    /// <summary>Native <c>dtl_cancel_flag</c>: a should_cancel that never calls into managed code.</summary>
    internal static delegate* unmanaged<void*, uint> CancelFlag;

    /// <summary>Loads and validates the native library. Success is cached; failures are rethrown on every call.</summary>
    internal static void Ensure()
    {
        if (Volatile.Read(ref _ready)) return;
        lock (Gate)
        {
            if (_ready) return;
            var handle = Load();
            if (!NativeLibrary.TryGetExport(handle, "dtl_abi_version", out var abi))
                throw Incompatible("it does not export dtl_abi_version");
            var version = ((delegate* unmanaged<uint>)abi)();
            if (version != 2) throw Incompatible($"it reports ABI version {version}, expected 2");
            if (!NativeLibrary.TryGetExport(handle, "dtl_cancel_flag", out var cancel))
                throw Incompatible("it does not export dtl_cancel_flag");
            CancelFlag = (delegate* unmanaged<void*, uint>)cancel;
            _handle = handle;
            if (!_resolverSet)
            {
                NativeLibrary.SetDllImportResolver(typeof(Native).Assembly, Resolve);
                _resolverSet = true;
            }
            Volatile.Write(ref _ready, true);
        }
    }

    static LayaException Incompatible(string why) =>
        new((int)LayaStatus.UnsupportedPlatform, $"The ducktape_laya native library is incompatible or too old: {why}.");

    static IntPtr Load()
    {
        var dir = Environment.GetEnvironmentVariable("DUCKTAPE_LAYA_LIB_DIR");
        if (!string.IsNullOrEmpty(dir))
        {
            // An explicit override never falls back to other locations.
            var full = System.IO.Path.Combine(dir, FileName);
            if (NativeLibrary.TryLoad(full, out var explicitHandle)) return explicitHandle;
            throw new DllNotFoundException($"DUCKTAPE_LAYA_LIB_DIR is set but '{full}' could not be loaded.");
        }
        var assembly = typeof(Native).Assembly;
        // The NuGet runtimes/linux-x64/native layout is found by default probing, as are the soname and plain name.
        foreach (var candidate in new[] { FileName, FileName + ".1" })
            if (NativeLibrary.TryLoad(candidate, assembly, null, out var handle)) return handle;
        throw new DllNotFoundException($"{FileName} was not found. Set DUCKTAPE_LAYA_LIB_DIR or install the native library.");
    }

    static IntPtr Resolve(string name, Assembly assembly, DllImportSearchPath? path) => name == Lib ? _handle : IntPtr.Zero;

    [LibraryImport(Lib)] internal static partial DtlCallOptions dtl_default_call_options();
    [LibraryImport(Lib)] internal static partial int dtl_info(void* engine, byte** result, byte** error);
    [LibraryImport(Lib)] internal static partial int dtl_open(byte* path, byte* config, void** engine, byte** error);
    [LibraryImport(Lib)] internal static partial int dtl_open_router(byte* router, byte* config, void** engine, byte** error);
    [LibraryImport(Lib)] internal static partial int dtl_execute(void* engine, byte* request, DtlCallOptions* options, byte** result, byte** error);
    [LibraryImport(Lib)] internal static partial void dtl_close(void* engine);
    [LibraryImport(Lib)] internal static partial void dtl_free(void* buffer);
}
