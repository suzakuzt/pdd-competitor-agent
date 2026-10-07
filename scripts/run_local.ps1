#requires -Version 5.1
<#
.SYNOPSIS
Run the local PDD CLI with a verified Python 3.10+ interpreter.
.EXAMPLE
.\scripts\run_local.ps1
.EXAMPLE
.\scripts\run_local.ps1 summary --run-id <run_id>
.EXAMPLE
.\scripts\run_local.ps1 -ProjectRoot 'C:\Projects\PDDCompetitorAgent' --data-dir data validate
.NOTES
PDD_PYTHON, if set, must name one Python executable (not a command with arguments).
It is an explicit override: an invalid override fails instead of silently selecting
another interpreter. This launcher does not change PATH or execution policy.
All relative CLI paths are resolved from ProjectRoot. No arguments means validate.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function Test-PythonInterpreter {
    param([string]$Executable, [string[]]$PrefixArgs = @())

    # No shell is involved. Probe output never enters CLI stdout.
    $probe = New-Object System.Diagnostics.Process
    $probe.StartInfo.FileName = $Executable
    $prefix = if ($PrefixArgs.Count) { ($PrefixArgs -join ' ') + ' ' } else { '' }
    $probe.StartInfo.Arguments = $prefix + '-B -c "import json,sys; print(json.dumps(dict(executable=sys.executable,version=list(sys.version_info[:3])))); raise SystemExit(0 if sys.version_info >= (3,10) else 1)"'
    $probe.StartInfo.UseShellExecute = $false
    $probe.StartInfo.CreateNoWindow = $true
    $probe.StartInfo.RedirectStandardOutput = $true
    $probe.StartInfo.RedirectStandardError = $true
    try {
        if (-not $probe.Start()) { return $null }
        $stdout = $probe.StandardOutput.ReadToEndAsync()
        $stderr = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit(10000)) {
            try { $probe.Kill() } catch { }
            return $null
        }
        $probe.WaitForExit()
        if ($probe.ExitCode -ne 0) { return $null }
        $info = $stdout.Result | ConvertFrom-Json
        if ($info.version.Count -ne 3 -or
            $info.version[0] -lt 3 -or
            ($info.version[0] -eq 3 -and $info.version[1] -lt 10)) {
            return $null
        }
        return [pscustomobject]@{
            Executable = $Executable
            PrefixArgs = $PrefixArgs
            ResolvedExecutable = $info.executable
            Version = ($info.version -join '.')
        }
    } catch {
        # Windows Store aliases, missing runtimes and malformed output are not
        # usable Python installations; try the next non-explicit candidate.
        return $null
    } finally {
        $probe.Dispose()
    }
}

function Resolve-PythonInterpreter {
    if (-not [string]::IsNullOrWhiteSpace($env:PDD_PYTHON)) {
        $explicitPython = Test-PythonInterpreter -Executable $env:PDD_PYTHON
        if ($null -eq $explicitPython) {
            throw 'PDD_PYTHON does not identify a working Python 3.10+ executable. Set it to an executable path without command-line arguments.'
        }
        return $explicitPython
    }

    foreach ($name in @('py', 'python')) {
        $candidate = Get-Command -Name $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $candidate) {
            $prefixArgs = if ($name -eq 'py') { @('-3') } else { @() }
            $usable = Test-PythonInterpreter -Executable $candidate.Source -PrefixArgs $prefixArgs
            if ($null -ne $usable) { return $usable }
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $runtimePython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $runtimePython -PathType Leaf) {
            $usable = Test-PythonInterpreter -Executable $runtimePython
            if ($null -ne $usable) { return $usable }
        }
    }
    throw 'No working Python 3.10+ interpreter was found. Set PDD_PYTHON to an installed python.exe, or install Python 3.10+.'
}

$savedPythonUtf8 = [Environment]::GetEnvironmentVariable('PYTHONUTF8', 'Process')
$savedConsoleEncoding = [Console]::OutputEncoding
$pushedLocation = $false
$resultCode = 2
try {
    $env:PYTHONUTF8 = '1'
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedRoot 'pdd_monitor\__main__.py') -PathType Leaf)) {
        throw 'ProjectRoot must contain pdd_monitor\__main__.py.'
    }
    $python = Resolve-PythonInterpreter
    [Console]::Error.WriteLine(('PDD Python: {0} ({1})' -f $python.ResolvedExecutable, $python.Version))
    Push-Location -LiteralPath $resolvedRoot
    $pushedLocation = $true
    if ($null -eq $CliArgs -or $CliArgs.Count -eq 0) { $CliArgs = @('validate') }
    & $python.Executable @($python.PrefixArgs) -B -m pdd_monitor @CliArgs
    $resultCode = $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine(('PDD launcher error: {0}' -f $_.Exception.Message))
    $resultCode = 2
} finally {
    if ($pushedLocation) { Pop-Location }
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', $savedPythonUtf8, 'Process')
    [Console]::OutputEncoding = $savedConsoleEncoding
}
exit $resultCode
