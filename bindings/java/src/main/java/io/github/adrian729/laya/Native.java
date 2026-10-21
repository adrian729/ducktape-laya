package io.github.adrian729.laya;

import java.io.IOException;
import java.io.InputStream;
import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.nio.file.*;
import java.util.Locale;

/** Locates libducktape_laya and binds its C ABI. */
final class Native {
    /** libducktape_laya.so on Linux, libducktape_laya.dylib on macOS. */
    static final String LIB = System.mapLibraryName("ducktape_laya");
    /** Bundled library resource for this JVM's architecture: /native/linux-x64/ or /native/linux-arm64/. */
    static String bundled(String arch) {
        return "/native/linux-" + (arch.equals("aarch64") || arch.equals("arm64") ? "arm64" : "x64") + "/" + LIB;
    }
    static final AddressLayout PTR = ValueLayout.ADDRESS;
    /** dtl_call_options: struct_size, reserved, request_id, timeout_ms, on_event, event_user_data, should_cancel, cancel_user_data. */
    static final StructLayout CALL_OPTIONS = MemoryLayout.structLayout(
        ValueLayout.JAVA_INT, ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG, ValueLayout.JAVA_LONG,
        PTR, PTR, PTR, PTR);
    static final long REQUEST_ID = 8, TIMEOUT_MS = 16, ON_EVENT = 24, EVENT_USER_DATA = 32, SHOULD_CANCEL = 40, CANCEL_USER_DATA = 48;

    private static final Linker LINKER = Linker.nativeLinker();

    final MethodHandle DEFAULT_CALL_OPTIONS;
    final MethodHandle INFO;
    final MethodHandle OPEN;
    final MethodHandle ROUTER_OPEN;
    final MethodHandle EXECUTE;
    final MethodHandle CLOSE;
    final MethodHandle FREE;
    /** Address of dtl_cancel_flag, used as the should_cancel callback. */
    final MemorySegment CANCEL_FLAG;

    /** Loads lazily on first use; a failure is cached and rethrown as a fresh LayaException per call. */
    private static final class Holder {
        static final Native INSTANCE;
        static final Throwable FAILURE;

        static {
            Native n = null;
            Throwable failure = null;
            try {
                n = new Native(load());
            } catch (Throwable t) {
                failure = t;
            }
            INSTANCE = n;
            FAILURE = failure;
        }
    }

    static Native get() {
        if (Holder.INSTANCE != null) return Holder.INSTANCE;
        Throwable f = Holder.FAILURE;
        throw new LayaException(LayaException.UNSUPPORTED_PLATFORM,
            "Cannot load the ducktape-laya native library: " + f.getMessage(), f);
    }

    private Native(SymbolLookup lookup) throws Throwable {
        DEFAULT_CALL_OPTIONS = bind(lookup, "dtl_default_call_options", FunctionDescriptor.of(CALL_OPTIONS));
        INFO = bind(lookup, "dtl_info", FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, PTR, PTR));
        OPEN = bind(lookup, "dtl_open", FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, PTR, PTR, PTR));
        ROUTER_OPEN = bind(lookup, "dtl_open_router", FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, PTR, PTR, PTR));
        EXECUTE = bind(lookup, "dtl_execute", FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, PTR, PTR, PTR, PTR));
        CLOSE = bind(lookup, "dtl_close", FunctionDescriptor.ofVoid(PTR));
        FREE = bind(lookup, "dtl_free", FunctionDescriptor.ofVoid(PTR));
        CANCEL_FLAG = lookup.find("dtl_cancel_flag").orElseThrow(() -> incompatible("lacks dtl_cancel_flag"));
        MethodHandle abi = bind(lookup, "dtl_abi_version", FunctionDescriptor.of(ValueLayout.JAVA_INT));
        int version = (int) abi.invokeExact();
        if (version != 2) throw incompatible("reports ABI version " + version + ", expected 2");
    }

    private static LayaException incompatible(String why) {
        return new LayaException(LayaException.UNSUPPORTED_PLATFORM,
            "Incompatible or too old ducktape-laya native library (" + why + ")");
    }

    private static MethodHandle bind(SymbolLookup lookup, String name, FunctionDescriptor fd) {
        MemorySegment symbol = lookup.find(name).orElseThrow(() -> incompatible("lacks symbol " + name));
        return LINKER.downcallHandle(symbol, fd);
    }

    private static SymbolLookup load() {
        Arena global = Arena.global();
        String path = System.getProperty("ducktape.laya.library");
        if (path != null && !path.isEmpty()) return open(Path.of(path), global);
        String dir = System.getenv("DUCKTAPE_LAYA_LIB_DIR");
        if (dir != null && !dir.isEmpty()) return open(Path.of(dir, LIB), global);
        String os = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
        String arch = System.getProperty("os.arch", "").toLowerCase(Locale.ROOT);
        if (!os.contains("linux") || !(arch.equals("amd64") || arch.equals("x86_64") || arch.equals("aarch64") || arch.equals("arm64")))
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM,
                "ducktape-laya supports Linux x86-64 and arm64 (found " + os + "/" + arch + ")");
        Path bundled = extractBundled(bundled(arch));
        if (bundled != null) return open(bundled, global);
        RuntimeException last = null;
        String versioned = LIB.endsWith(".dylib") ? "libducktape_laya.1.dylib" : LIB + ".1";
        for (String name : new String[] {versioned, LIB}) {
            try {
                return SymbolLookup.libraryLookup(name, global);
            } catch (RuntimeException e) {
                last = e;
            }
        }
        throw new LayaException(LayaException.UNSUPPORTED_PLATFORM,
            "Cannot find " + LIB + ": set -Dducktape.laya.library, DUCKTAPE_LAYA_LIB_DIR or the loader path", last);
    }

    private static SymbolLookup open(Path path, Arena arena) {
        try {
            return SymbolLookup.libraryLookup(path, arena);
        } catch (RuntimeException e) {
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot load " + path + ": " + e.getMessage(), e);
        }
    }

    private static Path extractBundled(String resource) {
        try (InputStream in = Native.class.getResourceAsStream(resource)) {
            if (in == null) return null;
            Path dir = Files.createTempDirectory("ducktape-laya");
            Path file = dir.resolve(LIB);
            Files.copy(in, file);
            // Hooks run in reverse registration order: the directory must be registered first.
            dir.toFile().deleteOnExit();
            file.toFile().deleteOnExit();
            return file;
        } catch (IOException e) {
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot extract bundled library: " + e.getMessage(), e);
        }
    }

    static void free(MemorySegment buffer) {
        try {
            get().FREE.invokeExact(buffer);
        } catch (Throwable t) {
            throw sneaky(t);
        }
    }

    static RuntimeException sneaky(Throwable t) {
        if (t instanceof RuntimeException r) return r;
        if (t instanceof Error e) throw e;
        return new LayaException(LayaException.INFERENCE_ERROR, "Native call failed: " + t, t);
    }
}
