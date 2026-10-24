# The runtime sources a native build compiles: a SHA-256 manifest of the llama.cpp
# files that can reach the library (relative to the runtime root) and of the
# patched copies in the build directory (relative to the patch root).
#
# The configure step records it in generated/runtime-sources.configure.json, and
# every link of the library records it again, with the library's hash and the
# runtime files that link cannot have recompiled (see dtl_runtime_stale), in
# <target>.runtime-sources.json next to the library. Packaging
# (release_support.require_runtime_sources) compares the link-time record with
# the runtime tree as it is then and with the configure-time patched copies, so
# a library linked from a modified tree cannot be packaged as the pinned runtime
# after the tree is restored. Nothing here reaches the library itself.
#
# Script mode: cmake -DRUNTIME=<root> [-DPATCHED=<patch root>] [-DBINARY=<file>]
#   -DOUTPUT=<json> -P runtime_sources.cmake

if(CMAKE_SCRIPT_MODE_FILE)
  # Script mode starts with every policy unset (OLD); quoted if() arguments must stay strings (CMP0054).
  cmake_minimum_required(VERSION 3.22)
endif()

# Files under the runtime root that the native build reads: llama.cpp's and ggml's
# build scripts and headers, ggml's core and CPU backend, llama, common and the
# vendored headers. Other ggml backends are not built; the top of the WebGPU
# backend's directory is listed because a runtime patch copies its source (built
# only in browser WebGPU builds). The first list is searched recursively, the
# second is not.
set(DTL_RUNTIME_SOURCE_GLOBS cmake/* ggml/cmake/* ggml/include/* ggml/src/ggml-cpu/* include/* src/* common/* vendor/*)
set(DTL_RUNTIME_SOURCE_FLAT_GLOBS CMakeLists.txt ggml/CMakeLists.txt ggml/src/* ggml/src/ggml-webgpu/*)

function(dtl_runtime_manifest out_var root globs flat_globs)
  set(patterns)
  foreach(glob IN LISTS globs)
    list(APPEND patterns "${root}/${glob}")
  endforeach()
  set(files)
  if(patterns)
    file(GLOB_RECURSE files LIST_DIRECTORIES false ${patterns})
  endif()
  set(patterns)
  foreach(glob IN LISTS flat_globs)
    list(APPEND patterns "${root}/${glob}")
  endforeach()
  if(patterns)
    file(GLOB flat LIST_DIRECTORIES false ${patterns})
    list(APPEND files ${flat})
  endif()
  list(REMOVE_DUPLICATES files)
  list(SORT files)
  set(manifest "{}")
  foreach(path IN LISTS files)
    file(RELATIVE_PATH name "${root}" "${path}")
    file(SHA256 "${path}" hash)
    string(JSON manifest SET "${manifest}" "${name}" "\"${hash}\"")
  endforeach()
  set(${out_var} "${manifest}" PARENT_SCOPE)
endfunction()

# {"upstream": {...}, "patched": {...}}; patched is empty without a patch root.
function(dtl_runtime_sources out_var runtime patch_root)
  dtl_runtime_manifest(upstream "${runtime}" "${DTL_RUNTIME_SOURCE_GLOBS}" "${DTL_RUNTIME_SOURCE_FLAT_GLOBS}")
  set(copies "{}")
  if(patch_root)
    dtl_runtime_manifest(copies "${patch_root}" "*" "")
  endif()
  set(result "{}")
  string(JSON result SET "${result}" upstream "${upstream}")
  string(JSON result SET "${result}" patched "${copies}")
  set(${out_var} "${result}" PARENT_SCOPE)
endfunction()

# Runtime files this link cannot have recompiled (["upstream/<name>", "patched/<name>"]). Build tools
# recompile a source only when it is newer than its object, so a file whose content changed since the
# previous link but whose timestamp is older than that link (restored with its old timestamp) is still
# compiled into the library as it was. Such files stay listed, link after link, until they are newer
# than the previous link (touched, and so recompiled).
function(dtl_runtime_stale out_var previous previous_time current runtime patch_root)
  set(stale)
  string(JSON count ERROR_VARIABLE absent LENGTH "${previous}" stale)
  if(NOT absent AND count GREATER 0)
    math(EXPR last "${count} - 1")
    foreach(index RANGE ${last})
      string(JSON entry GET "${previous}" stale ${index})
      string(REGEX REPLACE "^upstream/" "${runtime}/" path "${entry}")
      string(REGEX REPLACE "^patched/" "${patch_root}/" path "${path}")
      if(EXISTS "${path}")
        file(TIMESTAMP "${path}" time "%s" UTC)
        if(time LESS_EQUAL previous_time)
          list(APPEND stale "${entry}")
        endif()
      endif()
    endforeach()
  endif()
  foreach(kind IN ITEMS upstream patched)
    set(root "${runtime}")
    if(kind STREQUAL "patched")
      set(root "${patch_root}")
    endif()
    string(JSON before ERROR_VARIABLE absent GET "${previous}" ${kind})
    string(JSON after GET "${current}" ${kind})
    if(absent OR before STREQUAL after)
      continue()
    endif()
    string(JSON count LENGTH "${after}")
    if(count EQUAL 0)
      continue()
    endif()
    math(EXPR last "${count} - 1")
    foreach(index RANGE ${last})
      string(JSON name MEMBER "${after}" ${index})
      string(JSON now GET "${after}" "${name}")
      string(JSON then ERROR_VARIABLE new GET "${before}" "${name}")
      if(new OR then STREQUAL now)
        continue()
      endif()
      file(TIMESTAMP "${root}/${name}" time "%s" UTC)
      if(time LESS previous_time)
        list(APPEND stale "${kind}/${name}")
      endif()
    endforeach()
  endforeach()
  list(REMOVE_DUPLICATES stale)
  set(${out_var} "${stale}" PARENT_SCOPE)
endfunction()

if(CMAKE_SCRIPT_MODE_FILE)
  if(NOT RUNTIME OR NOT OUTPUT)
    message(FATAL_ERROR "Usage: cmake -DRUNTIME=<root> [-DPATCHED=<root>] [-DBINARY=<file>] -DOUTPUT=<json> -P runtime_sources.cmake")
  endif()
  dtl_runtime_sources(record "${RUNTIME}" "${PATCHED}")
  if(BINARY)
    file(SHA256 "${BINARY}" hash)
    get_filename_component(name "${BINARY}" NAME)
    string(JSON record SET "${record}" binary_name "\"${name}\"")
    string(JSON record SET "${record}" binary_sha256 "\"${hash}\"")
    set(stale)
    if(EXISTS "${OUTPUT}")
      file(READ "${OUTPUT}" previous)
      file(TIMESTAMP "${OUTPUT}" previous_time "%s" UTC)
      dtl_runtime_stale(stale "${previous}" "${previous_time}" "${record}" "${RUNTIME}" "${PATCHED}")
    endif()
    set(entries "[]")
    foreach(entry IN LISTS stale)
      string(JSON length LENGTH "${entries}")
      string(JSON entries SET "${entries}" ${length} "\"${entry}\"")
    endforeach()
    string(JSON record SET "${record}" stale "${entries}")
  endif()
  file(WRITE "${OUTPUT}" "${record}\n")
  return()
endif()

# Configure time: UPSTREAM is the runtime root and DTL_PATCH_ROOT holds the patched copies.
dtl_runtime_sources(DTL_RUNTIME_SOURCES "${UPSTREAM}" "${DTL_PATCH_ROOT}")
string(JSON DTL_RUNTIME_PATCHED_COUNT LENGTH "${DTL_RUNTIME_SOURCES}" patched)
if(DTL_RUNTIME_PATCHED_COUNT GREATER 0)
  math(EXPR last "${DTL_RUNTIME_PATCHED_COUNT} - 1")
  foreach(index RANGE ${last})
    string(JSON name MEMBER "${DTL_RUNTIME_SOURCES}" patched ${index})
    string(JSON original ERROR_VARIABLE missing GET "${DTL_RUNTIME_SOURCES}" upstream "${name}")
    if(missing)
      message(FATAL_ERROR "cmake/runtime_sources.cmake does not cover the patched runtime file ${name}; add its directory to DTL_RUNTIME_SOURCE_GLOBS")
    endif()
  endforeach()
endif()
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/generated/runtime-sources.configure.json" "${DTL_RUNTIME_SOURCES}\n")

set(DTL_RUNTIME_SOURCES_SCRIPT "${CMAKE_CURRENT_LIST_FILE}")
function(dtl_record_runtime_sources target)
  add_custom_command(TARGET ${target} POST_BUILD COMMAND ${CMAKE_COMMAND}
    "-DRUNTIME=${UPSTREAM}" "-DPATCHED=${DTL_PATCH_ROOT}" "-DBINARY=$<TARGET_FILE:${target}>"
    "-DOUTPUT=${CMAKE_CURRENT_BINARY_DIR}/${target}.runtime-sources.json"
    -P "${DTL_RUNTIME_SOURCES_SCRIPT}" VERBATIM)
endfunction()
