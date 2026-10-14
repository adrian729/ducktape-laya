# One path-independent fingerprint for build inputs and shipped binding sources.
# Used by both CMake builds and the packagers (cmake -P); no Python build dependency.
if(NOT DTL_SOURCE_ROOT)
  set(DTL_SOURCE_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")
endif()
set(glob_options)
if(NOT CMAKE_SCRIPT_MODE_FILE)
  list(APPEND glob_options CONFIGURE_DEPENDS)
endif()
file(GLOB_RECURSE identity_files LIST_DIRECTORIES false ${glob_options}
  "${DTL_SOURCE_ROOT}/src/*" "${DTL_SOURCE_ROOT}/include/*"
  "${DTL_SOURCE_ROOT}/cmake/*" "${DTL_SOURCE_ROOT}/patches/*"
  "${DTL_SOURCE_ROOT}/licenses/*"
  "${DTL_SOURCE_ROOT}/bindings/rust/ducktape-laya/src/*"
  "${DTL_SOURCE_ROOT}/bindings/rust/ducktape-laya-sys/src/*")
file(GLOB binding_files LIST_DIRECTORIES false ${glob_options}
  "${DTL_SOURCE_ROOT}/CMakeLists.txt" "${DTL_SOURCE_ROOT}/LICENSE"
  "${DTL_SOURCE_ROOT}/THIRD_PARTY_NOTICES.md"
  "${DTL_SOURCE_ROOT}/release/*.json" "${DTL_SOURCE_ROOT}/models/*.json"
  "${DTL_SOURCE_ROOT}/bindings/node/*" "${DTL_SOURCE_ROOT}/bindings/browser/*"
  "${DTL_SOURCE_ROOT}/bindings/rust/Cargo.*"
  "${DTL_SOURCE_ROOT}/bindings/rust/ducktape-laya/*"
  "${DTL_SOURCE_ROOT}/bindings/rust/ducktape-laya-sys/*")
list(APPEND identity_files ${binding_files})
list(SORT identity_files)
set(identity_manifest "{}")
foreach(path IN LISTS identity_files)
  file(RELATIVE_PATH name "${DTL_SOURCE_ROOT}" "${path}")
  file(SHA256 "${path}" hash)
  string(JSON identity_manifest SET "${identity_manifest}" "${name}" "\"${hash}\"")
endforeach()
string(SHA256 DTL_SOURCE_SHA256 "${identity_manifest}")
set(DTL_SOURCE_IDENTITY "{}")
string(JSON DTL_SOURCE_IDENTITY SET "${DTL_SOURCE_IDENTITY}" sha256 "\"${DTL_SOURCE_SHA256}\"")
string(JSON DTL_SOURCE_IDENTITY SET "${DTL_SOURCE_IDENTITY}" files "${identity_manifest}")
if(CMAKE_SCRIPT_MODE_FILE)
  file(WRITE "${OUTPUT}" "${DTL_SOURCE_IDENTITY}\n")
else()
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${identity_files})
  file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/generated/source-identity.json" CONTENT "${DTL_SOURCE_IDENTITY}\n")
endif()

function(dtl_record_native_build target)
  # Relink even when only a shipped wrapper/header changed, so a completed build
  # records its new inputs. Building just the library cannot bless an old addon.
  set_property(TARGET ${target} APPEND PROPERTY LINK_DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/generated/source-identity.json")
  add_custom_command(TARGET ${target} POST_BUILD COMMAND ${CMAKE_COMMAND}
    "-DINPUT=${CMAKE_CURRENT_BINARY_DIR}/generated/source-identity.json"
    "-DBINARY=$<TARGET_FILE:${target}>"
    "-DOUTPUT=${CMAKE_CURRENT_BINARY_DIR}/${target}.build.json"
    -P "${DTL_SOURCE_ROOT}/cmake/native_identity.cmake" VERBATIM)
endfunction()
