"""ctypes declarations for the ABI 2 C interface in ducktape_laya.h."""
import ctypes
import os
import pathlib
import platform
import sys

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


def _open():
    explicit = os.environ.get('DUCKTAPE_LAYA_LIB_DIR')
    if explicit:  # an explicit choice is honoured or fails; never a silent fallback
        path = pathlib.Path(explicit, _NAME)
        try:
            # macOS dyld would otherwise fall back to DYLD_LIBRARY_PATH for a missing path.
            if not path.is_file():
                raise OSError('no such file')
            return ctypes.CDLL(str(path))
        except OSError as error:
            raise OSError(f'DUCKTAPE_LAYA_LIB_DIR is set but {path} could not be loaded: {error}') from None
    if sys.platform != 'linux' or platform.machine() not in ('x86_64', 'AMD64', 'aarch64', 'arm64'):
        raise OSError(f'Unsupported platform {sys.platform}/{platform.machine()}; ducktape-laya supports Linux x86-64 and arm64')
    bundled = pathlib.Path(__file__).with_name('_native') / _NAME
    try:
        return ctypes.CDLL(str(bundled) if bundled.is_file() else _VERSIONED)
    except OSError as error:
        raise OSError('Native ducktape-laya library not found. Install a wheel that bundles it, '
                      f'or set DUCKTAPE_LAYA_LIB_DIR to its library directory: {error}') from None


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
            raise OSError(f'Native ducktape-laya library {lib._name} lacks {name}; it is older than this package') from None
        function.restype, function.argtypes = restype, argtypes
    if lib.dtl_abi_version() != 2:
        raise OSError(f'Native ducktape-laya ABI {lib.dtl_abi_version()} is not supported (expected 2)')
    return lib
