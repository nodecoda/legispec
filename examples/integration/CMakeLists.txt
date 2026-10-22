cmake_minimum_required(VERSION 3.20)
project(legispec_integration LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# The consumer is deliberately a separate fixture: it never edits the target's
# hand-written LegalizerInfo translation unit.  The generated rules are linked
# as their own static library through legalizer-rules.cmake.
if(NOT DEFINED LLVM_DIR AND NOT DEFINED ENV{LLVM_DIR})
  message(FATAL_ERROR
    "LLVM_DIR is required, for example F:/llvm/build-toycpu/lib/cmake/llvm")
endif()
find_package(LLVM CONFIG REQUIRED)

if(NOT DEFINED LEGISPEC_WIRING_DIR)
  message(FATAL_ERROR "LEGISPEC_WIRING_DIR is required (output of --emit-wiring)")
endif()
if(NOT DEFINED LEGISPEC_GENERATED_STEM)
  set(LEGISPEC_GENERATED_STEM "toycpu_rules_gen")
endif()

include("${LEGISPEC_WIRING_DIR}/legalizer-rules.cmake")
set(LEGISPEC_GENERATED_TARGET "legispec_${LEGISPEC_GENERATED_STEM}")
if(NOT TARGET ${LEGISPEC_GENERATED_TARGET})
  message(FATAL_ERROR
    "legalizer-rules.cmake did not create target ${LEGISPEC_GENERATED_TARGET}")
endif()

add_executable(legispec-consumer main.cpp)
target_include_directories(legispec-consumer PRIVATE
  ${LLVM_INCLUDE_DIRS}
  "${LEGISPEC_WIRING_DIR}"
  "${LEGISPEC_GENERATED_DIR}")

separate_arguments(LLVM_DEFINITIONS_LIST NATIVE_COMMAND "${LLVM_DEFINITIONS}")
target_compile_options(legispec-consumer PRIVATE ${LLVM_DEFINITIONS_LIST})
target_include_directories(${LEGISPEC_GENERATED_TARGET} PRIVATE ${LLVM_INCLUDE_DIRS})
target_compile_options(${LEGISPEC_GENERATED_TARGET} PRIVATE ${LLVM_DEFINITIONS_LIST})

# LLVM is commonly built with assertions even in Release.  CMake's Release
# flags define NDEBUG, which changes LegalizeRuleSet's debug-only members and
# therefore LegalizerInfo's layout.  Both TUs must match LLVM's NDEBUG state.
if(LLVM_ENABLE_ASSERTIONS)
  if(MSVC)
    target_compile_options(legispec-consumer PRIVATE /UNDEBUG)
    target_compile_options(${LEGISPEC_GENERATED_TARGET} PRIVATE /UNDEBUG)
  else()
    target_compile_options(legispec-consumer PRIVATE -UNDEBUG)
    target_compile_options(${LEGISPEC_GENERATED_TARGET} PRIVATE -UNDEBUG)
  endif()
endif()

if(MSVC)
  target_compile_options(legispec-consumer PRIVATE /W4 /permissive-)
  target_compile_options(${LEGISPEC_GENERATED_TARGET} PRIVATE /W4 /permissive-)
else()
  target_compile_options(legispec-consumer PRIVATE
    -Wall -Wextra -Wno-unused-parameter)
  target_compile_options(${LEGISPEC_GENERATED_TARGET} PRIVATE
    -Wall -Wextra -Wno-unused-parameter)
endif()

option(LEGISPEC_USE_EXTENDED_LLT
  "Enable LLT::setUseExtended(true) for float-keyed targets such as AArch64" OFF)
if(LEGISPEC_USE_EXTENDED_LLT)
  target_compile_definitions(legispec-consumer PRIVATE LEGISPEC_USE_EXTENDED_LLT)
endif()

target_link_libraries(legispec-consumer PRIVATE ${LEGISPEC_GENERATED_TARGET})
