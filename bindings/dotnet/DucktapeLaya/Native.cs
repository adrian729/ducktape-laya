using System;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;

[assembly: DisableRuntimeMarshalling]

namespace Ducktape.Laya;

[StructLayout(LayoutKind.Sequential)]
internal struct DtlOptions { public uint StructSize, AbiVersion, Threads, Reserved; }

[StructLayout(LayoutKind.Sequential)]
internal unsafe struct DtlObserver
{
    public ulong RequestId;
    public delegate* unmanaged<byte*, void*, void> Callback;
    public void* UserData;
}

[StructLayout(LayoutKind.Sequential)]
internal unsafe struct DtlCallOptions
{
    public uint StructSize, Reserved;
    public ulong TimeoutMs;
    public delegate* unmanaged<void*, uint> ShouldCancel;
    public void* UserData;
}

internal static unsafe partial class Native
{
    const string Lib = "ducktape_laya";
    const string FileName = "libducktape_laya.so";

    internal static void Init() => NativeLibrary.SetDllImportResolver(typeof(Native).Assembly, Resolve);

    static IntPtr Resolve(string name, Assembly assembly, DllImportSearchPath? path)
    {
        if (name != Lib) return IntPtr.Zero;
        var dir = Environment.GetEnvironmentVariable("DUCKTAPE_LAYA_LIB_DIR");
        if (!string.IsNullOrEmpty(dir) && NativeLibrary.TryLoad(System.IO.Path.Combine(dir, FileName), out var handle)) return handle;
        // The NuGet runtimes/linux-x64/native layout is found by default probing, as are the soname and plain name.
        foreach (var candidate in new[] { FileName, FileName + ".0" })
            if (NativeLibrary.TryLoad(candidate, assembly, path, out handle)) return handle;
        return IntPtr.Zero;
    }

    [LibraryImport(Lib)] internal static partial DtlOptions dtl_default_options();
    [LibraryImport(Lib)] internal static partial DtlCallOptions dtl_default_call_options();
    [LibraryImport(Lib)] internal static partial int dtl_info(void* engine, byte** result, byte** error);
    [LibraryImport(Lib)] internal static partial int dtl_open(byte* path, DtlOptions* options, void** engine, byte** error);
    [LibraryImport(Lib)] internal static partial int dtl_router_open(byte* config, DtlOptions* options, void** router, byte** error);
    [LibraryImport(Lib)] internal static partial int dtl_execute_with_options(void* engine, byte* request, DtlObserver* observer, DtlCallOptions* options, byte** result, byte** error);
    [LibraryImport(Lib)] internal static partial int dtl_decide_with_options(void* engine, byte* request, DtlObserver* observer, DtlCallOptions* options, byte** result, byte** error);
    [LibraryImport(Lib)] internal static partial int dtl_router_execute_with_options(void* router, byte* request, DtlObserver* observer, DtlCallOptions* options, byte** result, byte** error);
    [LibraryImport(Lib)] internal static partial void dtl_close(void* engine);
    [LibraryImport(Lib)] internal static partial void dtl_router_close(void* router);
    [LibraryImport(Lib)] internal static partial void dtl_free(void* buffer);
}
