# Build the pinned TypeTreeGeneratorAPI with our generic/SerializeReference fix.
# Requires Git, .NET 9 SDK and Visual Studio C++ build tools; end users use the ZIP.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$src = Join-Path $root "build\typetree-upstream"
$out = Join-Path $root "build\typetree-native"
$native = Join-Path $root "yakupatch\native"
$revision = "df79bd6a786d4ba52ed4a7b3d40047ecf578256b"
if (-not (Test-Path $src)) {
    git clone https://github.com/UnityPy-Org/TypeTreeGeneratorAPI.git $src
    if ($LASTEXITCODE -ne 0) { throw "Cloning TypeTreeGeneratorAPI failed" }
    git -C $src checkout --detach $revision
    if ($LASTEXITCODE -ne 0) { throw "Checking out TypeTreeGeneratorAPI failed" }
}
if ((git -C $src rev-parse HEAD) -ne $revision) { throw "Unexpected TypeTreeGeneratorAPI revision" }
git -C $src submodule update --init UnityCsReference
if ($LASTEXITCODE -ne 0) { throw "Fetching UnityCsReference failed" }
$patch = Join-Path $PSScriptRoot "typetree.patch"
git -C $src diff --quiet
if ($LASTEXITCODE -eq 0) {
    git -C $src apply --check $patch
    if ($LASTEXITCODE -ne 0) { throw "TypeTreeGeneratorAPI patch does not apply" }
    git -C $src apply $patch
    if ($LASTEXITCODE -ne 0) { throw "Applying TypeTreeGeneratorAPI patch failed" }
} else {
    git -C $src apply --reverse --check $patch
    if ($LASTEXITCODE -ne 0) { throw "Unexpected changes in TypeTreeGeneratorAPI checkout" }
}
# YakuPatch uses AssetStudio and AssetsTools; AssetRipper is not used.
dotnet publish "$src\TypeTreeGeneratorAPI\TypeTreeGeneratorAPI.csproj" -c Release -r win-x64 `
    -p:EnableAssetRipper=false --source https://api.nuget.org/v3/index.json -o $out
if ($LASTEXITCODE -ne 0) { throw "Building TypeTreeGeneratorAPI failed" }
New-Item -ItemType Directory -Force $native | Out-Null
Copy-Item "$out\TypeTreeGeneratorAPI.dll", "$out\capstone.dll" $native -Force
Copy-Item "$src\LICENSE" "$native\TypeTreeGeneratorAPI-LICENSE.txt" -Force
Copy-Item "$src\TypeTreeGeneratorAPI\TypeTreeGenerator\AssetStudio\AssetStudioUtility\LICENSE" "$native\AssetStudio-LICENSE.txt" -Force
Copy-Item "$src\UnityCsReference\LICENSE.md" "$native\UnityCsReference-LICENSE.txt" -Force
Write-Host "Built patched TypeTreeGeneratorAPI in $native"
dotnet build "$root\tests\fixtures\typetree\GenericFields.csproj" -c Release -o "$root\build\typetree-fixture" `
    "-p:BaseIntermediateOutputPath=$root/build/typetree-fixture-obj/" --source https://api.nuget.org/v3/index.json
if ($LASTEXITCODE -ne 0) { throw "Building type-tree regression fixture failed" }
