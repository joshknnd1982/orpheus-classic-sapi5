cmake_minimum_required(VERSION 3.15)
project(OrpheusClassicSAPI VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)

if(CMAKE_SIZEOF_VOID_P EQUAL 8)
    set(BUILD_X64 TRUE)
    message(STATUS "Building x64 version")
else()
    set(BUILD_X64 FALSE)
    message(STATUS "Building x86 version")
endif()

# Shared client for the Orpheus engine host, settings store and logging.
# Identical for both architectures: the host is always the shipped 32-bit
# orpheus-classic-host.exe reached over a loopback socket.
add_library(orpheus_core STATIC
    src/orpheus_client.cpp
    src/settings.cpp
    src/debug_log.cpp
)

target_include_directories(orpheus_core PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(orpheus_core PUBLIC
    ws2_32
    shlwapi
    advapi32
    shell32
)

target_compile_definitions(orpheus_core PRIVATE
    _CRT_SECURE_NO_WARNINGS
    UNICODE
    _UNICODE
)

# The SAPI5 engine DLL.
add_library(OrpheusClassicSAPI SHARED
    src/sapi_main.cpp
    src/com.cpp
    src/registry.cpp
    src/ISpDataKeyImpl.cpp
    src/IEnumSpObjectTokensImpl.cpp
    src/ISpTTSEngineImpl.cpp
    src/voice_token.cpp
    src/orpheus_sapi.def
)

target_include_directories(OrpheusClassicSAPI PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(OrpheusClassicSAPI PRIVATE
    orpheus_core
    ole32
    oleaut32
    advapi32
    shell32
)

target_compile_definitions(OrpheusClassicSAPI PRIVATE
    _CRT_SECURE_NO_WARNINGS
    UNICODE
    _UNICODE
)

if(MSVC)
    target_compile_options(orpheus_core PRIVATE /W3 /MP)
    target_compile_options(OrpheusClassicSAPI PRIVATE /W3 /MP)

    set_property(TARGET orpheus_core PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    set_property(TARGET OrpheusClassicSAPI PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

# Developer-only SAPI test harness (not installed).
add_executable(sapi_test
    test/sapi_test.cpp
)

add_executable(client_latency_test
    test/client_latency_test.cpp
)

target_link_libraries(client_latency_test PRIVATE
    orpheus_core
)

if(MSVC)
    set_property(TARGET client_latency_test PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

target_link_libraries(sapi_test PRIVATE
    ole32
    sapi
)

if(MSVC)
    set_property(TARGET sapi_test PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

# The configuration utility only needs to exist once; build it in the x86
# pass so it runs on every Windows installation.
if(NOT BUILD_X64)
    add_executable(OrpheusClassicConfig WIN32
        src/config_tool.cpp
        src/orpheus_config.rc
    )

    target_include_directories(OrpheusClassicConfig PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
    )

    target_link_libraries(OrpheusClassicConfig PRIVATE
        orpheus_core
        comctl32
        ole32
    )

    target_compile_definitions(OrpheusClassicConfig PRIVATE
        _CRT_SECURE_NO_WARNINGS
        UNICODE
        _UNICODE
    )

    if(MSVC)
        target_compile_options(OrpheusClassicConfig PRIVATE /W3 /MP)
        set_property(TARGET OrpheusClassicConfig PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    endif()
endif()
