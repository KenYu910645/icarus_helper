param([string]$Repak,[string]$Content,[string]$UAssetLibrary,[string]$OutputRoot)
$ErrorActionPreference='Stop'
Add-Type -Path $UAssetLibrary
function Read-AnimalAsset([string]$Pak,[string]$AssetPath) {
    $memory=[System.IO.MemoryStream]::new()
    foreach($extension in @('uasset','uexp')) {
        $info=[System.Diagnostics.ProcessStartInfo]::new()
        $info.FileName=$Repak
        $info.UseShellExecute=$false
        $info.RedirectStandardOutput=$true
        $info.CreateNoWindow=$true
        foreach($argument in @('get',(Join-Path $Content "Paks/$Pak"),"$AssetPath.$extension")) { $info.ArgumentList.Add($argument) }
        $process=[System.Diagnostics.Process]::Start($info)
        $process.StandardOutput.BaseStream.CopyTo($memory)
        $process.WaitForExit()
        if($process.ExitCode -ne 0){throw 'repak read failed'}
    }
    $memory.Position=0
    $reader=[UAssetAPI.AssetBinaryReader]::new($memory,$null)
    $asset=[UAssetAPI.UAsset]::new($reader,[UAssetAPI.UnrealTypes.EngineVersion]::VER_UE4_27,$null,$false,[UAssetAPI.CustomSerializationFlags]::None)
    $json=$asset.SerializeJson($false) | ConvertFrom-Json -Depth 100
    return $json
}
$targets=@(
    @('pakchunk0_s20-WindowsNoEditor.pak','Icarus/Content/Data/Character/C_MountExperienceGrowth'),
    @('pakchunk0_s20-WindowsNoEditor.pak','Icarus/Content/Data/Character/C_PetExperienceGrowth'),
    @('pakchunk0_s20-WindowsNoEditor.pak','Icarus/Content/BP/Mounts/BP_Mount_Base'),
    @('pakchunk0_s20-WindowsNoEditor.pak','Icarus/Content/BP/Mounts/BP_Tame_Base'),
    @('pakchunk0_s33-WindowsNoEditor.pak','Icarus/Content/UI/Windows/UMG_MountInterface_V2'),
    @('pakchunk0_s33-WindowsNoEditor.pak','Icarus/Content/UI/Components/UMG_CharacterInfo')
)
foreach($target in $targets) {
    $data=Read-AnimalAsset $target[0] $target[1]
    $name=Split-Path $target[1] -Leaf
    if($name.StartsWith('C_')) {
        $keys=@($data.Exports[0].Data[0].Value | Where-Object Name -eq 'Keys')[0].Value
        $summary=[ordered]@{asset=$target[1];pak=$target[0];keys=@($keys | ForEach-Object { $_.Value[0].Value })}
    } else {
        $matches=@($data.Exports | Where-Object {($_ | ConvertTo-Json -Compress -Depth 100) -match 'GetLevel|GrowthCurve|ExperienceCurve|AI_Mounts|AI_Pets'})
        $summary=[ordered]@{asset=$target[1];pak=$target[0];names=$data.NameMap;imports=$data.Imports;exports=$matches}
    }
    $summary | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $OutputRoot "$name.json") -Encoding utf8
    Write-Output "$name matches=$(@($summary.exports).Count) keys=$(@($summary.keys).Count)"
}
