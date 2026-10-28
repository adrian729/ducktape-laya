"""ctypes declarations for the ABI 2 C interface in ducktape_laya.h."""
import ctypes
import json
import os
import pathlib
import platform
import sys

from ._errors import LayaLoadError

c_char_p_p = ctypes.POINTER(ctypes.c_void_p)
EVENT_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_void_p)
CANCEL_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)


class CallOptions(ctypes.Structure):
    _fields_ = [('struct_size', ctypes.c_uint32), ('reserved', ctypes.c_uint32),
                ('request_id', ctypes.c_uint64), ('timeout_ms', ctypes.c_uint64),
                ('on_event', EVENT_CALLBACK), ('event_user_data', ctypes.c_void_p),
                ('should_cancel', CANCEL_CALLBACK), ('cancel_user_data', ctypes.c_void_p)]


# libducktape_laya.so (soname .so.1) on Linux, .dylib (install name .1.dylib) on macOS.
_NAME, _VERSIONED = (('libducktape_laya.dylib', 'libducktape_laya.1.dylib') if sys.platform == 'darwin'
                     else ('ducktape_laya.dll', 'ducktape_laya.dll') if sys.platform == 'win32'
                     else ('libducktape_laya.so', 'libducktape_laya.so.1'))

# Platforms where an installed library is tried when none is bundled. Wheels are released for Linux
# x86-64 and arm64, macOS arm64 and x86-64 and Windows x64; elsewhere the library is built from source.
_MACHINES = {'linux': ('x86_64', 'AMD64', 'aarch64', 'arm64'), 'darwin': ('arm64', 'x86_64'), 'win32': ('AMD64',)}
_RELEASED = ('wheels exist for Linux x86-64 and arm64, macOS arm64 and x86-64 and Windows x64; on other platforms '
             'build the library from source and set DUCKTAPE_LAYA_LIB_DIR')
# macOS never loads a bare name (dyld would search DYLD_LIBRARY_PATH, the rpaths and the current directory);
# installed libraries are loaded by absolute path from these directories: Homebrew's and the CMake install default.
_MACOS_DIRS = ('/opt/homebrew/lib', '/usr/local/lib')
# Why a library file that exists may fail to load on each platform.
_HINTS = {'linux': 'it needs glibc 2.38+ and libstdc++ with GLIBCXX_3.4.29', 'darwin': 'it needs macOS 14 or newer',
          'win32': 'it needs the Microsoft Visual C++ Redistributable'}
# LOAD_LIBRARY_SEARCH_DEFAULT_DIRS: the application directory, System32 and os.add_dll_directory()
# directories; never the current directory or PATH. ctypes uses it for a bare name since Python 3.8;
# passing it keeps the fallback independent of that default.
_WINDOWS_SAFE_SEARCH = 0x00001000


class _DlInfo(ctypes.Structure):
    _fields_ = [('dli_fname', ctypes.c_char_p), ('dli_fbase', ctypes.c_void_p),
                ('dli_sname', ctypes.c_char_p), ('dli_saddr', ctypes.c_void_p)]


def _verify_image(lib, expected):
    """macOS only: dyld searches DYLD_LIBRARY_PATH by leaf name even for an absolute path, so a library of
    the same name there replaces the one asked for. Ask dladdr which image provides dtl_abi_version and
    refuse unless it is the expected file."""
    libsystem = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    libsystem.dladdr.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DlInfo)]
    libsystem.dladdr.restype = ctypes.c_int
    info = _DlInfo()
    address = ctypes.cast(lib.dtl_abi_version, ctypes.c_void_p).value
    found = libsystem.dladdr(address, ctypes.byref(info)) and info.dli_fname
    loaded = os.fsdecode(info.dli_fname) if found else None
    if loaded is None or os.path.realpath(loaded) != os.path.realpath(expected):
        raise LayaLoadError(f'Refusing the native ducktape-laya library: {expected} was requested but dtl_abi_version '
                            f'comes from {loaded}; DYLD_LIBRARY_PATH may have replaced it. Unset DYLD_LIBRARY_PATH.')


def _load_path(cdll, path, plat):
    lib = cdll(path)
    if plat == 'darwin':
        _verify_image(lib, path)
    return lib


