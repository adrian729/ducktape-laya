cmake_minimum_required(VERSION 3.22)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/release/compatibility.json" DTL_RELEASE_RECORD)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/release/compatibility.json")
string(JSON DTL_VERSION GET "${DTL_RELEASE_RECORD}" package_version)
# The macOS packages promise macOS 14 (wheel tag macosx_14_0; floating-point std::to_chars needs
# macOS 13.3, and wheel tags name only major versions); scripts/package-native.py checks the binaries.
set(CMAKE_OSX_DEPLOYMENT_TARGET "14.0" CACHE STRING "Minimum macOS version")
project(ducktape_laya VERSION ${DTL_VERSION} LANGUAGES C CXX)
include(cmake/source_identity.cmake)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(EMSCRIPTEN)
  # The qualified browser profile uses ggml's WASM SIMD kernels, selected by processor.
  if(NOT CMAKE_SYSTEM_PROCESSOR STREQUAL "wasm32")
    message(FATAL_ERROR "Browser builds require -DEMSCRIPTEN_SYSTEM_PROCESSOR=wasm32 (ggml WASM kernels)")
  endif()
  set(LLAMA_WASM_MEM64 OFF CACHE BOOL "" FORCE)
  set(LLAMA_BUILD_HTML OFF CACHE BOOL "" FORCE)
  if(GGML_WEBGPU)
    add_compile_options(-msimd128 -fwasm-exceptions)
    add_link_options(-msimd128 -fwasm-exceptions)
  else()
    add_compile_options(-msimd128 -fexceptions)
    add_link_options(-msimd128 -fexceptions)
  endif()
  set(DTL_LIBRARY_KIND STATIC)
else()
  set(DTL_LIBRARY_KIND SHARED)
endif()
option(DUCKTAPE_BUILD_TESTS "Build native tests" ON)
set(DUCKTAPE_TEST_MODEL "" CACHE FILEPATH "Local Laya GGUF for integration tests")
set(DUCKTAPE_REAL_TEST_TIMEOUT 600 CACHE STRING "Per-test timeout in seconds for long real-model tests (raise for instrumented builds)")
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
foreach(opt COMMON TESTS TOOLS EXAMPLES SERVER APP MTMD)
  set(LLAMA_BUILD_${opt} OFF CACHE BOOL "" FORCE)
endforeach()
option(DUCKTAPE_BUILD_REFERENCE_PROBE "Build developer upstream-processing reference (never packaged)" OFF)
if(DUCKTAPE_BUILD_REFERENCE_PROBE)
  set(LLAMA_BUILD_COMMON ON CACHE BOOL "" FORCE)
  set(LLAMA_BUILD_MTMD ON CACHE BOOL "" FORCE)
  set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
  set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
  set(LLAMA_SUBPROCESS OFF CACHE BOOL "" FORCE)
