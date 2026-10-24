using System;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Threading;

[assembly: DisableRuntimeMarshalling]
// Keeps the current directory and PATH out of the Windows search for the library.
[assembly: DefaultDllImportSearchPaths(DllImportSearchPath.AssemblyDirectory | DllImportSearchPath.SafeDirectories)]

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
    internal static readonly string FileName = OperatingSystem.IsMacOS() ? "libducktape_laya.dylib"
        : OperatingSystem.IsWindows() ? "ducktape_laya.dll" : "libducktape_laya.so";
    static readonly string VersionedName = OperatingSystem.IsMacOS() ? "libducktape_laya.1.dylib"
        : OperatingSystem.IsWindows() ? "ducktape_laya.dll" : "libducktape_laya.so.1";

    // NuGet runtime identifier of this process, e.g. linux-x64 (null for an unsupported platform).
    internal static string? RuntimeId()
    {
        var os = OperatingSystem.IsWindows() ? "win" : OperatingSystem.IsMacOS() ? "osx" : OperatingSystem.IsLinux() ? "linux" : null;
        var arch = RuntimeInformation.ProcessArchitecture switch
        {
            Architecture.X64 => "x64",
            Architecture.Arm64 => "arm64",
            _ => null,
        };
        return os is null || arch is null ? null : os + "-" + arch;
    }

    /// <summary>The library file shipped with the package, if present next to <paramref name="baseDir"/>:
    /// runtimes/&lt;rid&gt;/native/ (the NuGet layout), or the directory itself (a flattened publish).</summary>
    internal static string? BundledPath(string baseDir)
    {
        var rid = RuntimeId();
        if (rid is not null)
        {
            var nuget = System.IO.Path.Combine(baseDir, "runtimes", rid, "native", FileName);
            if (System.IO.File.Exists(nuget)) return nuget;
        }
        var flat = System.IO.Path.Combine(baseDir, FileName);
        return System.IO.File.Exists(flat) ? flat : null;
    }

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

    static LayaException LoadError(string message, Exception? inner = null) => new((int)LayaStatus.UnsupportedPlatform, message, inner);

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

    /// <summary>Loads exactly this file, or throws status 7 with the operating system's reason. A missing path
    /// fails before the loader runs: macOS dyld would otherwise find a library of the same name on DYLD_LIBRARY_PATH.</summary>
    internal static IntPtr LoadFile(string path, string what, string? hint)
    {
        if (!System.IO.File.Exists(path)) throw LoadError($"{what} {path} does not exist.", new DllNotFoundException(path));
        try { return NativeLibrary.Load(path); }
        catch (Exception ex) when (ex is DllNotFoundException or BadImageFormatException)
        {
            throw LoadError($"{what} {path} was found but could not be loaded: {ex.Message}" + (hint is null ? "" : $" ({hint})"), ex);
        }
    }

    /// <summary>Why a library file that exists may fail to load on this platform.</summary>
    internal static string LoadHint() => OperatingSystem.IsWindows() ? "it needs the Microsoft Visual C++ Redistributable"
        : OperatingSystem.IsMacOS() ? "it needs macOS 14 or newer" : "it needs glibc 2.38+ and libstdc++ with GLIBCXX_3.4.29";

    /// <summary>True when the directories hold a musl dynamic loader and no glibc one (by default /lib and /lib64).</summary>
    internal static bool IsMusl(params string[] dirs)
    {
        if (dirs.Length == 0) dirs = ["/lib", "/lib64"];
        bool musl = false, glibc = false;
        foreach (var dir in dirs)
        {
            string[] names;
            try { names = System.IO.Directory.GetFiles(dir); } catch (Exception) { continue; }
            foreach (var name in names)
            {
                var file = System.IO.Path.GetFileName(name);
                musl |= file.StartsWith("ld-musl-", StringComparison.Ordinal);
                glibc |= file.StartsWith("ld-linux-", StringComparison.Ordinal);
            }
        }
        return musl && !glibc;
    }

    internal static LayaException MuslUnsupported() => LoadError(
        "The bundled ducktape_laya library needs glibc, and this looks like a musl system (for example Alpine), and no musl build of "
        + "libducktape_laya.so.1 is on the loader path; use a glibc-based image, or set DUCKTAPE_LAYA_LIB_DIR to a musl build.");

    // Released packages; any other platform needs a library built from source.
    const string Released = "packages exist for Linux x86-64 and arm64 and Windows x64; on other platforms build the library from source and set DUCKTAPE_LAYA_LIB_DIR";

    static (IntPtr, IntPtr) Load()
    {
        // An explicit override is honoured or fails; it never falls back to other locations.
        var dir = Environment.GetEnvironmentVariable("DUCKTAPE_LAYA_LIB_DIR");
        if (!string.IsNullOrEmpty(dir)) return Checked(LoadFile(System.IO.Path.Combine(dir, FileName), "DUCKTAPE_LAYA_LIB_DIR is set but", null));
        // On a glibc system, the copy shipped with the package (runtimes/<rid>/native/, or next to the assembly) comes
        // first, so an unrelated installed version of the library cannot shadow it; present, it must load. On musl
        // the bundled glibc build cannot load, so only a musl build through the system loader can.
        var musl = OperatingSystem.IsLinux() && IsMusl();
        if (!musl && BundledPath(AppContext.BaseDirectory) is { } bundled)
            return Checked(LoadFile(bundled, "The bundled library", LoadHint()));
        // The soname, then the plain name, through the system loader: the assembly directory and the safe system
        // directories (never the current directory or PATH) on Windows, the dynamic loader's search on Linux.
        foreach (var name in new[] { VersionedName, FileName })
        {
            if (NativeLibrary.TryLoad(name, typeof(Native).Assembly, DllImportSearchPath.AssemblyDirectory | DllImportSearchPath.SafeDirectories, out var handle))
                return Checked(handle);
        }
        if (musl) throw MuslUnsupported();
        var missing = new DllNotFoundException($"{FileName} was not found.");
        throw LoadError(RuntimeId() is null
            ? $"Unsupported platform {RuntimeInformation.OSDescription} {RuntimeInformation.ProcessArchitecture}: ducktape-laya {Released}."
            : $"{FileName} was not found: the application has no bundled copy (runtimes/{RuntimeId()}/native/) and the system loader has none. "
              + $"Install the DucktapeLaya package for this platform, or set DUCKTAPE_LAYA_LIB_DIR (ducktape-laya {Released}).", missing);
    }

    static (IntPtr, IntPtr) Checked(IntPtr handle)
    {
        try { return (handle, Validate(handle)); }
        catch (LayaException)
        {
            NativeLibrary.Free(handle);
            throw;
        }
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
