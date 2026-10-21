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
    // libducktape_laya.so (soname .so.1) on Linux, libducktape_laya.dylib (install name .1.dylib) on macOS.
    static readonly string FileName = OperatingSystem.IsMacOS() ? "libducktape_laya.dylib"
        : OperatingSystem.IsWindows() ? "ducktape_laya.dll" : "libducktape_laya.so";
    static readonly string VersionedName = OperatingSystem.IsMacOS() ? "libducktape_laya.1.dylib"
        : OperatingSystem.IsWindows() ? "ducktape_laya.dll" : "libducktape_laya.so.1";

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
            var (handle, cancel) = Load();
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

    // Rejects a library of the wrong ABI; returns dtl_cancel_flag.
    static IntPtr Validate(IntPtr handle)
    {
        if (!NativeLibrary.TryGetExport(handle, "dtl_abi_version", out var abi))
            throw Incompatible("it does not export dtl_abi_version");
        var version = ((delegate* unmanaged<uint>)abi)();
        if (version != 2) throw Incompatible($"it reports ABI version {version}, expected 2");
        if (!NativeLibrary.TryGetExport(handle, "dtl_cancel_flag", out var cancel))
            throw Incompatible("it does not export dtl_cancel_flag");
        return cancel;
    }

    static (IntPtr, IntPtr) Load()
    {
        var dir = Environment.GetEnvironmentVariable("DUCKTAPE_LAYA_LIB_DIR");
        var assembly = typeof(Native).Assembly;
        // An explicit override never falls back to other locations. Otherwise the soname comes first (as in
        // the Python and Java bindings), then the plain name; the NuGet runtimes/<rid>/native layout (linux-x64, linux-arm64) is
        // found by default probing. A library of the wrong ABI is skipped in favour of the next candidate.
        var overridden = !string.IsNullOrEmpty(dir);
        var candidates = overridden
            ? new[] { System.IO.Path.Combine(dir!, FileName) }
            : new[] { VersionedName, FileName };
        LayaException? incompatible = null;
        foreach (var candidate in candidates)
        {
            // A missing explicit path must fail; macOS dyld would otherwise search DYLD_LIBRARY_PATH.
            var loaded = overridden ? System.IO.File.Exists(candidate) && NativeLibrary.TryLoad(candidate, out var h) ? h : IntPtr.Zero
                                    : NativeLibrary.TryLoad(candidate, assembly, null, out h) ? h : IntPtr.Zero;
            if (loaded == IntPtr.Zero) continue;
            try { return (loaded, Validate(loaded)); }
            catch (LayaException ex)
            {
                incompatible = ex;
                NativeLibrary.Free(loaded);
            }
        }
        if (incompatible is not null) throw incompatible;
        throw overridden
            ? new DllNotFoundException($"DUCKTAPE_LAYA_LIB_DIR is set but '{candidates[0]}' could not be loaded.")
            : new DllNotFoundException($"{FileName} was not found. Set DUCKTAPE_LAYA_LIB_DIR or install the native library.");
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
