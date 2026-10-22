<#
.SYNOPSIS
  Run the test suite inside WSL (the HA test harness does not support native Windows).

.EXAMPLE
  ./scripts/test.ps1                         # full suite with coverage
  ./scripts/test.ps1 -q tests/test_init.py   # any pytest arguments pass through
  ./scripts/test.ps1 setup                   # create/refresh the WSL venv first

  One-time prerequisite inside WSL: uv (https://docs.astral.sh/uv/) at ~/.local/bin/uv.
#>
$ErrorActionPreference = "Stop"

$repo = (wsl -e wslpath -a ($PSScriptRoot | Split-Path -Parent)).Trim()
$venv = '$HOME/.venvs/dyness-test'
$uv = '$HOME/.local/bin/uv'

if ($args.Count -gt 0 -and $args[0] -eq "setup") {
    wsl -e bash -lc "$uv venv --allow-existing -p 3.14 $venv && $uv pip install -p $venv/bin/python -r '$repo/requirements_test.txt'"
    exit $LASTEXITCODE
}

$pytestArgs = if ($args.Count) { $args } else { @("--cov", "--cov-report=term-missing") }
$quoted = ($pytestArgs | ForEach-Object { "'$_'" }) -join " "
wsl -e bash -lc "cd '$repo' && $venv/bin/python -m pytest $quoted"
exit $LASTEXITCODE
