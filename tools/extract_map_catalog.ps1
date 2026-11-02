param(
    [Parameter(Mandatory=$true)][string]$AssetRoot,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [Parameter(Mandatory=$true)][string]$UAssetLibrary
)
$ErrorActionPreference = 'Stop'
Add-Type -Path $UAssetLibrary
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

function Find-Property($Properties, [string]$Name) {
    foreach ($property in $Properties) {
        if ($property.Name.ToString() -eq $Name) { return $property }
    }
    return $null
}

function Component-WorldOffset($Asset, $Component) {
    $offsetX=0.0; $offsetY=0.0; $offsetZ=0.0
    if($null -ne $Asset.WorldTileInfo) {
        $offsetX += $Asset.WorldTileInfo.Position[0]
        $offsetY += $Asset.WorldTileInfo.Position[1]
        $offsetZ += $Asset.WorldTileInfo.Position[2]
    }
    $current=$Component
    $supported=$true
    for($depth=0;$depth -lt 12;$depth++) {
        $location=Find-Property $current.Data 'RelativeLocation'
        if($null -ne $location) {
            $vector=$location.Value[0].Value
            $offsetX += $vector.X; $offsetY += $vector.Y; $offsetZ += $vector.Z
        }
        $rotation=Find-Property $current.Data 'RelativeRotation'
        if($null -ne $rotation) {
            $rotator=$rotation.Value[0].Value
            if([Math]::Abs($rotator.Pitch)+[Math]::Abs($rotator.Yaw)+[Math]::Abs($rotator.Roll) -gt 0.001) {$supported=$false}
        }
        $scale=Find-Property $current.Data 'RelativeScale3D'
        if($null -ne $scale) {
            $vector=$scale.Value[0].Value
            if([Math]::Abs($vector.X-1)+[Math]::Abs($vector.Y-1)+[Math]::Abs($vector.Z-1) -gt 0.001) {$supported=$false}
        }
        $parent=Find-Property $current.Data 'AttachParent'
        if($null -eq $parent -or $parent.Value.Index -le 0){break}
        $current=$Asset.Exports[$parent.Value.Index-1]
    }
    return @{x=$offsetX;y=$offsetY;z=$offsetZ;supported=$supported}
}

