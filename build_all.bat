@echo off
setlocal

echo Orpheus Classic SAPI5 Build
echo.

set BUILD_DIR_X86=build_x86
set BUILD_DIR_X64=build_x64
set OUTPUT_DIR=output

if not exist %BUILD_DIR_X86% mkdir %BUILD_DIR_X86%
if not exist %BUILD_DIR_X64% mkdir %BUILD_DIR_X64%
if not exist %OUTPUT_DIR% mkdir %OUTPUT_DIR%
if not exist %OUTPUT_DIR%\x64 mkdir %OUTPUT_DIR%\x64

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
    echo ERROR: vswhere.exe not found. Please install Visual Studio 2022 or later.
    exit /b 1
)

for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do (
    set "VSINSTALLDIR=%%i\"
)

if not defined VSINSTALLDIR (
    echo ERROR: Visual Studio installation not found.
    exit /b 1
)

echo Found Visual Studio at: %VSINSTALLDIR%
echo.

echo Building x86 version...
echo.

cmake -A Win32 -S . -B %BUILD_DIR_X86%
if errorlevel 1 (
    echo ERROR: CMake x86 configuration failed.
    exit /b 1
)

cmake --build %BUILD_DIR_X86% --config Release
if errorlevel 1 (
    echo ERROR: x86 build failed.
    exit /b 1
)

echo Building x64 version...
echo.

cmake -A x64 -S . -B %BUILD_DIR_X64%
if errorlevel 1 (
    echo ERROR: CMake x64 configuration failed.
    exit /b 1
)

cmake --build %BUILD_DIR_X64% --config Release
if errorlevel 1 (
    echo ERROR: x64 build failed.
    exit /b 1
)

echo Staging files in output directory...
echo.

copy /Y "%BUILD_DIR_X86%\bin\Release\OrpheusClassicSAPI.dll" "%OUTPUT_DIR%\"
copy /Y "%BUILD_DIR_X86%\bin\Release\OrpheusClassicConfig.exe" "%OUTPUT_DIR%\"
copy /Y "%BUILD_DIR_X64%\bin\Release\OrpheusClassicSAPI.dll" "%OUTPUT_DIR%\x64\"
copy /Y "bin\orpheus-classic-host.exe" "%OUTPUT_DIR%\"
robocopy "bin\orpheus" "%OUTPUT_DIR%\orpheus" /MIR /NFL /NDL /NJH /NJS
if errorlevel 8 (
    echo ERROR: Failed to copy the Orpheus engine data.
    exit /b 1
)

echo Building installer...
echo.

set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo ERROR: Inno Setup 6 compiler not found.
    exit /b 1
)

"%ISCC%" /O"%OUTPUT_DIR%" "installer\orpheus.iss"
if errorlevel 1 (
    echo ERROR: Installer build failed.
    exit /b 1
)

echo.
echo Build completed successfully!
echo Installer: %OUTPUT_DIR%\OrpheusClassicSAPI_Setup.exe
endlocal
