package io.github.adrian729.laya;

import java.io.IOException;
import java.io.InputStream;
import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.nio.file.*;
import java.nio.file.attribute.AclEntry;
import java.nio.file.attribute.AclEntryFlag;
import java.nio.file.attribute.AclEntryPermission;
import java.nio.file.attribute.AclEntryType;
import java.nio.file.attribute.AclFileAttributeView;
import java.nio.file.attribute.FileAttribute;
import java.nio.file.attribute.PosixFilePermissions;
import java.nio.file.attribute.UserPrincipal;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.Collections;
import java.util.EnumSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** Locates libducktape_laya and binds its C ABI. */
final class Native {
    /** libducktape_laya.so on Linux, libducktape_laya.dylib on macOS, ducktape_laya.dll on Windows. */
    static final String LIB = System.mapLibraryName("ducktape_laya");
    /** Package platform of a JVM's os.name and os.arch (the jar's native/&lt;platform&gt;/ directory),
     *  or null where no package exists: linux-x64, linux-arm64, macos-arm64, macos-x64 or windows-x64. */
    static String platform(String os, String arch) {
        boolean x64 = arch.equals("amd64") || arch.equals("x86_64");
        boolean arm64 = arch.equals("aarch64") || arch.equals("arm64");
        if (os.contains("linux") && (x64 || arm64)) return x64 ? "linux-x64" : "linux-arm64";
        if ((os.contains("mac") || os.contains("darwin")) && (x64 || arm64)) return x64 ? "macos-x64" : "macos-arm64";
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

    /** Released packages; any other platform needs a library built from source. */
    static final String RELEASED = "packages exist for Linux x86-64 and arm64, macOS arm64 and x86-64 and Windows x64; on other platforms build the "
        + "library from source and set -Dducktape.laya.library or DUCKTAPE_LAYA_LIB_DIR";

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
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Unsupported platform " + os + "/" + arch + ": ducktape-laya " + RELEASED);
        if (platform.startsWith("linux") && isMusl()) {
            // The bundled library needs glibc; only a musl build on the loader path can load.
            SymbolLookup system = systemLibrary(platform, global);
            if (system != null) return system;
            throw muslUnsupported();
        }
        SymbolLookup bundled = loadBundled("/native/" + platform + "/" + LIB, platform, global);
        if (bundled != null) return bundled;
        SymbolLookup system = systemLibrary(platform, global);
        if (system != null) return system;
        throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "The ducktape-laya jar bundles no library for " + platform
            + " and " + systemSearch(platform) + "; set -Dducktape.laya.library or DUCKTAPE_LAYA_LIB_DIR");
    }

    /** Where {@link #systemLibrary} looks, for error messages. */
    private static String systemSearch(String platform) {
        if (platform.startsWith("linux")) return "the dynamic loader found no libducktape_laya.so.1 or libducktape_laya.so";
        if (platform.startsWith("macos")) return "no libducktape_laya.1.dylib or libducktape_laya.dylib is in " + MACOS_LIBRARY_DIRS;
        return "Windows has no system location for it";
    }

    /** Library directories searched on macOS, by absolute path: Homebrew's and the CMake install default. */
    static final List<Path> MACOS_LIBRARY_DIRS = List.of(Path.of("/opt/homebrew/lib"), Path.of("/usr/local/lib"));

    /** An installed library, or null. Linux asks the dynamic loader for the soname, then the plain name
     *  (LD_LIBRARY_PATH, the loader cache and the system directories; never the current directory).
     *  macOS loads by absolute path from {@link #MACOS_LIBRARY_DIRS} only, since a bare name would make dyld
     *  search further. Windows has no such location, and a bare name would search the current directory and
     *  PATH, so it has no fallback. */
    private static SymbolLookup systemLibrary(String platform, Arena arena) {
        String versioned = LIB.endsWith(".dylib") ? "libducktape_laya.1.dylib" : LIB + ".1";
        if (platform.startsWith("linux")) {
            for (String name : new String[] {versioned, LIB}) {
                try {
                    return SymbolLookup.libraryLookup(name, arena);
                } catch (RuntimeException e) {
                    // not on the loader path
                }
            }
        } else if (platform.startsWith("macos")) {
            for (Path dir : MACOS_LIBRARY_DIRS)
                for (String name : new String[] {versioned, LIB}) {
                    Path candidate = dir.resolve(name);
                    if (Files.isRegularFile(candidate)) return open(candidate, arena);
                }
        }
        return null;
    }

    /** Why a library file that exists may fail to load on this platform. */
    static String loadHint(String platform) {
        if (platform.startsWith("linux")) return "it needs glibc 2.38+ and libstdc++ with GLIBCXX_3.4.29";
        if (platform.startsWith("windows")) return "it needs the Microsoft Visual C++ Redistributable";
        return "it needs macOS 14 or newer";
    }

    static LayaException muslUnsupported() {
        return new LayaException(LayaException.UNSUPPORTED_PLATFORM,
            "The bundled ducktape-laya library needs glibc, and this looks like a musl system (for example Alpine), "
            + "and no musl build of libducktape_laya.so.1 is on the loader path; use a glibc-based image, or set "
            + "-Dducktape.laya.library or DUCKTAPE_LAYA_LIB_DIR to a musl build");
    }

    /** True on a musl system: a musl dynamic loader is present and no glibc one is. */
    static boolean isMusl() {
        return isMusl(Path.of("/lib"), Path.of("/lib64"));
    }

    /** True when the directories hold a musl dynamic loader and no glibc one (tests pass their own). */
    static boolean isMusl(Path... dirs) {
        boolean musl = false, glibc = false;
        for (Path d : dirs) {
            try (var names = Files.list(d)) {
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
        SymbolLookup lookup;
        try {
            lookup = SymbolLookup.libraryLookup(path, arena);
        } catch (RuntimeException e) {
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot load " + path + ": " + e.getMessage(), e);
        }
        requireImage(lookup, path, arena);
        return lookup;
    }

    /** macOS only: dyld searches DYLD_LIBRARY_PATH by leaf name even for an absolute path, so a library of
     *  the same name there replaces the one asked for. Asks dladdr which image provides dtl_abi_version and
     *  refuses (status 7) unless it is the expected file. */
    static void requireImage(SymbolLookup lookup, Path expected, Arena arena) {
        if (!LIB.endsWith(".dylib")) return;
        String loaded = null;
        try {
            MemorySegment symbol = lookup.find("dtl_abi_version").orElseThrow(() -> incompatible("lacks symbol dtl_abi_version"));
            SymbolLookup system = LINKER.defaultLookup();
            MemorySegment dladdr = system.find("dladdr").orElseGet(
                () -> SymbolLookup.libraryLookup("/usr/lib/libSystem.B.dylib", arena).find("dladdr").orElseThrow());
            MethodHandle handle = LINKER.downcallHandle(dladdr, FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, PTR));
            try (Arena local = Arena.ofConfined()) {
                // Dl_info: dli_fname, dli_fbase, dli_sname, dli_saddr.
                MemorySegment info = local.allocate(32, 8);
                int found = (int) handle.invokeExact(symbol, info);
                MemorySegment name = info.get(PTR, 0);
                if (found != 0 && name.address() != 0) loaded = name.reinterpret(Long.MAX_VALUE).getString(0);
            }
        } catch (LayaException e) {
            throw e;
        } catch (Throwable t) {
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot check which file provides " + expected + ": " + t, t);
        }
        if (loaded == null || !realPath(Path.of(loaded)).equals(realPath(expected)))
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Refusing the ducktape-laya native library: " + expected
                + " was requested but dtl_abi_version comes from " + loaded + "; DYLD_LIBRARY_PATH may have replaced it. Unset DYLD_LIBRARY_PATH.");
    }

    private static Path realPath(Path path) {
        try {
            return path.toRealPath();
        } catch (IOException | RuntimeException e) {
            return path.toAbsolutePath().normalize();
        }
    }

    /** Extracts the bundled library and loads it. On Linux and macOS the copy is deleted as soon as
     *  it is loaded (unlinking a mapped library is safe), so nothing is left behind even if the JVM
     *  is killed. A bundled library that is present but cannot be loaded is an error that names it. */
    private static SymbolLookup loadBundled(String resource, String platform, Arena arena) {
        byte[] bytes;
        try (InputStream in = Native.class.getResourceAsStream(resource)) {
            if (in == null) return null;
            bytes = in.readAllBytes();
        } catch (IOException e) {
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot read bundled library: " + e.getMessage(), e);
        }
        Path dir = null;
        Path file;
        try {
            if (platform.startsWith("windows")) {
                file = extractWindows(bytes);
            } else {
                dir = Files.createTempDirectory(tempBase(), "ducktape-laya");
                file = dir.resolve(platform.startsWith("macos") ? uniqueLeaf(bytes) : LIB);
                Files.write(file, bytes);
            }
        } catch (IOException e) {
            if (dir != null) deleteTree(dir);
            throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Cannot extract bundled library: " + e.getMessage(), e);
        }
        try {
            SymbolLookup lookup;
            try {
                lookup = SymbolLookup.libraryLookup(file, arena);
            } catch (RuntimeException e) {
                throw new LayaException(LayaException.UNSUPPORTED_PLATFORM, "Found the bundled library " + resource
                    + " but it could not be loaded (extracted to " + file + "): " + e.getMessage() + " (" + loadHint(platform) + ")", e);
            }
            requireImage(lookup, file, arena);
            return lookup;
        } finally {
            if (dir != null) deleteTree(dir);
        }
    }

    /** The macOS extraction leaf: libducktape_laya-&lt;first 16 hex of the content's sha256&gt;.dylib, so that a
     *  file of the usual name on DYLD_LIBRARY_PATH cannot shadow the extracted copy. */
    static String uniqueLeaf(byte[] bytes) {
        try {
            String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes), 0, 8);
            return "libducktape_laya-" + hash + ".dylib";
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static void deleteTree(Path dir) {
        try {
            try (var names = Files.list(dir)) {
                for (Path entry : (Iterable<Path>) names::iterator) Files.deleteIfExists(entry);
            }
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
        return extractWindows(bytes, System.getProperty("ducktape.laya.tmpdir"), System.getenv("LOCALAPPDATA"),
            Path.of(System.getProperty("java.io.tmpdir")));
    }

    /** {@link #extractWindows(byte[])} with its inputs: the ducktape.laya.tmpdir property, the
     *  LOCALAPPDATA variable and java.io.tmpdir. An explicit tmpdir that cannot be used safely is an
     *  error; an unusable %LOCALAPPDATA% location falls back to a fresh owner-only directory. */
    static Path extractWindows(byte[] bytes, String tmpdir, String localAppData, Path javaTmp) throws IOException {
        boolean explicit = tmpdir != null && !tmpdir.isEmpty();
        Path base = explicit ? Path.of(tmpdir)
            : localAppData != null && !localAppData.isEmpty() ? Path.of(localAppData, "ducktape-laya") : null;
        if (base != null) {
            try {
                return extractShared(base, bytes);
            } catch (IOException e) {
                if (explicit) throw e;
                // Unusable or not ours: fall through to a private directory.
            }
        }
        UserPrincipal user = currentUser(javaTmp);
        Path dir = Files.createTempDirectory(javaTmp, "ducktape-laya", ownerOnly(javaTmp, user));
        restrictToOwner(dir, user);
        Path file = dir.resolve(LIB);
        Files.write(file, bytes);
        // Hooks run in reverse registration order: the directory must be registered first.
        dir.toFile().deleteOnExit();
        file.toFile().deleteOnExit();
        return file;
    }

    /** The name of the shared directory for bytes and user: a hash of the content, then a short hash
     *  of the owner's name. Processes of one account that create files under different owners (an
     *  administrator's elevated processes own their files as the Administrators group) each get their
     *  own directory instead of refusing each other's. */
    static String sharedName(byte[] bytes, UserPrincipal user) {
        try {
            MessageDigest sha = MessageDigest.getInstance("SHA-256");
            String content = HexFormat.of().formatHex(sha.digest(bytes), 0, 16);
            String owner = HexFormat.of().formatHex(sha.digest(user.getName().getBytes(java.nio.charset.StandardCharsets.UTF_8)), 0, 4);
            return content + "-" + owner;
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Shares one copy per content and owner in base/{@link #sharedName}/. The directory is created
     *  with an owner-only ACL (owner-only permissions where there are no ACLs); an existing one must
     *  belong to the current user, or the call fails. Where the file system has ACLs (Windows), the
     *  directory and the copy are made owner-only and protected from inheriting ACL entries before the
     *  copy is checked, so an ACL that lets another account write them (for example inherited from a
     *  shared base directory) is replaced. Then every entry other than the copy and in-progress
     *  ducktape_laya*.part files is deleted (or the call fails), and a copy that is not the current
     *  user's or differs from the bundled bytes is replaced. */
    static Path extractShared(Path base, byte[] bytes) throws IOException {
        Files.createDirectories(base);
        UserPrincipal user = currentUser(base);
        Path dir = base.resolve(sharedName(bytes, user));
        try {
            Files.createDirectory(dir, ownerOnly(base, user));
        } catch (FileAlreadyExistsException e) {
            // Created earlier (by this user, checked below).
        }
        if (!Files.isDirectory(dir, LinkOption.NOFOLLOW_LINKS) || !ownedBy(dir, user))
            throw new IOException(dir + " is not a directory owned by the current user");
        secure(dir, user);
        removeStrays(dir);
        Path file = dir.resolve(LIB);
        if (Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS) && ownedBy(file, user)) {
            secure(file, user);
            if (sameContent(file, bytes)) return file;
        }
        Path part = Files.createTempFile(dir, "ducktape_laya", ".part");
        try {
            Files.write(part, bytes);
            Files.move(part, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            // Another process of this user may have put the same copy in place and loaded it first.
            if (!(ownedBy(file, user) && sameContent(file, bytes))) throw e;
        } finally {
            Files.deleteIfExists(part);
        }
        if (!ownedBy(file, user)) throw new IOException(file + " is not owned by the current user");
        secure(file, user);
        return file;
    }

    /** Deletes every entry of the shared directory but the library and in-progress copies, which
     *  another process of this user may be writing. An entry that cannot be deleted (for example a
     *  non-empty directory) fails the call. */
    private static void removeStrays(Path dir) throws IOException {
        List<Path> strays;
        try (var entries = Files.list(dir)) {
            strays = entries.filter(entry -> {
                String name = entry.getFileName().toString();
                return !name.equals(LIB) && !(name.startsWith("ducktape_laya") && name.endsWith(".part"));
            }).toList();
        }
        for (Path stray : strays) {
            try {
                Files.delete(stray);
            } catch (IOException e) {
                throw new IOException(dir + " holds an unexpected entry that could not be removed: " + stray, e);
            }
        }
    }

    /** The owner of the files this process creates (on Windows the token's default owner, which for
     *  an elevated administrator may be the Administrators group), read from a probe file in dir. */
    private static UserPrincipal currentUser(Path dir) throws IOException {
        Path probe = Files.createTempFile(dir, "owner", ".probe");
        try {
            return Files.getOwner(probe);
        } finally {
            Files.deleteIfExists(probe);
        }
    }

    private static boolean ownedBy(Path path, UserPrincipal user) throws IOException {
        return Files.getOwner(path, LinkOption.NOFOLLOW_LINKS).equals(user);
    }

    /** Permissions that let an account change a file or directory, its contents or its ACL. */
    private static final Set<AclEntryPermission> MODIFY = EnumSet.of(AclEntryPermission.WRITE_DATA,
        AclEntryPermission.APPEND_DATA, AclEntryPermission.WRITE_NAMED_ATTRS, AclEntryPermission.WRITE_ATTRIBUTES,
        AclEntryPermission.DELETE, AclEntryPermission.DELETE_CHILD, AclEntryPermission.WRITE_ACL, AclEntryPermission.WRITE_OWNER);

    /** True when an ACL entry that applies to path lets an account other than user modify it
     *  (false where the file system has no ACLs). */
    static boolean writableByOthers(Path path, UserPrincipal user) throws IOException {
        AclFileAttributeView view = Files.getFileAttributeView(path, AclFileAttributeView.class, LinkOption.NOFOLLOW_LINKS);
        if (view == null) return false;
        for (AclEntry entry : view.getAcl()) {
            if (entry.type() == AclEntryType.ALLOW && !entry.flags().contains(AclEntryFlag.INHERIT_ONLY)
                && !entry.principal().equals(user) && !Collections.disjoint(entry.permissions(), MODIFY))
                return true;
        }
        return false;
    }

    /** Makes path owner-only if another account could modify it, and protects a directory's ACL from
     *  inheritance (Windows), so ACL entries later made inheritable on its parent do not reach it. */
    private static void secure(Path path, UserPrincipal user) throws IOException {
        if (writableByOthers(path, user)) restrictToOwner(path, user);
        else if (Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS)) protectAcl(path);
    }

    /** The ACL entry that grants user everything; on a directory it is inherited by its contents. */
    private static AclEntry ownerEntry(UserPrincipal user, boolean directory) {
        AclEntry.Builder entry = AclEntry.newBuilder().setType(AclEntryType.ALLOW).setPrincipal(user)
            .setPermissions(EnumSet.allOf(AclEntryPermission.class));
        if (directory) entry.setFlags(AclEntryFlag.FILE_INHERIT, AclEntryFlag.DIRECTORY_INHERIT);
        return entry.build();
    }

    /** The attribute that creates a directory owner-only from the start: an ACL granting only user
     *  where the file system has ACLs (Windows), else POSIX mode 700. */
    private static FileAttribute<?> ownerOnly(Path base, UserPrincipal user) {
        if (base.getFileSystem().supportedFileAttributeViews().contains("acl")) {
            List<AclEntry> acl = List.of(ownerEntry(user, true));
            return new FileAttribute<List<AclEntry>>() {
                @Override public String name() { return "acl:acl"; }
                @Override public List<AclEntry> value() { return acl; }
            };
        }
        return PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------"));
    }

    /** Replaces the ACL of a file or directory by one that grants only user, the owner (Windows), and
     *  protects a directory's ACL from inheritance. */
    private static void restrictToOwner(Path path, UserPrincipal user) throws IOException {
        AclFileAttributeView view = Files.getFileAttributeView(path, AclFileAttributeView.class, LinkOption.NOFOLLOW_LINKS);
        if (view == null) return;
        boolean directory = Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS);
        view.setAcl(List.of(ownerEntry(user, directory)));
        if (directory) protectAcl(path);
        if (writableByOthers(path, user)) throw new IOException(path + " could not be made owner-only");
    }

    private static final int SE_FILE_OBJECT = 1, DACL_SECURITY_INFORMATION = 4, PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000;

    /** Marks the DACL of path protected from inheritance (Windows; elsewhere nothing to do). The JDK
     *  writes an ACL with SetFileSecurity and never sets SE_DACL_PROTECTED, so an ACL entry made
     *  inheritable on the parent later (by its owner or an administrator) would propagate into the
     *  directory. GetNamedSecurityInfoW and SetNamedSecurityInfoW (advapi32, a Windows KnownDLL) rewrite
     *  the same DACL with PROTECTED_DACL_SECURITY_INFORMATION, which drops inherited entries. */
    static void protectAcl(Path path) throws IOException {
        if (Files.getFileAttributeView(path, AclFileAttributeView.class, LinkOption.NOFOLLOW_LINKS) == null) return;
        try (Arena arena = Arena.ofConfined()) {
            SymbolLookup advapi = SymbolLookup.libraryLookup("advapi32", arena);
            SymbolLookup kernel = SymbolLookup.libraryLookup("kernel32", arena);
            MethodHandle get = LINKER.downcallHandle(advapi.find("GetNamedSecurityInfoW").orElseThrow(),
                FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, ValueLayout.JAVA_INT, ValueLayout.JAVA_INT, PTR, PTR, PTR, PTR, PTR));
            MethodHandle set = LINKER.downcallHandle(advapi.find("SetNamedSecurityInfoW").orElseThrow(),
                FunctionDescriptor.of(ValueLayout.JAVA_INT, PTR, ValueLayout.JAVA_INT, ValueLayout.JAVA_INT, PTR, PTR, PTR, PTR));
            MethodHandle localFree = LINKER.downcallHandle(kernel.find("LocalFree").orElseThrow(), FunctionDescriptor.of(PTR, PTR));
            MemorySegment name = arena.allocateFrom(path.toAbsolutePath().toString(), java.nio.charset.StandardCharsets.UTF_16LE);
            MemorySegment dacl = arena.allocate(PTR), descriptor = arena.allocate(PTR);
            int status = (int) get.invokeExact(name, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, MemorySegment.NULL, MemorySegment.NULL,
                dacl, MemorySegment.NULL, descriptor);
            if (status != 0) throw new IOException("GetNamedSecurityInfoW(" + path + ") failed: error " + status);
            try {
                status = (int) set.invokeExact(name, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                    MemorySegment.NULL, MemorySegment.NULL, dacl.get(PTR, 0), MemorySegment.NULL);
            } finally {
                MemorySegment ignored = (MemorySegment) localFree.invokeExact(descriptor.get(PTR, 0));
            }
            if (status != 0) throw new IOException("SetNamedSecurityInfoW(" + path + ") failed: error " + status);
        } catch (IOException | RuntimeException | Error e) {
            throw e;
        } catch (Throwable t) {
            throw new IOException("Cannot protect the ACL of " + path + ": " + t, t);
        }
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