foreach ($mapFile in Get-ChildItem -LiteralPath $AssetRoot -Filter '*.umap' | Where-Object { $_.BaseName -match '^(T019_Generated|heightmap)_x\d+_y\d+$' }) {
    $destination = Join-Path $OutputRoot ($mapFile.BaseName + '.json')
    if ((Test-Path -LiteralPath $destination) -and ((Get-Content -LiteralPath $destination -Raw | ConvertFrom-Json).schema_version -eq 4)) { continue }
    $packagePath = if ($mapFile.BaseName.StartsWith('heightmap')) { '/Game/Maps/Terrain_019_DLC/heightmap/' + $mapFile.BaseName } else { '/Game/Maps/Terrain_019_DLC/Sublevels/Generated/' + $mapFile.BaseName }
    $asset = [UAssetAPI.UAsset]::new($mapFile.FullName, [UAssetAPI.UnrealTypes.EngineVersion]::VER_UE4_27, $null, [UAssetAPI.CustomSerializationFlags]::None)
    $points = [System.Collections.Generic.List[object]]::new()
    $classes = @{}
    foreach ($export in $asset.Exports) {
        $objectName = $export.ObjectName.ToString()
        if ($objectName.StartsWith('FLODFISMComponent') -or $objectName.StartsWith('FoliageInstancedStaticMeshComponent')) {
            $meshProperty = Find-Property $export.Data 'StaticMesh'
            $countProperty = Find-Property $export.Data 'NumBuiltInstances'
            $boundsProperty = Find-Property $export.Data 'BuiltInstanceBounds'
            if ($null -eq $meshProperty -or $null -eq $countProperty -or $null -eq $boundsProperty -or $countProperty.Value -lt 1) { continue }
            $index = $meshProperty.Value.Index
            $parts = [System.Collections.Generic.List[string]]::new()
            while ($index -lt 0) {
                $import = $asset.Imports[-$index - 1]
                $parts.Add($import.ObjectName.ToString())
                $index = $import.OuterIndex.Index
            }
            $mesh = ($parts.ToArray() | Select-Object -Last 1) + '/' + ($parts.ToArray() | Select-Object -First 1)
            # Decorative decals/planes and unclassified geometry are not resources.
            $groupKind = if ($mesh -match '/TRE/' -and $mesh -notmatch 'Kelp') { 'tree' } elseif ($mesh -match '/RCK/|ROCK_') { 'surface_resource' } elseif ($mesh -match '/HRB/|/FOL/|Kelp') { 'plant' } else { '' }
            if (-not $groupKind) { continue }
            $box = $boundsProperty.Value[0].Value
            if (($box.Max.X-$box.Min.X) -lt 1 -and ($box.Max.Y-$box.Min.Y) -lt 1) { continue }
            $worldOffset=Component-WorldOffset $asset $export
            if(-not $worldOffset.supported){continue}
            $points.Add([ordered]@{
                id=$objectName
                actor_path=('{0}.{1}:PersistentLevel.{2}' -f $packagePath,$mapFile.BaseName,$objectName)
                kind=$groupKind
                x=([double]$box.Min.X+[double]$box.Max.X)/2+$worldOffset.x
                y=([double]$box.Min.Y+[double]$box.Max.Y)/2+$worldOffset.y
                z=([double]$box.Min.Z+[double]$box.Max.Z)/2+$worldOffset.z
                mesh=$mesh
                generated_instance_count=[int]$countProperty.Value
                region_bounds=@{min_x=[double]$box.Min.X+$worldOffset.x; min_y=[double]$box.Min.Y+$worldOffset.y; max_x=[double]$box.Max.X+$worldOffset.x; max_y=[double]$box.Max.Y+$worldOffset.y}
                world_offset=$worldOffset
                transform_method='BuiltInstanceBounds + component/attach-parent RelativeLocation + WorldTileInfo.Position; identity rotation/scale required'
                tile=$mapFile.BaseName
            })
            continue
        }
        if (-not $objectName.StartsWith('BP_')) { continue }
        $classKey = $objectName -replace '\d+(_GENERATED_\d+)?$',''
        if (-not $classes.ContainsKey($classKey)) { $classes[$classKey] = 0 }
        $classes[$classKey]++
        $kind = if ($objectName.StartsWith('BP_Voxel_GEN_')) { 'ore' } elseif ($objectName.StartsWith('BP_CaveEntrance_')) { 'cave' } elseif ($objectName.StartsWith('BP_InteractableLake') -or $objectName.StartsWith('BP_InteractableRiver')) { 'water' } elseif ($objectName -match 'Deep_Mining|Geyser|Exotic|MetaDeposit') { 'special' } else { '' }
        if (-not $kind -or -not $export.PSObject.Properties['Data']) { continue }
        $rootProperty = Find-Property $export.Data 'RootComponent'
        if ($null -eq $rootProperty -or $rootProperty.Value.Index -le 0) { continue }
        $component = $asset.Exports[$rootProperty.Value.Index - 1]
        $locationProperty = Find-Property $component.Data 'RelativeLocation'
        if ($null -eq $locationProperty -or $locationProperty.Value.Count -lt 1) { continue }
        $vector = $locationProperty.Value[0].Value
        # Actor root locations already include their own translation. Add only
        # the level position here; generated resources have no attach parent.
        $levelOffset=@(0,0,0)
        if($null -ne $asset.WorldTileInfo){$levelOffset=$asset.WorldTileInfo.Position}
        $seedProperty = Find-Property $export.Data 'VoxelInstanceSeed'
        $poolProperty = Find-Property $export.Data 'ResourcePool'
        $poolName = $null
        if ($null -ne $poolProperty) {
            $row = Find-Property $poolProperty.Value 'RowName'
            if ($null -ne $row) { $poolName = $row.Value.ToString() }
        }
        $points.Add([ordered]@{
            id=$objectName
            actor_path=('{0}.{1}:PersistentLevel.{2}' -f $packagePath,$mapFile.BaseName,$objectName)
            kind=$kind
            x=[double]$vector.X+$levelOffset[0]
            y=[double]$vector.Y+$levelOffset[1]
            z=[double]$vector.Z+$levelOffset[2]
            seed=$(if ($null -ne $seedProperty) { [int]$seedProperty.Value } else { $null })
            pool=$poolName
            tile=$mapFile.BaseName
        })
    }
    $result = [ordered]@{schema_version=4;tile=$mapFile.BaseName;export_count=$asset.Exports.Count;raw_export_count=@($asset.Exports | Where-Object {$_.GetType().Name -eq 'RawExport'}).Count;points=@($points.ToArray());actor_classes=$classes}
    $temporary = $destination + '.partial'
    $result | ConvertTo-Json -Depth 6 -Compress | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $destination -Force
    Write-Output ($mapFile.BaseName + ': ' + $points.Count + ' points')
    $asset = $null
    [GC]::Collect()
}
