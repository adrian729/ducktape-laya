# Build-directory sources written at configure time (the patched runtime copies and
# ggml-version.h) are replaced only when their content changes. A reconfigure then
# keeps unchanged files with their timestamps, and the build recompiles nothing that
# did not change. tests/reconfigure.py checks this.

# Make <root> hold exactly the files of <stage>: copy new and changed files, keep the
# unchanged ones untouched and remove those <stage> no longer has.
function(dtl_sync_patched_copies stage root)
  file(GLOB_RECURSE staged LIST_DIRECTORIES false RELATIVE "${stage}" "${stage}/*")
  # patch(1) can leave a backup NAME.orig beside a file whose hunks applied at a line offset (GNU
  # patch does unless told --no-backup-if-mismatch; other patch programs may too). A backup is not
  # a patched copy: copied, it reached the runtime source record as an uncovered file and failed the
  # configure of a runtime-upgrade rehearsal whose upstream had moved the patched lines.
  list(FILTER staged EXCLUDE REGEX "\\.orig$")
  foreach(name IN LISTS staged)
    get_filename_component(directory "${root}/${name}" DIRECTORY)
    file(MAKE_DIRECTORY "${directory}")
    file(COPY_FILE "${stage}/${name}" "${root}/${name}" ONLY_IF_DIFFERENT)
  endforeach()
  file(GLOB_RECURSE existing LIST_DIRECTORIES false RELATIVE "${root}" "${root}/*")
  foreach(name IN LISTS existing)
    if(NOT name IN_LIST staged)
      file(REMOVE "${root}/${name}")
    endif()
  endforeach()
endfunction()

# Give <path>, which another configure step may already have rewritten, the content
# <text>, with the timestamp of the last configure that changed <text>: the text is
# kept in <stage> (written only when it differs) and copied with its timestamp.
function(dtl_write_generated path stage text)
  set(previous "")
  if(EXISTS "${stage}")
    file(READ "${stage}" previous)
  endif()
  if(NOT previous STREQUAL text)
    file(WRITE "${stage}" "${text}")
  endif()
  get_filename_component(name "${path}" NAME)
  get_filename_component(stage_name "${stage}" NAME)
  if(NOT name STREQUAL stage_name)
    message(FATAL_ERROR "dtl_write_generated: ${stage} must have the file name of ${path}")
  endif()
  get_filename_component(directory "${path}" DIRECTORY)
  set(current "")
  if(EXISTS "${path}")
    file(READ "${path}" current)
  endif()
  if(NOT current STREQUAL text)
    # file(COPY) keeps the source's timestamp, but skips a destination whose timestamp is
    # within a second of it (as one written in the same second is), so remove it first.
    file(REMOVE "${path}")
    file(COPY "${stage}" DESTINATION "${directory}")
  endif()
  file(READ "${path}" current)
  if(NOT current STREQUAL text)
    message(FATAL_ERROR "dtl_write_generated: ${path} does not have the generated content")
  endif()
endfunction()

# Write <text> to <path> only when its content differs, so an unchanged configure output
# keeps its timestamp.
function(dtl_write_if_changed path text)
  set(current "")
  if(EXISTS "${path}")
    file(READ "${path}" current)
  endif()
  if(NOT current STREQUAL text)
    file(WRITE "${path}" "${text}")
  endif()
endfunction()
