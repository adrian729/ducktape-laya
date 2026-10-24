"""ctypes declarations for the ABI 2 C interface in ducktape_laya.h."""
import ctypes
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

# Platforms where the system loader is tried when no library is bundled. Wheels are released for Linux
# x86-64 and arm64 and Windows x64; elsewhere the library is built from source.
_MACHINES = {'linux': ('x86_64', 'AMD64', 'aarch64', 'arm64'), 'darwin': ('arm64',), 'win32': ('AMD64',)}
_RELEASED = ('wheels exist for Linux x86-64 and arm64 and Windows x64; on other platforms build the library '
             'from source and set DUCKTAPE_LAYA_LIB_DIR')
# Why a library file that exists may fail to load on each platform.
_HINTS = {'linux': 'it needs glibc 2.38+ and libstdc++ with GLIBCXX_3.4.29', 'darwin': 'it needs macOS 14 or newer',
          'win32': 'it needs the Microsoft Visual C++ Redistributable'}
# LOAD_LIBRARY_SEARCH_DEFAULT_DIRS: the application directory, System32 and os.add_dll_directory()
# directories; never the current directory or PATH. ctypes uses it for a bare name since Python 3.8;
# passing it keeps the fallback independent of that default.
_WINDOWS_SAFE_SEARCH = 0x00001000


def _open(cdll=ctypes.CDLL, machine=None, plat=None, bundled=None):
    plat = plat or sys.platform
    explicit = os.environ.get('DUCKTAPE_LAYA_LIB_DIR')
    if explicit:  # an explicit choice is honoured or fails; never a silent fallback
        path = pathlib.Path(explicit, _NAME)
        # macOS dyld would otherwise fall back to DYLD_LIBRARY_PATH for a missing path.
        if not path.is_file():
            raise LayaLoadError(f'DUCKTAPE_LAYA_LIB_DIR is set but {path} does not exist')
        try:
            return cdll(str(path))
        except OSError as error:
            raise LayaLoadError(f'DUCKTAPE_LAYA_LIB_DIR is set but {path} could not be loaded: {error}') from error
    target, is_bundled = _target(machine, plat, bundled)
    if is_bundled:
        try:
            return cdll(target)
        except OSError as error:
            raise LayaLoadError(f'The bundled library {target} was found but could not be loaded: {error} '
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
    through the system loader on a platform in _MACHINES; any other platform is an error."""
    bundled = bundled or pathlib.Path(__file__).with_name('_native') / _NAME
    if bundled.is_file():
        return str(bundled), True
    machine, plat = machine or platform.machine(), plat or sys.platform
    if machine not in _MACHINES.get(plat, ()):
        raise LayaLoadError(f'Unsupported platform {plat}/{machine}: ducktape-laya {_RELEASED}')
    return _VERSIONED, False


def load():
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
    return lib