def _open(cdll=ctypes.CDLL, machine=None, plat=None, bundled=None):
    plat = plat or sys.platform
    # An explicit choice is honoured or fails; never a silent fallback. Both variables must be absolute
    # (a relative path would depend on the working directory).
    for variable, directory in (('DUCKTAPE_LAYA_LIBRARY', False), ('DUCKTAPE_LAYA_LIB_DIR', True)):
        explicit = os.environ.get(variable)
        if not explicit:
            continue
        path = pathlib.Path(explicit, _NAME) if directory else pathlib.Path(explicit)
        if not os.path.isabs(explicit):
            raise LayaLoadError(f'{variable} must be an absolute path, got {explicit}')
        if not path.is_file():  # also stops macOS dyld from falling back to DYLD_LIBRARY_PATH
            raise LayaLoadError(f'{variable} is set but {path} does not exist')
        try:
            return _load_path(cdll, str(path), plat)
        except LayaLoadError:  # a library dyld replaced (also an OSError)
            raise
        except OSError as error:
            raise LayaLoadError(f'{variable} is set but {path} could not be loaded: {error}') from error
    target, is_bundled = _target(machine, plat, bundled)
    if is_bundled or plat == 'darwin':
        what = 'The bundled library' if is_bundled else 'The installed library'
        try:
            return _load_path(cdll, target, plat)
        except LayaLoadError:
            raise
        except OSError as error:
            raise LayaLoadError(f'{what} {target} was found but could not be loaded: {error} '
                                f'({_HINTS.get(plat, "it was built for another platform")})') from error
    try:
        return cdll(target, winmode=_WINDOWS_SAFE_SEARCH) if plat == 'win32' else cdll(target)
    except OSError as error:
        raise LayaLoadError(f'Native ducktape-laya library not found: this package bundles none and the system loader '
                            f'could not load {target} ({error}). Install a wheel for this platform ({_RELEASED}).') from error


def _target(machine=None, plat=None, bundled=None):
    """What to load without an override, and whether it is the bundled library: the wheel's bundled
    library when present (the wheel tag already matched this interpreter, and platform.machine() reports
    the OS architecture, which differs for x64 Python under emulation on Windows ARM64), else the soname
    through the system loader on a platform in _MACHINES; any other platform is an error. On macOS the
    fallback is an absolute path in _MACOS_DIRS, never a bare name."""
    bundled = bundled or pathlib.Path(__file__).with_name('_native') / _NAME
    if bundled.is_file():
        return str(bundled), True
    machine, plat = machine or platform.machine(), plat or sys.platform
    if machine not in _MACHINES.get(plat, ()):
        raise LayaLoadError(f'Unsupported platform {plat}/{machine}: ducktape-laya {_RELEASED}')
    if plat == 'darwin':
        for directory in _MACOS_DIRS:
            for name in (_VERSIONED, _NAME):
                if os.path.isfile(os.path.join(directory, name)):
                    return os.path.join(directory, name), False
        raise LayaLoadError(f'Native ducktape-laya library not found: this package bundles none and neither '
                            f'{" nor ".join(_MACOS_DIRS)} holds {_VERSIONED} or {_NAME}. '
                            f'Install a wheel for this platform ({_RELEASED}).')
    return _VERSIONED, False


def check_version(info_json, expected):
    """Refuse a library of another package version than this package (for example one the loader found
    elsewhere); ``info_json`` is the text ``dtl_info(NULL)`` returns."""
    try:
        version = json.loads(info_json).get('package_version')
    except (ValueError, AttributeError):
        version = None
    if version != expected:
        raise LayaLoadError(f'The native ducktape-laya library is version {version}, but this package is {expected}; '
                            f'another copy of the library was loaded instead of the one shipped with the package')


def load(expected_version):
    lib = _open()
    handle = ctypes.c_void_p
    status = ctypes.c_int32
    signatures = {
        'dtl_abi_version': (ctypes.c_uint32, []),
        'dtl_default_call_options': (CallOptions, []),
        'dtl_info': (status, [handle, c_char_p_p, c_char_p_p]),
        'dtl_open': (status, [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(handle), c_char_p_p]),
        'dtl_open_router': (status, [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(handle), c_char_p_p]),
        'dtl_execute': (status, [handle, ctypes.c_char_p, ctypes.POINTER(CallOptions), c_char_p_p, c_char_p_p]),
        'dtl_close': (None, [handle]),
        'dtl_free': (None, [ctypes.c_void_p]),
        'dtl_cancel_flag': (ctypes.c_uint32, [ctypes.c_void_p]),
    }
    for name, (restype, argtypes) in signatures.items():
        try:
            function = getattr(lib, name)
        except AttributeError:
            raise LayaLoadError(f'Native ducktape-laya library {lib._name} lacks {name}; it is older than this package') from None
        function.restype, function.argtypes = restype, argtypes
    if lib.dtl_abi_version() != 2:
        raise LayaLoadError(f'Native ducktape-laya ABI {lib.dtl_abi_version()} is not supported (expected 2)')
    result, error = ctypes.c_void_p(), ctypes.c_void_p()
    status = lib.dtl_info(None, ctypes.byref(result), ctypes.byref(error))
    text = ctypes.string_at(result.value).decode('utf-8', 'replace') if result.value else None
    for pointer in (result, error):
        if pointer.value:
            lib.dtl_free(pointer)
    if status or text is None:
        raise LayaLoadError(f'Native ducktape-laya library {lib._name} failed its version query (status {status})')
    check_version(text, expected_version)
    return lib
