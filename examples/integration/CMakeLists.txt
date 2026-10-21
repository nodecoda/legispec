cmake_minimum_required(VERSION 3.20)
project(legalizer_dsl_integration LANGUAGES CXX)

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

if(NOT DEFINED LDSL_WIRING_DIR)
  message(FATAL_ERROR "LDSL_WIRING_DIR is required (output of --emit-wiring)")
endif()
if(NOT DEFINED LDSL_GENERATED_STEM)
  set(LDSL_GENERATED_STEM "toycpu_rules_gen")
endif()

include("${LDSL_WIRING_DIR}/legalizer-rules.cmake")
set(LDSL_GENERATED_TARGET "legalizer_dsl_${LDSL_GENERATED_STEM}")
if(NOT TARGET ${LDSL_GENERATED_TARGET})
  message(FATAL_ERROR
    "legalizer-rules.cmake did not create target ${LDSL_GENERATED_TARGET}")
endif()

add_executable(legalizer-dsl-consumer main.cpp)
target_include_directories(legalizer-dsl-consumer PRIVATE
  ${LLVM_INCLUDE_DIRS}
  "${LDSL_WIRING_DIR}"
  "${LEGALIZER_DSL_GENERATED_DIR}")

separate_arguments(LLVM_DEFINITIONS_LIST NATIVE_COMMAND "${LLVM_DEFINITIONS}")
target_compile_options(legalizer-dsl-consumer PRIVATE ${LLVM_DEFINITIONS_LIST})
target_include_directories(${LDSL_GENERATED_TARGET} PRIVATE ${LLVM_INCLUDE_DIRS})
target_compile_options(${LDSL_GENERATED_TARGET} PRIVATE ${LLVM_DEFINITIONS_LIST})

# LLVM is commonly built with assertions even in Release.  CMake's Release
# flags define NDEBUG, which changes LegalizeRuleSet's debug-only members and
# therefore LegalizerInfo's layout.  Both TUs must match LLVM's NDEBUG state.
if(LLVM_ENABLE_ASSERTIONS)
  if(MSVC)
    target_compile_options(legalizer-dsl-consumer PRIVATE /UNDEBUG)
    target_compile_options(${LDSL_GENERATED_TARGET} PRIVATE /UNDEBUG)
  else()
    target_compile_options(legalizer-dsl-consumer PRIVATE -UNDEBUG)
    target_compile_options(${LDSL_GENERATED_TARGET} PRIVATE -UNDEBUG)
  endif()
endif()

if(MSVC)
  target_compile_options(legalizer-dsl-consumer PRIVATE /W4 /permissive-)
  target_compile_options(${LDSL_GENERATED_TARGET} PRIVATE /W4 /permissive-)
else()
  target_compile_options(legalizer-dsl-consumer PRIVATE
    -Wall -Wextra -Wno-unused-parameter)
  target_compile_options(${LDSL_GENERATED_TARGET} PRIVATE
    -Wall -Wextra -Wno-unused-parameter)
endif()

option(LDSL_USE_EXTENDED_LLT
  "Enable LLT::setUseExtended(true) for float-keyed targets such as AArch64" OFF)
if(LDSL_USE_EXTENDED_LLT)
  target_compile_definitions(legalizer-dsl-consumer PRIVATE LDSL_USE_EXTENDED_LLT)
endif()

target_link_libraries(legalizer-dsl-consumer PRIVATE ${LDSL_GENERATED_TARGET})
