package io.github.adrian729.laya;

import java.io.IOException;
import java.io.InputStream;
import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.nio.file.*;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.Locale;

/** Locates libducktape_laya and binds its C ABI. */
final class Native {
    /** libducktape_laya.so on Linux, libducktape_laya.dylib on macOS, ducktape_laya.dll on Windows. */
    static final String LIB = System.mapLibraryName("ducktape_laya");
    /** Package platform of a JVM's os.name and os.arch (the jar's native/&lt;platform&gt;/ directory),
     *  or null where no package exists: linux-x64, linux-arm64, macos-arm64 or windows-x64. */
    static String platform(String os, String arch) {
        boolean x64 = arch.equals("amd64") || arch.equals("x86_64");
        boolean arm64 = arch.equals("aarch64") || arch.equals("arm64");
        if (os.contains("linux") && (x64 || arm64)) return x64 ? "linux-x64" : "linux-arm64";
        if ((os.contains("mac") || os.contains("darwin")) && arm64) return "macos-arm64";
        if (os.contains("windows") && x64) return "windows-x64";
        return null;
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
        String platform = platform(os, arch);
        if (platform == null)
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM,
                "ducktape-laya supports Linux x86-64 and arm64, macOS arm64 and Windows x64 (found " + os + "/" + arch + ")");
        if (platform.startsWith("linux") && isMusl())
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM,
                "The bundled ducktape-laya library needs glibc, and this looks like a musl system (for example Alpine); "
                + "use a glibc-based image or set -Dducktape.laya.library to a musl build");
        SymbolLookup bundled = loadBundled("/native/" + platform + "/" + LIB, os.contains("windows"), global);
        if (bundled != null) return bundled;
        RuntimeException last = null;
        String versioned = LIB.endsWith(".dylib") ? "libducktape_laya.1.dylib" : LIB.endsWith(".dll") ? LIB : LIB + ".1";
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

    /** True on a musl system: a musl dynamic loader is present and no glibc one is. */
    static boolean isMusl() {
        boolean musl = false, glibc = false;
        for (String d : new String[] {"/lib", "/lib64"}) {
            try (var names = Files.list(Path.of(d))) {
                for (Path q : (Iterable<Path>) names::iterator) {
                    String n = q.getFileName().toString();
                    musl |= n.startsWith("ld-musl-");
                    glibc |= n.startsWith("ld-linux-");
                }
            } catch (IOException | RuntimeException ignored) {
                // directory absent
            }
        }
        return musl && !glibc;
    }

    private static SymbolLookup open(Path path, Arena arena) {
        // macOS dyld would otherwise fall back to DYLD_LIBRARY_PATH for a missing path.
        if (!Files.isRegularFile(path))
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot load " + path + ": no such file");
        try {
            return SymbolLookup.libraryLookup(path, arena);
        } catch (RuntimeException e) {
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot load " + path + ": " + e.getMessage(), e);
        }
    }

    /** Extracts the bundled library and loads it. On Linux and macOS the copy is deleted as soon as
     *  it is loaded (unlinking a mapped library is safe), so nothing is left behind even if the JVM
     *  is killed. */
    private static SymbolLookup loadBundled(String resource, boolean windows, Arena arena) {
        byte[] bytes;
        try (InputStream in = Native.class.getResourceAsStream(resource)) {
            if (in == null) return null;
            bytes = in.readAllBytes();
        } catch (IOException e) {
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot read bundled library: " + e.getMessage(), e);
        }
        Path dir = null;
        try {
            Path file;
            if (windows) {
                file = extractWindows(bytes);
            } else {
                dir = Files.createTempDirectory(tempBase(), "ducktape-laya");
                file = dir.resolve(LIB);
                Files.write(file, bytes);
            }
            return open(file, arena);
        } catch (IOException e) {
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot extract bundled library: " + e.getMessage(), e);
        } finally {
            if (dir != null) deleteTree(dir);
        }
    }

    private static void deleteTree(Path dir) {
        try {
            Files.deleteIfExists(dir.resolve(LIB));
            Files.deleteIfExists(dir);
        } catch (IOException | RuntimeException ignored) {
            // Best effort: the files are private to this user in a directory only this user can write.
        }
    }

    /** Base directory for extraction: -Dducktape.laya.tmpdir (for example when /tmp is mounted
     *  noexec) or java.io.tmpdir. */
    private static Path tempBase() {
        String p = System.getProperty("ducktape.laya.tmpdir");
        if (p != null && !p.isEmpty()) return Path.of(p);
        return Path.of(System.getProperty("java.io.tmpdir"));
    }

    /** Windows cannot delete a DLL while it is loaded, so a per-process copy would be left behind
     *  on every run. Instead every process of the user shares one copy per library content in a
     *  per-user directory (%LOCALAPPDATA%\ducktape-laya, or -Dducktape.laya.tmpdir) that other
     *  users cannot write to. Without such a location the library goes into a fresh, owner-only
     *  directory under java.io.tmpdir (never a predictable name in a shared directory). */
    private static Path extractWindows(byte[] bytes) throws IOException {
        String prop = System.getProperty("ducktape.laya.tmpdir");
        String local = System.getenv("LOCALAPPDATA");
        Path base = prop != null && !prop.isEmpty() ? Path.of(prop)
            : local != null && !local.isEmpty() ? Path.of(local, "ducktape-laya") : null;
        if (base != null) {
            try {
                return extractShared(base, bytes);
            } catch (IOException e) {
                if (prop != null && !prop.isEmpty()) throw e;
                // Unusable or not ours: fall through to a private directory.
            }
        }
        Path dir = Files.createTempDirectory(Path.of(System.getProperty("java.io.tmpdir")), "ducktape-laya");
        restrictToOwner(dir);
        Path file = dir.resolve(LIB);
        Files.write(file, bytes);
        // Hooks run in reverse registration order: the directory must be registered first.
        dir.toFile().deleteOnExit();
        file.toFile().deleteOnExit();
        return file;
    }

    /** Shares one copy per content under base, which must belong to the current user. */
    static Path extractShared(Path base, byte[] bytes) throws IOException {
        byte[] digest;
        try {
            digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        } catch (NoSuchAlgorithmException e) {
            throw new IOException(e);
        }
        Files.createDirectories(base);
        Path dir = base.resolve(HexFormat.of().formatHex(digest, 0, 16));
        Files.createDirectories(dir);
        requireOwn(base, dir);
        Path file = dir.resolve(LIB);
        if (sameContent(file, bytes)) {
            requireOwn(base, file);
            return file;
        }
        Path part = Files.createTempFile(dir, "ducktape_laya", ".part");
        try {
            Files.write(part, bytes);
            Files.move(part, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            // Another process may have put the same copy in place and loaded it first.
            if (!sameContent(file, bytes)) throw e;
        } finally {
            Files.deleteIfExists(part);
        }
        requireOwn(base, file);
        return file;
    }

    /** Refuses a path whose owner differs from the owner of the files this process creates. */
    private static void requireOwn(Path base, Path path) throws IOException {
        Path probe = Files.createTempFile(base, "owner", ".probe");
        try {
            if (!Files.getOwner(path, LinkOption.NOFOLLOW_LINKS).equals(Files.getOwner(probe)))
                throw new IOException(path + " is not owned by the current user");
        } finally {
            Files.deleteIfExists(probe);
        }
    }

    /** Replaces the ACL of a directory by one that grants only its owner (Windows). */
    private static void restrictToOwner(Path dir) throws IOException {
        java.nio.file.attribute.AclFileAttributeView view =
            Files.getFileAttributeView(dir, java.nio.file.attribute.AclFileAttributeView.class);
        if (view == null) return;
        java.nio.file.attribute.AclEntry entry = java.nio.file.attribute.AclEntry.newBuilder()
            .setType(java.nio.file.attribute.AclEntryType.ALLOW)
            .setPrincipal(view.getOwner())
            .setPermissions(java.util.EnumSet.allOf(java.nio.file.attribute.AclEntryPermission.class))
            .setFlags(java.nio.file.attribute.AclEntryFlag.FILE_INHERIT, java.nio.file.attribute.AclEntryFlag.DIRECTORY_INHERIT)
            .build();
        view.setAcl(java.util.List.of(entry));
    }

    private static boolean sameContent(Path file, byte[] bytes) throws IOException {
        return Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS) && Files.size(file) == bytes.length
            && Arrays.equals(Files.readAllBytes(file), bytes);
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
