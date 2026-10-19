package io.github.adrian729.laya;

import java.io.IOException;
import java.io.InputStream;
import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.nio.file.*;
import java.util.Locale;

/** Locates libducktape_laya and binds its C ABI. */
final class Native {
    static final String LIB = "libducktape_laya.so";
    static final String BUNDLED = "/native/linux-x64/" + LIB;
    static final AddressLayout PTR = ValueLayout.ADDRESS;
    static final StructLayout OPTIONS = MemoryLayout.structLayout(
        ValueLayout.JAVA_INT, ValueLayout.JAVA_INT, ValueLayout.JAVA_INT, ValueLayout.JAVA_INT);
    static final StructLayout CALL_OPTIONS = MemoryLayout.structLayout(
        ValueLayout.JAVA_INT, ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG, PTR, PTR);
    static final long OBSERVER_SIZE = 24;

    private static final Linker LINKER = Linker.nativeLinker();
    private static final SymbolLookup LOOKUP = load();
    static final MethodHandle DEFAULT_OPTIONS = bind("dtl_default_options", FunctionDescriptor.of(OPTIONS));
    static final MethodHandle DEFAULT_CALL_OPTIONS = bind("dtl_default_call_options", FunctionDescriptor.of(CALL_OPTIONS));
    static final MethodHandle INFO = bind("dtl_info", FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, PTR, PTR));
    static final MethodHandle OPEN = bind("dtl_open", FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, PTR, PTR, PTR));
    static final MethodHandle ROUTER_OPEN = bind("dtl_router_open", FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, PTR, PTR, PTR));
    static final MethodHandle EXECUTE = bind("dtl_execute_with_options", call());
    static final MethodHandle DECIDE = bind("dtl_decide_with_options", call());
    static final MethodHandle ROUTER_EXECUTE = bind("dtl_router_execute_with_options", call());
    static final MethodHandle CLOSE = bind("dtl_close", FunctionDescriptor.ofVoid(PTR));
    static final MethodHandle ROUTER_CLOSE = bind("dtl_router_close", FunctionDescriptor.ofVoid(PTR));
    static final MethodHandle FREE = bind("dtl_free", FunctionDescriptor.ofVoid(PTR));

    private Native() {}

    private static FunctionDescriptor call() {
        return FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, PTR, PTR, PTR, PTR, PTR);
    }

    private static MethodHandle bind(String name, FunctionDescriptor fd) {
        MemorySegment symbol = LOOKUP.find(name).orElseThrow(() ->
            new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Native library lacks symbol " + name));
        return LINKER.downcallHandle(symbol, fd);
    }

    private static SymbolLookup load() {
        String os = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
        String arch = System.getProperty("os.arch", "").toLowerCase(Locale.ROOT);
        if (!os.contains("linux") || !(arch.equals("amd64") || arch.equals("x86_64")))
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM,
                "ducktape-laya supports Linux x86-64 only (found " + os + "/" + arch + ")");
        Arena global = Arena.global();
        String path = System.getProperty("ducktape.laya.library");
        if (path != null && !path.isEmpty()) return open(Path.of(path), global);
        String dir = System.getenv("DUCKTAPE_LAYA_LIB_DIR");
        if (dir != null && !dir.isEmpty()) return open(Path.of(dir, LIB), global);
        Path bundled = extractBundled();
        if (bundled != null) return open(bundled, global);
        RuntimeException last = null;
        for (String name : new String[] {LIB + ".0", System.mapLibraryName("ducktape_laya")}) {
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

    private static Path extractBundled() {
        try (InputStream in = Native.class.getResourceAsStream(BUNDLED)) {
            if (in == null) return null;
            Path dir = Files.createTempDirectory("ducktape-laya");
            Path file = dir.resolve(LIB);
            Files.copy(in, file);
            file.toFile().deleteOnExit();
            dir.toFile().deleteOnExit();
            return file;
        } catch (IOException e) {
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot extract bundled library: " + e.getMessage(), e);
        }
    }

    static void free(MemorySegment buffer) {
        try {
            FREE.invokeExact(buffer);
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
