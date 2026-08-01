[CmdletBinding()]
param(
    [switch]$SkipTests,
    [string]$PythonCommand = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$requirementsLock = Join-Path $repoRoot "requirements.lock"
$targetExe = Join-Path $repoRoot "dist\ComfyUI-EXIF-viewer.exe"
$checksumFile = Join-Path $repoRoot "dist\SHA256SUMS.txt"
$versionFile = Join-Path $repoRoot "packaging\version_info.txt"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Executable $($Arguments -join ' ')"
    }
}

Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Invoke-Checked $PythonCommand @("-m", "venv", ".venv")
    }

    if (-not (Test-Path -LiteralPath $requirementsLock)) {
        throw "Missing dependency lock file: $requirementsLock"
    }

    Invoke-Checked $venvPython @(
        "-m", "pip", "install", "--require-hashes", "-r", $requirementsLock
    )
    Invoke-Checked $venvPython @("-m", "pip", "check")

    if (-not $SkipTests) {
        Invoke-Checked $venvPython @(
            "-m", "unittest", "discover", "-s", "tests", "-v"
        )
        Invoke-Checked $venvPython @(
            "-m", "compileall", "-q",
            "comfy_exif_viewer.py",
            "comfy_metadata_reader.py",
            "comfy_workflow_prompts.py",
            "tests"
        )
    }

    if (Test-Path -LiteralPath $targetExe) {
        Remove-Item -LiteralPath $targetExe -Force
    }

    Invoke-Checked $venvPython @(
        "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--noupx",
        "--collect-data", "tkinterdnd2",
        "--version-file", $versionFile,
        "--name", "ComfyUI-EXIF-viewer",
        "comfy_exif_viewer.py"
    )

    if (-not (Test-Path -LiteralPath $targetExe)) {
        throw "PyInstaller did not create the expected EXE: $targetExe"
    }

    Invoke-Checked $targetExe @("--version")

    $signingValues = @(
        $env:SIGNTOOL_EXE,
        $env:CODE_SIGN_CERT_SUBJECT,
        $env:CODE_SIGN_TIMESTAMP_URL
    )
    $signingRequested = @($signingValues | Where-Object { $_ }).Count -gt 0
    if ($signingRequested) {
        if (@($signingValues | Where-Object { -not $_ }).Count -gt 0) {
            throw (
                "Signing requires SIGNTOOL_EXE, CODE_SIGN_CERT_SUBJECT, " +
                "and CODE_SIGN_TIMESTAMP_URL."
            )
        }
        Invoke-Checked $env:SIGNTOOL_EXE @(
            "sign",
            "/n", $env:CODE_SIGN_CERT_SUBJECT,
            "/fd", "SHA256",
            "/tr", $env:CODE_SIGN_TIMESTAMP_URL,
            "/td", "SHA256",
            $targetExe
        )
        Invoke-Checked $env:SIGNTOOL_EXE @("verify", "/pa", "/v", $targetExe)
    }
    else {
        Write-Warning "Built an unsigned EXE. Configure signing before public release."
    }

    $signature = Get-AuthenticodeSignature -LiteralPath $targetExe
    if ($signingRequested -and $signature.Status -ne "Valid") {
        throw "Authenticode verification failed: $($signature.StatusMessage)"
    }

    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $targetExe).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $checksumFile -Value "$hash  ComfyUI-EXIF-viewer.exe" -Encoding ascii
    Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination (Join-Path $repoRoot "dist\LICENSE.txt") -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "THIRD_PARTY_NOTICES.md") -Destination (Join-Path $repoRoot "dist\THIRD_PARTY_NOTICES.md") -Force

    Write-Host "Built: $targetExe"
    Write-Host "Signature: $($signature.Status)"
    Write-Host "SHA-256: $hash"
}
finally {
    Pop-Location
}
