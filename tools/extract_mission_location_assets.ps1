param([Parameter(Mandatory=$true)][string]$AssetPath,[Parameter(Mandatory=$true)][string]$OutputPath,[Parameter(Mandatory=$true)][string]$UAssetLibrary)
$ErrorActionPreference='Stop'
Add-Type -Path $UAssetLibrary
$taskAsset=[UAssetAPI.UAsset]::new($AssetPath,[UAssetAPI.UnrealTypes.EngineVersion]::VER_UE4_27,$null,[UAssetAPI.CustomSerializationFlags]::None)
$taskRows=[System.Collections.Generic.List[object]]::new()
function Find-Property($Properties,[string]$Name) { foreach($property in $Properties) { if($property.Name.ToString() -eq $Name) { return $property } }; return $null }
foreach($taskExport in $taskAsset.Exports) {
    if(-not $taskExport.PSObject.Properties['Data']) { continue }
    $taskTags=Find-Property $taskExport.Data 'GameplayTags'
    if($null -eq $taskTags) { continue }
    $taskRoot=Find-Property $taskExport.Data 'RootComponent'
    if($null -eq $taskRoot -or $taskRoot.Value.Index -le 0) { continue }
    $taskComponent=$taskAsset.Exports[$taskRoot.Value.Index-1]
    $taskLocation=Find-Property $taskComponent.Data 'RelativeLocation'
    if($null -eq $taskLocation) { continue }
    $taskVector=$taskLocation.Value[0].Value
    $taskNames=@($taskTags.Value | ForEach-Object { foreach($taskName in $_.Value) { $taskName.ToString() } })
    $taskClassIndex=$taskExport.ClassIndex.Index
    $taskClass=if($taskClassIndex -lt 0){$taskAsset.Imports[-$taskClassIndex-1].ObjectName.ToString()}else{''}
    $taskRows.Add([pscustomobject]@{name=$taskExport.ObjectName.ToString();class=$taskClass;tags=$taskNames;x=[double]$taskVector.X;y=[double]$taskVector.Y;z=[double]$taskVector.Z})
}
$taskRows.ToArray() | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
[pscustomobject]@{exports=$taskAsset.Exports.Count;matches=$taskRows.Count;output=$OutputPath} | ConvertTo-Json -Compress
