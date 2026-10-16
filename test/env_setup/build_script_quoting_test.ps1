<#
.SYNOPSIS
    Robustness test for backend\scripts\build_extensions.cmd (argument handling + path quoting).

.DESCRIPTION
    Runs the build script in --if-missing mode (nothing is built or reinstalled while all four
    extensions import) through directory junctions whose paths contain spaces, parentheses and
    "&" - i.e. the kinds of characters found in "C:\VS Studio 2026" or "C:\Program Files (x86)".
    The junctions live under %TEMP% and are removed with `rmdir` (removes the link only, never
    the target). Also checks --help, an unknown name (exit 2) and target ordering/deduplication.

    Run:  pwsh -File E:\14-OCT-2026\Img3D\test\env_setup\build_script_quoting_test.ps1
    Log:  test\env_setup\build_script_quoting.log
#>
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$backend = (Resolve-Path (Join-Path $here '..\..\backend')).Path
$script = Join-Path $backend 'scripts\build_extensions.cmd'
$log = Join-Path $here 'build_script_quoting.log'
$failures = 0
$lines = [System.Collections.Generic.List[string]]::new()

function Report([string]$name, [bool]$ok, [string]$detail) {
    $status = if ($ok) { 'PASS' } else { 'FAIL' }
    if (-not $ok) { $script:failures++ }
    $line = "{0}  {1}  {2}" -f $status, $name, $detail
    $lines.Add($line); Write-Host $line
}

function Invoke-Build([string]$scriptPath, [string]$arguments) {
    $out = cmd /c "`"$scriptPath`" $arguments 2>&1"
    return @{ Code = $LASTEXITCODE; Out = ($out -join "`n") }
}

# 1. --help and unknown extension
$r = Invoke-Build $script '--help'
Report 'help' ($r.Code -eq 0 -and $r.Out -match 'Usage:') "exit $($r.Code)"
$r = Invoke-Build $script 'bogus'
Report 'unknown extension -> exit 2' ($r.Code -eq 2 -and $r.Out -match 'unknown extension') "exit $($r.Code)"

# 2. Ordering / dedup + quoting through junctions with awkward paths
$cases = @(
    @{ Name = 'plain path'; Dir = $null },
    @{ Name = 'spaces + (x86)'; Dir = 'img3d quoting (x86) test' },
    @{ Name = 'spaces + &'; Dir = 'img3d quoting & more test' }
)
foreach ($case in $cases) {
    $junction = $null
    $base = $null
    try {
        if ($case.Dir) {
            $base = Join-Path $env:TEMP $case.Dir
            New-Item -ItemType Directory -Force -Path $base | Out-Null
            $junction = Join-Path $base 'backend'
            cmd /c "mklink /J `"$junction`" `"$backend`"" | Out-Null
            $target = Join-Path $junction 'scripts\build_extensions.cmd'
        } else {
            $target = $script
        }
        $r = Invoke-Build $target 'o_voxel CUMESH cumesh --if-missing'
        $ordered = $r.Out -match '\[build\] targets   : cumesh o_voxel\s*(\n|$)'
        $skipped = ([regex]::Matches($r.Out, 'skipped \(--if-missing\)')).Count -eq 2
        $timed = $r.Out -match '\[build\] total time: \d+ s'
        Report "if-missing via $($case.Name)" ($r.Code -eq 0 -and $ordered -and $skipped -and $timed) `
            "exit $($r.Code), ordered+dedup=$ordered, skipped2=$skipped, timer=$timed"
        if ($r.Code -ne 0) { $lines.Add($r.Out) }
    } finally {
        if ($junction -and (Test-Path -LiteralPath $junction)) { cmd /c "rmdir `"$junction`"" | Out-Null }
        if ($base -and (Test-Path -LiteralPath $base)) { Remove-Item -LiteralPath $base -Force }
    }
}
Report 'backend intact after junction cleanup' (Test-Path (Join-Path $backend 'python\python.exe')) ''

$lines.Add($(if ($failures) { "$failures FAILURE(S)" } else { 'ALL PASSED' }))
Set-Content -Path $log -Value $lines -Encoding utf8
exit [int]($failures -gt 0)