endif()
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_BACKEND_DL OFF CACHE BOOL "" FORCE)
set(GGML_CUDA OFF CACHE BOOL "" FORCE)
set(GGML_METAL OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
# x86-64 profile: AVX2 baseline (AVX, AVX2, FMA, F16C, BMI2, SSE4.2), checked at open
# (src/platform.cpp). ggml's own defaults turn these off when SOURCE_DATE_EPOCH is set
# or when cross-compiling, which would silently change the numerical profile; pin them.
# They remain cache options: -DGGML_AVX2=OFF ... still selects the portable profile.
if(NOT EMSCRIPTEN AND CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64")
  foreach(feature AVX AVX2 BMI2 SSE42)
    set(GGML_${feature} ON CACHE BOOL "ggml: enable ${feature} (ducktape-laya profile)")
  endforeach()
  if(NOT MSVC) # MSVC-style builds have no GGML_FMA/GGML_F16C: /arch:AVX2 implies them
    set(GGML_FMA ON CACHE BOOL "ggml: enable FMA (ducktape-laya profile)")
    set(GGML_F16C ON CACHE BOOL "ggml: enable F16C (ducktape-laya profile)")
  endif()
endif()
# 64-bit Arm baseline: dot product and half precision (Graviton2+, Ampere Altra,
# Cortex-A76 / Raspberry Pi 5). The library checks them at open (src/platform.cpp).
if(NOT EMSCRIPTEN AND CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64|ARM64")
  set(GGML_CPU_ARM_ARCH "armv8.2-a+dotprod+fp16" CACHE STRING "Arm -march for ggml")
endif()
set(DUCKTAPE_LLAMA_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/llama.cpp" CACHE PATH "Pinned or isolated candidate llama.cpp checkout")
get_filename_component(UPSTREAM "${DUCKTAPE_LLAMA_SOURCE}" ABSOLUTE)
string(JSON DTL_APPROVED_RUNTIME_COMMIT GET "${DTL_RELEASE_RECORD}" runtime commit)
set(DUCKTAPE_LLAMA_REVISION "" CACHE STRING "Identity of an isolated runtime archive (set by upgrade tooling)")
if(DUCKTAPE_LLAMA_REVISION)
  set(DTL_RUNTIME_COMMIT "${DUCKTAPE_LLAMA_REVISION}")
elseif(UPSTREAM STREQUAL "${CMAKE_CURRENT_SOURCE_DIR}/third_party/llama.cpp")
  set(DTL_RUNTIME_COMMIT "${DTL_APPROVED_RUNTIME_COMMIT}")
else()
  set(DTL_RUNTIME_COMMIT "unverified-custom-source")
endif()
string(JSON DTL_PATCHES_JSON GET "${DTL_RELEASE_RECORD}" runtime patches)
# Normalize paths embedded by compiler diagnostics/debug metadata.
option(DUCKTAPE_REPRODUCIBLE_PATHS "Normalize source/build paths in compiled artifacts" ON)
if(DUCKTAPE_REPRODUCIBLE_PATHS AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  # clang-cl takes driver options through /clang: (a bare -f... would read as a file).
  if(CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC")
    set(DTL_DRIVER_OPTION "/clang:")
    # Deterministic PE files: no timestamps in objects, and a content hash instead of the
    # link time in the DLL, addon and import library headers.
    add_compile_options(/Brepro)
    add_link_options(/Brepro)
  else()
    set(DTL_DRIVER_OPTION "")
  endif()
  add_compile_options("${DTL_DRIVER_OPTION}-ffile-prefix-map=${UPSTREAM}=./third_party/llama.cpp"
    "${DTL_DRIVER_OPTION}-ffile-prefix-map=${CMAKE_CURRENT_SOURCE_DIR}=."
    "${DTL_DRIVER_OPTION}-ffile-prefix-map=${CMAKE_CURRENT_BINARY_DIR}=./build")
endif()
# Hardening for everything compiled into the shipped library and Node addon (ggml and llama.cpp
# included: these options are set before they are added). Without them the protections come only
# from the distribution compiler's defaults. Not for the browser build (WebAssembly).
if(NOT EMSCRIPTEN AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  if(CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC")
    # Control Flow Guard: compile with checks, and mark the image (DllCharacteristics GUARD_CF).
    add_compile_options("$<$<COMPILE_LANGUAGE:C,CXX>:/guard:cf>")
    add_link_options(/guard:cf)
  else()
    add_compile_options("$<$<COMPILE_LANGUAGE:C,CXX>:-fstack-protector-strong>")
    # Fortification needs optimisation; -U first, as some toolchains predefine the macro.
    add_compile_options("$<$<AND:$<COMPILE_LANGUAGE:C,CXX>,$<CONFIG:Release,RelWithDebInfo,MinSizeRel>>:-U_FORTIFY_SOURCE;-D_FORTIFY_SOURCE=3>")
    if(UNIX AND NOT APPLE)
      # ELF: read-only relocations, resolved at load time (full RELRO / BIND_NOW).
      add_link_options("LINKER:-z,relro,-z,now")
      if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64|ARM64")
        add_compile_options("$<$<COMPILE_LANGUAGE:C,CXX>:-mbranch-protection=standard>")
      endif()
    endif()
  endif()
endif()
add_subdirectory("${UPSTREAM}" "${CMAKE_CURRENT_BINARY_DIR}/third_party/llama.cpp" EXCLUDE_FROM_ALL)
# Upstream Git discovery can find the parent project's repository in an archive.
# Normalize its generated metadata to the explicit runtime identity instead.
set(DTL_GGML_VERSION_HEADER "${CMAKE_CURRENT_BINARY_DIR}/third_party/llama.cpp/ggml/src/ggml-version.h")
file(READ "${DTL_GGML_VERSION_HEADER}" DTL_GGML_VERSION_TEXT)
string(REGEX REPLACE "#define GGML_COMMIT[ \t]+\"[^\"]*\"" "#define GGML_COMMIT \"${DTL_RUNTIME_COMMIT}\"" DTL_GGML_VERSION_TEXT "${DTL_GGML_VERSION_TEXT}")
file(WRITE "${DTL_GGML_VERSION_HEADER}" "${DTL_GGML_VERSION_TEXT}")
include(cmake/runtime_patches.cmake)
if(NOT EMSCRIPTEN)
  # The runtime sources this build compiles, checked by packaging (cmake/runtime_sources.cmake).
  include(cmake/runtime_sources.cmake)
endif()
configure_file(cmake/build_config.hpp.in ${CMAKE_CURRENT_BINARY_DIR}/generated/build_config.hpp @ONLY)
add_library(ducktape_laya ${DTL_LIBRARY_KIND} src/laya.cpp src/backend_llama.cpp src/language.cpp src/platform.cpp
  ${UPSTREAM}/common/json.cpp ${UPSTREAM}/common/unicode.cpp
  ${UPSTREAM}/common/jinja/lexer.cpp ${UPSTREAM}/common/jinja/parser.cpp
  ${UPSTREAM}/common/jinja/runtime.cpp ${UPSTREAM}/common/jinja/value.cpp
  ${UPSTREAM}/common/jinja/string.cpp)
target_include_directories(ducktape_laya PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include> $<INSTALL_INTERFACE:include>
  PRIVATE ${UPSTREAM}/common ${DTL_VENDOR_INCLUDE} ${UPSTREAM}/vendor ${CMAKE_CURRENT_BINARY_DIR}/generated)
dtl_use_patched_sources(ducktape_laya)
target_link_libraries(ducktape_laya PRIVATE llama)
target_compile_definitions(ducktape_laya PRIVATE DUCKTAPE_LAYA_BUILD)
if(NOT EMSCRIPTEN AND CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64")
  foreach(feature AVX AVX2 FMA F16C BMI2 SSE42)
    # MSVC-style builds (clang-cl too) have no GGML_FMA/GGML_F16C: ggml's /arch:AVX2 implies them.
    if(GGML_${feature} OR (MSVC AND GGML_AVX2 AND feature MATCHES "^(FMA|F16C)$"))
      target_compile_definitions(ducktape_laya PRIVATE DTL_NEED_${feature})
    endif()
  endforeach()
  foreach(feature AVX512 AVX512_VBMI AVX512_VNNI AVX512_BF16 AVX_VNNI)
    if(GGML_${feature})
      message(FATAL_ERROR "This package's CPU guard does not yet support GGML_${feature}; use the validated AVX2 or portable configuration")
    endif()
  endforeach()
endif()
if(NOT EMSCRIPTEN AND CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64|ARM64")
  if(GGML_CPU_ARM_ARCH MATCHES "dotprod")
    target_compile_definitions(ducktape_laya PRIVATE DTL_NEED_DOTPROD)
  endif()
  if(GGML_CPU_ARM_ARCH MATCHES "fp16")
    target_compile_definitions(ducktape_laya PRIVATE DTL_NEED_FP16)
  endif()
endif()
set_target_properties(ducktape_laya PROPERTIES EXPORT_NAME laya VERSION ${PROJECT_VERSION} SOVERSION 1
  CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN YES)
if(NOT EMSCRIPTEN)
  dtl_record_native_build(ducktape_laya)
  dtl_record_runtime_sources(ducktape_laya)
endif()
if(UNIX AND NOT APPLE AND NOT EMSCRIPTEN)
  target_link_options(ducktape_laya PRIVATE "LINKER:--exclude-libs,ALL"
    "LINKER:--version-script=${CMAKE_CURRENT_SOURCE_DIR}/src/exports.map")
  set_property(TARGET ducktape_laya APPEND PROPERTY LINK_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/src/exports.map)
elseif(APPLE)
  # The exports.map equivalent; macOS has no symbol versions, so the ABI is the
  # install name's compatibility version (libducktape_laya.1.dylib).
  target_link_options(ducktape_laya PRIVATE "LINKER:-exported_symbols_list,${CMAKE_CURRENT_SOURCE_DIR}/src/exports.macos")
  set_property(TARGET ducktape_laya APPEND PROPERTY LINK_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/src/exports.macos)
  set_target_properties(ducktape_laya PROPERTIES INSTALL_NAME_DIR "@rpath")
endif()
option(DUCKTAPE_BUILD_EXAMPLES "Build optional developer examples and forms" OFF)
if(DUCKTAPE_BUILD_EXAMPLES)
  add_executable(laya-decide examples/decide.c)
  # Keep C compilation/ABI coverage, but let the C++ driver supply runtime
  # support for instrumented C++ shared libraries (notably Clang 18 UBSan).
  set_target_properties(laya-decide PROPERTIES LINKER_LANGUAGE CXX)
  target_link_libraries(laya-decide PRIVATE ducktape_laya)
  add_executable(laya-decide-cpp examples/decide.cpp)
  target_link_libraries(laya-decide-cpp PRIVATE ducktape_laya)
  # Example-only form/JSON helper; no additions to the shipped library API.
  add_library(laya-demo-form STATIC examples/demo_form.cpp)
  target_include_directories(laya-demo-form PRIVATE ${UPSTREAM}/vendor)
  add_executable(laya-demo-c examples/demo.c)
  target_link_libraries(laya-demo-c PRIVATE ducktape_laya laya-demo-form)
  add_executable(laya-demo-cpp examples/demo.cpp)
  target_link_libraries(laya-demo-cpp PRIVATE ducktape_laya laya-demo-form)
endif()
if(NOT EMSCRIPTEN)
include(GNUInstallDirs)
install(TARGETS ducktape_laya EXPORT ducktape_laya-targets LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
  RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR} ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
include(CMakePackageConfigHelpers)
configure_package_config_file(cmake/ducktape_laya-config.cmake.in
  ${CMAKE_CURRENT_BINARY_DIR}/ducktape_laya-config.cmake
  INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/ducktape_laya)
write_basic_package_version_file(${CMAKE_CURRENT_BINARY_DIR}/ducktape_laya-config-version.cmake
  VERSION ${PROJECT_VERSION} COMPATIBILITY SameMinorVersion)
# The .pc file sits in <libdir>/pkgconfig; find the prefix relative to it, so a
# multiarch libdir (lib/x86_64-linux-gnu) resolves correctly.
file(RELATIVE_PATH DTL_PC_PREFIX "/prefix/${CMAKE_INSTALL_LIBDIR}/pkgconfig" "/prefix")
string(REGEX REPLACE "/$" "" DTL_PC_PREFIX "${DTL_PC_PREFIX}")
configure_file(cmake/ducktape-laya.pc.in ${CMAKE_CURRENT_BINARY_DIR}/ducktape-laya.pc @ONLY)
install(EXPORT ducktape_laya-targets NAMESPACE ducktape:: DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/ducktape_laya)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/ducktape_laya-config.cmake
  ${CMAKE_CURRENT_BINARY_DIR}/ducktape_laya-config-version.cmake DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/ducktape_laya)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/ducktape-laya.pc DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig)
install(FILES release/compatibility.json models/manifest.json DESTINATION ${CMAKE_INSTALL_DATADIR}/ducktape-laya)
install(PROGRAMS scripts/download-model.py DESTINATION ${CMAKE_INSTALL_DATADIR}/ducktape-laya)
install(FILES include/ducktape_laya.h include/ducktape_laya.hpp DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
install(FILES LICENSE THIRD_PARTY_NOTICES.md DESTINATION ${CMAKE_INSTALL_DATADIR}/ducktape-laya)
install(DIRECTORY licenses/ DESTINATION ${CMAKE_INSTALL_DATADIR}/ducktape-laya/licenses)
endif()
if(DUCKTAPE_BUILD_TESTS)
  enable_testing()
  add_executable(laya-conformance-c tests/consumers/c.c)
  set_target_properties(laya-conformance-c PROPERTIES LINKER_LANGUAGE CXX)
  target_link_libraries(laya-conformance-c PRIVATE ducktape_laya)
  add_executable(laya-cpu-guard-probe tests/cpu-guard-probe.c)
  set_target_properties(laya-cpu-guard-probe PROPERTIES LINKER_LANGUAGE CXX)
  target_link_libraries(laya-cpu-guard-probe PRIVATE ducktape_laya)
  add_executable(laya-conformance-cpp tests/consumers/cpp.cpp)
  target_link_libraries(laya-conformance-cpp PRIVATE ducktape_laya)
  add_executable(laya-control-tests tests/control.cpp)
  target_link_libraries(laya-control-tests PRIVATE ducktape_laya)
  add_test(NAME control-errors COMMAND laya-control-tests)
  add_executable(laya-tests tests/native.cpp)
  target_link_libraries(laya-tests PRIVATE ducktape_laya)
  add_executable(laya-feature-tests tests/features.cpp)
  target_include_directories(laya-feature-tests PRIVATE ${UPSTREAM}/vendor)
  target_link_libraries(laya-feature-tests PRIVATE ducktape_laya)
  add_test(NAME feature-errors COMMAND laya-feature-tests)
  add_test(NAME abi-errors COMMAND laya-tests)
  add_executable(laya-decision-tests tests/decision.cpp)
  target_include_directories(laya-decision-tests PRIVATE src ${DTL_VENDOR_INCLUDE})
  add_test(NAME decision-rules COMMAND laya-decision-tests)
  add_executable(laya-language-tests tests/language.cpp src/language.cpp)
  target_include_directories(laya-language-tests PRIVATE src ${DTL_VENDOR_INCLUDE})
  add_test(NAME language-routing COMMAND laya-language-tests ${CMAKE_CURRENT_SOURCE_DIR}/tests/original-reference/routing)
  # Special-token partition (tokenizer-linear-partition.patch): unchanged tokens and
  # linear time, on upstream's vocabulary-only test files (no weights).
  add_executable(laya-tokenizer-tests tests/tokenizer.cpp)
  target_link_libraries(laya-tokenizer-tests PRIVATE llama)
  set(DTL_TOKENIZER_GOLDEN ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/tokenizer-partition.txt)
  set(DTL_TOKENIZER_VOCABS)
  foreach(name gpt-neox qwen2 llama-bpe llama-spm phi-3 bert-bge gemma-4 nomic-bert-moe)
    list(APPEND DTL_TOKENIZER_VOCABS ${UPSTREAM}/models/ggml-vocab-${name}.gguf)
  endforeach()
  add_test(NAME tokenizer-partition COMMAND laya-tokenizer-tests ${DTL_TOKENIZER_GOLDEN} ${DTL_TOKENIZER_VOCABS})
  # Selected decision head (laya-selected-decision-head.patch): bit-identical to the full graph.
  add_executable(laya-decision-head-tests tests/decision-head.cpp)
  target_link_libraries(laya-decision-head-tests PRIVATE llama)
  # Fault injection uses Linux process controls (prctl, rlimits); its tests are Linux-only.
  if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(laya-fault-tests tests/faults.cpp)
    target_link_libraries(laya-fault-tests PRIVATE ducktape_laya)
    add_executable(laya-ggml-fault-tests tests/ggml-faults.cpp)
    target_link_libraries(laya-ggml-fault-tests PRIVATE ggml ${CMAKE_DL_LIBS})
    add_test(NAME ggml-faults COMMAND laya-ggml-fault-tests)
  endif()
  if(DUCKTAPE_TEST_MODEL)
    add_test(NAME real-control COMMAND laya-control-tests ${DUCKTAPE_TEST_MODEL})
    add_test(NAME real-model COMMAND laya-tests ${DUCKTAPE_TEST_MODEL})
    add_test(NAME real-features COMMAND laya-feature-tests ${DUCKTAPE_TEST_MODEL})
    add_test(NAME real-tokenizer COMMAND laya-tokenizer-tests ${DTL_TOKENIZER_GOLDEN} ${DUCKTAPE_TEST_MODEL})
    add_test(NAME real-decision-head COMMAND laya-decision-head-tests ${DUCKTAPE_TEST_MODEL})
    set_tests_properties(real-features PROPERTIES TIMEOUT ${DUCKTAPE_REAL_TEST_TIMEOUT})
    if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
      add_test(NAME real-faults COMMAND laya-fault-tests ${DUCKTAPE_TEST_MODEL})
      set_tests_properties(real-faults PROPERTIES TIMEOUT ${DUCKTAPE_REAL_TEST_TIMEOUT})
      add_test(NAME real-runtime-faults COMMAND laya-fault-tests ${DUCKTAPE_TEST_MODEL} --runtime-faults)
      set_tests_properties(real-runtime-faults PROPERTIES TIMEOUT ${DUCKTAPE_REAL_TEST_TIMEOUT})
      if(NOT CMAKE_CXX_FLAGS MATCHES "-fsanitize")
        add_test(NAME real-c-faults COMMAND laya-fault-tests ${DUCKTAPE_TEST_MODEL} --c-allocations)
        set_tests_properties(real-c-faults PROPERTIES TIMEOUT ${DUCKTAPE_REAL_TEST_TIMEOUT})
      endif()
    endif()
    # Emulated CPUs without the required features; instrumented builds do not run under QEMU.
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64" AND NOT CMAKE_CXX_FLAGS MATCHES "-fsanitize")
      find_program(DTL_QEMU_X86_64 qemu-x86_64)
      find_program(DTL_PYTHON3 python3)
      if(DTL_QEMU_X86_64 AND DTL_PYTHON3)
        add_test(NAME cpu-guard COMMAND ${DTL_PYTHON3} ${CMAKE_CURRENT_SOURCE_DIR}/tests/cpu-guard.py
          --build ${CMAKE_CURRENT_BINARY_DIR} ${DUCKTAPE_TEST_MODEL})
        set_tests_properties(cpu-guard PROPERTIES TIMEOUT ${DUCKTAPE_REAL_TEST_TIMEOUT})
      endif()
    endif()
  endif()
endif()

option(DUCKTAPE_BUILD_NODE "Build Node-API addon (supply NODE_INCLUDE_DIR, and NODE_LIB on Windows)" OFF)
if(DUCKTAPE_BUILD_NODE)
  find_path(NODE_INCLUDE_DIR node_api.h REQUIRED)
  add_library(ducktape_laya_node MODULE bindings/node/addon.cpp)
  target_include_directories(ducktape_laya_node PRIVATE ${NODE_INCLUDE_DIR})
  target_link_libraries(ducktape_laya_node PRIVATE ducktape_laya)
  target_compile_definitions(ducktape_laya_node PRIVATE NAPI_VERSION=8 NODE_GYP_MODULE_NAME=ducktape_laya)
  set_target_properties(ducktape_laya_node PROPERTIES PREFIX "" SUFFIX ".node" OUTPUT_NAME ducktape_laya
    BUILD_WITH_INSTALL_RPATH TRUE
    INSTALL_RPATH "$<IF:$<BOOL:${APPLE}>,@loader_path;@loader_path/../..,$ORIGIN;$ORIGIN/../..>")
  dtl_record_native_build(ducktape_laya_node)
  install(TARGETS ducktape_laya_node LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}/ducktape-laya/node)
  if(APPLE)
    target_link_options(ducktape_laya_node PRIVATE "LINKER:-undefined,dynamic_lookup")
  elseif(WIN32)
    # Node-API lives in the host executable. Link the Node distribution's import
    # library (scripts/fetch-node-sdk.py) and delay-load it, so the hook binds the
    # imports to whichever executable loaded the addon: node.exe or Electron's.
    set(NODE_LIB "" CACHE FILEPATH "node.lib from the Node distribution (win-x64/node.lib)")
    if(NOT EXISTS "${NODE_LIB}")
      message(FATAL_ERROR "The Windows Node addon needs NODE_LIB (see scripts/fetch-node-sdk.py)")
    endif()
    target_sources(ducktape_laya_node PRIVATE bindings/node/win_delay_load_hook.cpp)
    target_link_libraries(ducktape_laya_node PRIVATE "${NODE_LIB}" delayimp)
    # The addon's own import library would default to ducktape_laya.lib, the
    # library's import library, which the Rust and Go bindings link.
    target_link_options(ducktape_laya_node PRIVATE "LINKER:/DELAYLOAD:node.exe"
      "LINKER:/IMPLIB:${CMAKE_CURRENT_BINARY_DIR}/CMakeFiles/ducktape_laya_node.dir/ducktape_laya_node.lib")
  endif()
endif()

# Explicit developer-only coverage instrumentation; never enable in artifacts.
option(DUCKTAPE_BUILD_FUZZERS "Build Linux Clang libFuzzer request/config target" OFF)
if(DUCKTAPE_BUILD_FUZZERS)
  if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    message(FATAL_ERROR "Fuzzer builds require Linux and Clang")
  endif()
  target_compile_options(ducktape_laya PRIVATE -fsanitize=fuzzer-no-link,address,undefined -fno-omit-frame-pointer)
  target_link_options(ducktape_laya PRIVATE -fsanitize=address,undefined)
  add_executable(laya-input-fuzzer EXCLUDE_FROM_ALL tests/input-libfuzzer.cpp)
  target_compile_options(laya-input-fuzzer PRIVATE -fsanitize=fuzzer,address,undefined -fno-omit-frame-pointer)
  target_link_options(laya-input-fuzzer PRIVATE -fsanitize=fuzzer,address,undefined)
  target_link_libraries(laya-input-fuzzer PRIVATE ducktape_laya)
endif()

# Explicit developer stress run, excluded from the normal build/test suite.
if(DUCKTAPE_BUILD_TESTS)
  add_executable(laya-stress EXCLUDE_FROM_ALL tests/stress.cpp)
  target_link_libraries(laya-stress PRIVATE ducktape_laya)
endif()

if(EMSCRIPTEN)
  if(DUCKTAPE_BUILD_NODE OR DUCKTAPE_BUILD_TESTS OR DUCKTAPE_BUILD_EXAMPLES)
    message(FATAL_ERROR "Browser build requires native Node/tests/examples options OFF")
  endif()
  if(GGML_WEBGPU)
    target_compile_definitions(ducktape_laya PRIVATE DTL_BROWSER_WEBGPU)
  endif()
  add_executable(ducktape_laya_wasm bindings/browser/entry.cpp)
  target_link_libraries(ducktape_laya_wasm PRIVATE ducktape_laya)
  set_target_properties(ducktape_laya_wasm PROPERTIES OUTPUT_NAME laya SUFFIX .mjs)
  execute_process(COMMAND "${CMAKE_CXX_COMPILER}" --version OUTPUT_VARIABLE DTL_EMCC_VERSION COMMAND_ERROR_IS_FATAL ANY)
  string(REGEX MATCH "^[^\r\n]*" DTL_EMCC_VERSION "${DTL_EMCC_VERSION}")
  set(DTL_BROWSER_IDENTITY "{}")
  string(JSON DTL_BROWSER_IDENTITY SET "${DTL_BROWSER_IDENTITY}" source "${DTL_SOURCE_IDENTITY}")
  string(JSON DTL_BROWSER_IDENTITY SET "${DTL_BROWSER_IDENTITY}" package_version "\"${PROJECT_VERSION}\"")
  string(JSON DTL_BROWSER_IDENTITY SET "${DTL_BROWSER_IDENTITY}" llama_cpp_commit "\"${DTL_RUNTIME_COMMIT}\"")
  string(JSON DTL_BROWSER_IDENTITY SET "${DTL_BROWSER_IDENTITY}" runtime_patches "${DTL_PATCHES_JSON}")
  string(JSON DTL_BROWSER_IDENTITY SET "${DTL_BROWSER_IDENTITY}" toolchain "\"${DTL_EMCC_VERSION}\"")
  if(GGML_WEBGPU)
    set(DTL_BROWSER_BACKEND webgpu)
  else()
    set(DTL_BROWSER_BACKEND wasm)
  endif()
  string(JSON DTL_BROWSER_IDENTITY SET "${DTL_BROWSER_IDENTITY}" backend "\"${DTL_BROWSER_BACKEND}\"")
  # Test builds may lower the WebAssembly memory ceiling to exercise growth failure;
  # packaging accepts only the product ceiling (4 GiB).
  set(DUCKTAPE_WASM_MAXIMUM_MEMORY "4294967296" CACHE STRING "WebAssembly memory ceiling in bytes (64 KiB multiple)")
  if(NOT DUCKTAPE_WASM_MAXIMUM_MEMORY MATCHES "^[0-9]+$" OR DUCKTAPE_WASM_MAXIMUM_MEMORY GREATER 4294967296
     OR DUCKTAPE_WASM_MAXIMUM_MEMORY LESS 67108864)
    message(FATAL_ERROR "Invalid DUCKTAPE_WASM_MAXIMUM_MEMORY")
  endif()
  math(EXPR DTL_WASM_PAGE_REMAINDER "${DUCKTAPE_WASM_MAXIMUM_MEMORY} % 65536")
  if(NOT DTL_WASM_PAGE_REMAINDER EQUAL 0)
    message(FATAL_ERROR "DUCKTAPE_WASM_MAXIMUM_MEMORY must be a multiple of 65536")
  endif()
  string(JSON DTL_BROWSER_IDENTITY SET "${DTL_BROWSER_IDENTITY}" maximum_memory "${DUCKTAPE_WASM_MAXIMUM_MEMORY}")
  set(DUCKTAPE_WASM_OPTIMIZATION "2" CACHE STRING "WASM link optimization: 0, 1, 2, 3, s or z")
  if(NOT DUCKTAPE_WASM_OPTIMIZATION MATCHES "^[0123sz]$")
    message(FATAL_ERROR "Invalid DUCKTAPE_WASM_OPTIMIZATION")
  endif()
  # Build switches that change the shipped assets; packaging accepts only product values.
  string(JSON DTL_BROWSER_IDENTITY SET "${DTL_BROWSER_IDENTITY}" optimization "\"${DUCKTAPE_WASM_OPTIMIZATION}\"")
  if(GGML_WEBGPU_DEBUG)
    string(JSON DTL_BROWSER_IDENTITY SET "${DTL_BROWSER_IDENTITY}" webgpu_debug true)
  else()
    string(JSON DTL_BROWSER_IDENTITY SET "${DTL_BROWSER_IDENTITY}" webgpu_debug false)
  endif()
  file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/generated/browser-identity.json" CONTENT "${DTL_BROWSER_IDENTITY}")
  set_property(TARGET ducktape_laya_wasm APPEND PROPERTY LINK_DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/generated/browser-identity.json")
  add_custom_command(TARGET ducktape_laya_wasm POST_BUILD COMMAND ${CMAKE_COMMAND}
    "-DINPUT=${CMAKE_CURRENT_BINARY_DIR}/generated/browser-identity.json"
    "-DMODULE=$<TARGET_FILE:ducktape_laya_wasm>"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/browser_identity.cmake")
  if(GGML_WEBGPU)
    target_link_options(ducktape_laya_wasm PRIVATE "-sJSPI_EXPORTS=['dtl_open','dtl_open_router','dtl_execute','dtl_close']")
  endif()
  target_link_options(ducktape_laya_wasm PRIVATE "-O${DUCKTAPE_WASM_OPTIMIZATION}"
    "-sMODULARIZE=1" "-sEXPORT_ES6=1" "-sENVIRONMENT=web,worker"
    "-sALLOW_MEMORY_GROWTH=1" "-sMAXIMUM_MEMORY=${DUCKTAPE_WASM_MAXIMUM_MEMORY}" "-sINITIAL_MEMORY=67108864"
    "-sSTACK_SIZE=2097152" "-sFORCE_FILESYSTEM=1"
    "$<$<NOT:$<BOOL:${GGML_WEBGPU}>>:-sDISABLE_EXCEPTION_CATCHING=0>"
    "-sEXPORTED_FUNCTIONS=['_malloc','_free','_dtl_abi_version','_dtl_open','_dtl_open_router','_dtl_info','_dtl_default_call_options','_dtl_cancel_flag','_dtl_execute','_dtl_close','_dtl_free']"
    "-sALLOW_TABLE_GROWTH=1"
    "-sEXPORTED_RUNTIME_METHODS=['HEAPU32','addFunction','removeFunction','ccall','UTF8ToString','stringToUTF8','lengthBytesUTF8','FS']")
endif()

if(DUCKTAPE_BUILD_REFERENCE_PROBE)
  add_executable(laya-reference tests/upstream-reference.cpp
    ${UPSTREAM}/tools/server/server-decision.cpp ${UPSTREAM}/tools/server/server-common.cpp)
  target_include_directories(laya-reference PRIVATE ${UPSTREAM}/tools/server ${UPSTREAM})
  target_link_libraries(laya-reference PRIVATE llama-common mtmd)
  if(EMSCRIPTEN)
    set_target_properties(laya-reference PROPERTIES SUFFIX .mjs)
    target_link_options(laya-reference PRIVATE "-O1" "-sMODULARIZE=1" "-sEXPORT_ES6=1"
      "-sENVIRONMENT=web,worker" "-sALLOW_MEMORY_GROWTH=1" "-sMAXIMUM_MEMORY=4294967296"
      "-sINITIAL_MEMORY=67108864" "-sSTACK_SIZE=2097152" "-sFORCE_FILESYSTEM=1"
      "-sEXPORTED_FUNCTIONS=['_reference_open','_reference_execute','_reference_close']"
      "-sEXPORTED_RUNTIME_METHODS=['ccall','FS']"
      "$<$<NOT:$<BOOL:${GGML_WEBGPU}>>:-sDISABLE_EXCEPTION_CATCHING=0>")
    if(GGML_WEBGPU)
      target_compile_definitions(laya-reference PRIVATE REFERENCE_WEBGPU)
      target_link_options(laya-reference PRIVATE "-sJSPI_EXPORTS=['reference_open','reference_execute','reference_close']")
    endif()
  endif()
endif()
