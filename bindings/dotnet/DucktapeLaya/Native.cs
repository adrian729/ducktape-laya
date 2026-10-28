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

[StructLayout(LayoutKind.Sequential)]
internal unsafe struct DlInfo
{
    public byte* FileName;
    public void* BaseAddress;
    public byte* SymbolName;
    public void* SymbolAddress;
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

    /// <summary>Loads and validates the native library. Success is cached; a failure is not, so the next call tries again.</summary>
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

    static LayaLoadException Incompatible(string why) =>
        new($"The ducktape_laya native library is incompatible or too old: {why}.");

    static LayaLoadException LoadError(string message, Exception? inner = null) => new(message, inner);

    /// <summary>This package's version: the informational version without the build metadata after '+'.</summary>
    internal static string PackageVersion()
    {
        var text = typeof(Native).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "unknown";
        var plus = text.IndexOf('+');
        return plus < 0 ? text : text[..plus];
    }

    /// <summary>Throws status 7 unless the runtime info JSON (<c>dtl_info(NULL)</c>) reports <c>package_version</c> equal to <paramref name="expected"/>.</summary>
    internal static void CheckVersion(string infoJson, string expected)
    {
        string version = "unknown";
        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(infoJson);
            if (doc.RootElement.ValueKind == System.Text.Json.JsonValueKind.Object
                && doc.RootElement.TryGetProperty("package_version", out var v) && v.ValueKind == System.Text.Json.JsonValueKind.String)
                version = v.GetString()!;
        }
        catch (System.Text.Json.JsonException) { }
        if (version != expected)
            throw LoadError($"The native ducktape_laya library is version {version}, but this package is {expected}; "
                + "another copy of the library was loaded instead of the one shipped with the package.");
    }

    // dtl_info(NULL) of a loaded library, as text.
    static string ReadRuntimeInfo(IntPtr handle)
    {
        if (!NativeLibrary.TryGetExport(handle, "dtl_info", out var info) || !NativeLibrary.TryGetExport(handle, "dtl_free", out var free))
            throw Incompatible("it does not export dtl_info and dtl_free");
        byte* result = null, error = null;
        var status = ((delegate* unmanaged<void*, byte**, byte**, int>)info)(null, &result, &error);
        try
        {
            if (status != 0 || result == null) throw Incompatible($"dtl_info failed with status {status}");
            return Marshal.PtrToStringUTF8((IntPtr)result) ?? "";
        }
        finally
        {
            ((delegate* unmanaged<void*, void>)free)(result);
            ((delegate* unmanaged<void*, void>)free)(error);
        }
    }

    /// <summary>The file an override names: <c>DUCKTAPE_LAYA_LIBRARY</c> (a file), else <c>DUCKTAPE_LAYA_LIB_DIR</c> (a directory) plus the platform's
    /// file name; null when neither is set. Both must be absolute paths (status 7).</summary>
    internal static string? OverridePath(string? library, string? dir)
    {
        if (!string.IsNullOrEmpty(library))
            return System.IO.Path.IsPathFullyQualified(library) ? library : throw LoadError($"DUCKTAPE_LAYA_LIBRARY must be an absolute path, got {library}.");
        if (!string.IsNullOrEmpty(dir))
            return System.IO.Path.IsPathFullyQualified(dir) ? System.IO.Path.Combine(dir, FileName) : throw LoadError($"DUCKTAPE_LAYA_LIB_DIR must be an absolute path, got {dir}.");
        return null;
    }

    const string LibSystem = "/usr/lib/libSystem.B.dylib";

    // macOS: realpath() through libSystem (resolves directory symlinks such as /tmp -> /private/tmp).
    static string? RealPath(IntPtr libSystem, string path)
    {
        if (!NativeLibrary.TryGetExport(libSystem, "realpath", out var realpath)) return null;
        var input = Marshal.StringToCoTaskMemUTF8(path);
        try
        {
            byte* buffer = stackalloc byte[4096];
            var result = ((delegate* unmanaged<byte*, byte*, byte*>)realpath)((byte*)input, buffer);
            return result == null ? null : Marshal.PtrToStringUTF8((IntPtr)buffer);
        }
        finally { Marshal.FreeCoTaskMem(input); }
    }

    /// <summary>macOS only: dyld searches DYLD_LIBRARY_PATH by leaf name even for an absolute path, so a library of the
    /// same name there replaces the one asked for. Asks dladdr which image provides dtl_abi_version and throws status 7
    /// unless it is the expected file.</summary>
    internal static void RequireImage(IntPtr abi, string expected)
    {
        var libSystem = NativeLibrary.Load(LibSystem);
        string? loaded = null;
        if (NativeLibrary.TryGetExport(libSystem, "dladdr", out var dladdr))
        {
            DlInfo info = default;
            if (((delegate* unmanaged<void*, DlInfo*, int>)dladdr)((void*)abi, &info) != 0 && info.FileName != null)
                loaded = Marshal.PtrToStringUTF8((IntPtr)info.FileName);
        }
        var want = RealPath(libSystem, expected) ?? expected;
        var got = loaded is null ? null : RealPath(libSystem, loaded) ?? loaded;
        if (got != want)
            throw LoadError($"Refusing the native ducktape_laya library: {expected} was requested but dtl_abi_version comes from {loaded ?? "an unknown image"}; "
                + "DYLD_LIBRARY_PATH may have replaced it. Unset DYLD_LIBRARY_PATH.");
    }

    // Rejects a library of the wrong ABI (and, on macOS, one that is not the expected file); returns dtl_cancel_flag.
    static IntPtr Validate(IntPtr handle, string? expected)
    {
        if (!NativeLibrary.TryGetExport(handle, "dtl_abi_version", out var abi))
            throw Incompatible("it does not export dtl_abi_version");
        if (expected is not null) RequireImage(abi, expected);
        var version = ((delegate* unmanaged<uint>)abi)();
        if (version != 2) throw Incompatible($"it reports ABI version {version}, expected 2");
        if (!NativeLibrary.TryGetExport(handle, "dtl_cancel_flag", out var cancel))
            throw Incompatible("it does not export dtl_cancel_flag");
        CheckVersion(ReadRuntimeInfo(handle), PackageVersion());
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
            throw LoadError($"{what} {path} was found but could not be loaded: {ex.Message.Trim()}" + (hint is null ? "" : $" ({hint})"), ex);
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

    internal static LayaLoadException MuslUnsupported() => LoadError(
        "The bundled ducktape_laya library needs glibc, and this looks like a musl system (for example Alpine), and no musl build of "
        + "libducktape_laya.so.1 is on the loader path; use a glibc-based image, or set DUCKTAPE_LAYA_LIBRARY or DUCKTAPE_LAYA_LIB_DIR to a musl build.");

    // macOS never loads a bare name (dyld would search DYLD_LIBRARY_PATH, the rpaths and the current directory):
    // installed libraries are loaded by absolute path from Homebrew's directory and the CMake install default.
    static readonly string[] MacOSLibraryDirs = ["/opt/homebrew/lib", "/usr/local/lib"];

    // Released packages; any other platform needs a library built from source.
    const string Released = "packages exist for Linux x86-64 and arm64, macOS arm64 and x86-64 and Windows x64; on other platforms build the library from source and set DUCKTAPE_LAYA_LIBRARY or DUCKTAPE_LAYA_LIB_DIR";

    static (IntPtr, IntPtr) Load()
    {
        // An explicit override is honoured or fails; it never falls back to other locations.
        var library = Environment.GetEnvironmentVariable("DUCKTAPE_LAYA_LIBRARY");
        if (OverridePath(library, Environment.GetEnvironmentVariable("DUCKTAPE_LAYA_LIB_DIR")) is { } file)
        {
            if (OperatingSystem.IsMacOS()) file = System.IO.Path.GetFullPath(file);
            return Checked(LoadFile(file, string.IsNullOrEmpty(library) ? "DUCKTAPE_LAYA_LIB_DIR is set but" : "DUCKTAPE_LAYA_LIBRARY is set but", null), file);
        }
        // On a glibc system, the copy shipped with the package (runtimes/<rid>/native/, or next to the assembly) comes
        // first, so an unrelated installed version of the library cannot shadow it; present, it must load. On musl
        // the bundled glibc build cannot load, so only a musl build through the system loader can.
        var musl = OperatingSystem.IsLinux() && IsMusl();
        if (!musl && BundledPath(AppContext.BaseDirectory) is { } bundled)
            return Checked(LoadFile(bundled, "The bundled library", LoadHint()), bundled);
        if (OperatingSystem.IsMacOS())
        {
            // Absolute paths only, never a bare name.
            foreach (var installed in MacOSLibraryDirs)
                foreach (var name in new[] { VersionedName, FileName })
                {
                    var candidate = System.IO.Path.Combine(installed, name);
                    if (System.IO.File.Exists(candidate)) return Checked(LoadFile(candidate, "The installed library", LoadHint()), candidate);
                }
        }
        else
        {
            // The soname, then the plain name, through the system loader: the assembly directory and the safe system
            // directories (never the current directory or PATH) on Windows, the dynamic loader's search on Linux.
            foreach (var name in new[] { VersionedName, FileName })
            {
                if (NativeLibrary.TryLoad(name, typeof(Native).Assembly, DllImportSearchPath.AssemblyDirectory | DllImportSearchPath.SafeDirectories, out var handle))
                    return Checked(handle, null);
            }
        }
        if (musl) throw MuslUnsupported();
        var missing = new DllNotFoundException($"{FileName} was not found.");
        throw LoadError(RuntimeId() is null
            ? $"Unsupported platform {RuntimeInformation.OSDescription} {RuntimeInformation.ProcessArchitecture}: ducktape-laya {Released}."
            : $"{FileName} was not found: the application has no bundled copy (runtimes/{RuntimeId()}/native/) and the system loader has none. "
              + $"Install the DucktapeLaya package for this platform, or set DUCKTAPE_LAYA_LIBRARY or DUCKTAPE_LAYA_LIB_DIR (ducktape-laya {Released}).", missing);
    }

    // expected is the file that must provide the library (checked on macOS only).
    static (IntPtr, IntPtr) Checked(IntPtr handle, string? expected)
    {
        try { return (handle, Validate(handle, OperatingSystem.IsMacOS() ? expected : null)); }
        catch (LayaLoadException)
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
