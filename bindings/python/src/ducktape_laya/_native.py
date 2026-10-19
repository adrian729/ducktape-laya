"""ctypes declarations for the ABI v1 C interface in ducktape_laya.h."""
import ctypes
import os
import pathlib
import platform
import sys

c_char_p_p = ctypes.POINTER(ctypes.c_void_p)
EVENT_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_void_p)
CANCEL_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)


class Options(ctypes.Structure):
    _fields_ = [('struct_size', ctypes.c_uint32), ('abi_version', ctypes.c_uint32),
                ('threads', ctypes.c_uint32), ('reserved', ctypes.c_uint32)]


class Observer(ctypes.Structure):
    _fields_ = [('request_id', ctypes.c_uint64), ('callback', EVENT_CALLBACK),
                ('user_data', ctypes.c_void_p)]


class CallOptions(ctypes.Structure):
    _fields_ = [('struct_size', ctypes.c_uint32), ('reserved', ctypes.c_uint32),
                ('timeout_ms', ctypes.c_uint64), ('should_cancel', CANCEL_CALLBACK),
                ('user_data', ctypes.c_void_p)]


def _open():
    explicit = os.environ.get('DUCKTAPE_LAYA_LIB_DIR')
    if explicit:  # an explicit choice is honoured or fails; never a silent fallback
        path = pathlib.Path(explicit, 'libducktape_laya.so')
        try:
            return ctypes.CDLL(str(path))
        except OSError as error:
            raise OSError(f'DUCKTAPE_LAYA_LIB_DIR is set but {path} could not be loaded: {error}') from None
    if sys.platform != 'linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise OSError(f'Unsupported platform {sys.platform}/{platform.machine()}; ducktape-laya ships Linux x86-64 only')
    bundled = pathlib.Path(__file__).with_name('_native') / 'libducktape_laya.so'
    try:
        return ctypes.CDLL(str(bundled) if bundled.is_file() else 'libducktape_laya.so.0')
    except OSError as error:
        raise OSError('Native ducktape-laya library not found. Install a wheel that bundles it, '
                      f'or set DUCKTAPE_LAYA_LIB_DIR to its library directory: {error}') from None


def load():
    lib = _open()
    handle = ctypes.c_void_p
    status = ctypes.c_int32
    signatures = {
        'dtl_abi_version': (ctypes.c_uint32, []),
        'dtl_default_options': (Options, []),
        'dtl_default_call_options': (CallOptions, []),
        'dtl_info': (status, [handle, c_char_p_p, c_char_p_p]),
        'dtl_open': (status, [ctypes.c_char_p, ctypes.POINTER(Options), ctypes.POINTER(handle), c_char_p_p]),
        'dtl_router_open': (status, [ctypes.c_char_p, ctypes.POINTER(Options), ctypes.POINTER(handle), c_char_p_p]),
        'dtl_close': (None, [handle]),
        'dtl_router_close': (None, [handle]),
        'dtl_free': (None, [ctypes.c_void_p]),
    }
    for name in ['dtl_execute_with_options', 'dtl_decide_with_options', 'dtl_router_execute_with_options']:
        signatures[name] = (status, [handle, ctypes.c_char_p, ctypes.POINTER(Observer),
                                     ctypes.POINTER(CallOptions), c_char_p_p, c_char_p_p])
    signatures['dtl_cancel_flag'] = (ctypes.c_uint32, [ctypes.c_void_p])
    for name, (restype, argtypes) in signatures.items():
        try:
            function = getattr(lib, name)
        except AttributeError:
            raise OSError(f'Native ducktape-laya library {lib._name} lacks {name}; it is older than this package') from None
        function.restype, function.argtypes = restype, argtypes
    if lib.dtl_abi_version() != 1:
        raise OSError(f'Native ducktape-laya ABI {lib.dtl_abi_version()} is not supported (expected 1)')
    return lib
