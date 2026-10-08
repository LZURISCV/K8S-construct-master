param([string]$GoExecutable = 'go')
$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
$oldCgo = $env:CGO_ENABLED
$oldOS = $env:GOOS
$oldArch = $env:GOARCH
try {
  $env:CGO_ENABLED = '0'
  Remove-Item Env:GOOS,Env:GOARCH -ErrorAction SilentlyContinue
  & $GoExecutable test ./...
  if ($LASTEXITCODE -ne 0) { throw 'Go tests failed' }
  & $GoExecutable vet ./...
  if ($LASTEXITCODE -ne 0) { throw 'Go vet failed' }
  New-Item -ItemType Directory -Force -Path bin | Out-Null
  foreach ($arch in @('amd64','arm64','riscv64')) {
    $env:GOOS = 'linux'; $env:GOARCH = $arch
    & $GoExecutable build -trimpath '-ldflags=-s -w -X main.version=0.1.2' -o "bin/rcs-linux-$arch" .
    if ($LASTEXITCODE -ne 0) { throw "Build failed: linux/$arch" }
  }
  $env:GOOS = 'windows'; $env:GOARCH = 'amd64'
  & $GoExecutable build -trimpath '-ldflags=-s -w -X main.version=0.1.2' -o bin/rcs-windows-amd64.exe .
  if ($LASTEXITCODE -ne 0) { throw 'Windows build failed' }
  $checksums = Get-ChildItem -LiteralPath bin -File -Filter 'rcs-*' | Sort-Object Name | ForEach-Object { '{0}  bin/{1}' -f (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant(),$_.Name }
  $checksums | Set-Content -LiteralPath bin/SHA256SUMS -Encoding ascii
  $checksums
} finally {
  $env:CGO_ENABLED=$oldCgo; $env:GOOS=$oldOS; $env:GOARCH=$oldArch
  Pop-Location
}
