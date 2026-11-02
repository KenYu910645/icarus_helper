param([string]$AssetRoot,[string]$DescriptionPath,[string]$OutputPath,[string]$UAssetLibrary)
$ErrorActionPreference='Stop'
Add-Type -Path $UAssetLibrary
$descriptions=Get-Content -LiteralPath $DescriptionPath -Raw | ConvertFrom-Json
$rows=[System.Collections.Generic.List[object]]::new()
for($index=0;$index -lt $descriptions.Rows.Count;$index++) {
    $row=$descriptions.Rows[$index]
    $reference=$row.FoliageType
    if(-not $reference -or $reference -eq 'None'){continue}
    $assetPath=Join-Path $AssetRoot (($reference.Split('.')[0] -replace '^/Game/','Icarus/Content/')+'.uasset')
    if(-not(Test-Path -LiteralPath $assetPath)){continue}
    $asset=[UAssetAPI.UAsset]::new($assetPath,[UAssetAPI.UnrealTypes.EngineVersion]::VER_UE4_27,$null,[UAssetAPI.CustomSerializationFlags]::None)
    $mesh=$null
    foreach($export in $asset.Exports) {
        if(-not $export.PSObject.Properties['Data']){continue}
        $property=$export.Data|Where-Object {$_.Name.ToString() -in @('Mesh','StaticMesh')}|Select-Object -First 1
        if($null -eq $property -or $null -eq $property.Value){continue}
        $meshIndex=$property.Value.Index
        $parts=[System.Collections.Generic.List[string]]::new()
        while($meshIndex -lt 0) {
            $import=$asset.Imports[-$meshIndex-1]
            $parts.Add($import.ObjectName.ToString())
            $meshIndex=$import.OuterIndex.Index
        }
        if($parts.Count -gt 0){$mesh=$parts[$parts.Count-1]+'/'+$parts[0];break}
    }
    $rows.Add([ordered]@{record_index=$index;name=$row.Name;mesh=$mesh;tags=@($row.FoliageTags.GameplayTags|ForEach-Object {$_.TagName});view_trace_actor=$row.ViewTraceActor;disabled=[bool]$row.bDisabled})
}
$rows.ToArray() | ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output ('Foliage types: '+$rows.Count+'; mesh matched: '+@($rows|Where-Object {$_.mesh}).Count)
