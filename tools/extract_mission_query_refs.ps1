param([Parameter(Mandatory=$true)][string]$AssetRoot,[Parameter(Mandatory=$true)][string]$QueriesPath,[Parameter(Mandatory=$true)][string]$OutputPath,[Parameter(Mandatory=$true)][string]$UAssetLibrary)
$ErrorActionPreference='Stop'
Add-Type -Path $UAssetLibrary
$taskQueryNames=[System.Collections.Generic.HashSet[string]]::new()
foreach($taskRow in (Get-Content -LiteralPath $QueriesPath -Raw -Encoding UTF8 | ConvertFrom-Json).Rows){[void]$taskQueryNames.Add($taskRow.Name)}
$taskRows=[System.Collections.Generic.List[object]]::new()
foreach($taskFile in Get-ChildItem -LiteralPath $AssetRoot -Filter '*.uasset' -Recurse){
    $taskAsset=[UAssetAPI.UAsset]::new($taskFile.FullName,[UAssetAPI.UnrealTypes.EngineVersion]::VER_UE4_27,$null,[UAssetAPI.CustomSerializationFlags]::None)
    $taskMatches=@($taskAsset.GetNameMapIndexList() | ForEach-Object {$_.ToString()} | Where-Object {$taskQueryNames.Contains($_)})
    if($taskMatches.Count -gt 0) {$taskRows.Add([pscustomobject]@{blueprint=$taskFile.BaseName;queries=$taskMatches})}
}
$taskRows.ToArray() | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
[pscustomobject]@{blueprints=(Get-ChildItem -LiteralPath $AssetRoot -Filter '*.uasset' -Recurse).Count;with_queries=$taskRows.Count;output=$OutputPath} | ConvertTo-Json -Compress
