// The addon imports Node-API from "node.exe" (node.lib), delay-loaded. Resolve
// that name to the running executable, so the addon also loads in hosts with a
// different name that export Node-API, such as Electron (node-gyp does the same).
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <delayimp.h>
#include <string.h>

static FARPROC WINAPI load_host_executable(unsigned event, PDelayLoadInfo info) {
    if (event != dliNotePreLoadLibrary || _stricmp(info->szDll, "node.exe") != 0) return nullptr;
    return reinterpret_cast<FARPROC>(GetModuleHandleW(nullptr));
}

decltype(__pfnDliNotifyHook2) __pfnDliNotifyHook2 = load_host_executable;
#endif
