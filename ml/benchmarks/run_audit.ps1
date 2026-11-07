# Run serially so two variants never compete for the same GPU.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateRange(1,65535)][int]$WorkerPort,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]*$')][string]$RunName,
    [string]$Dev,
    [string]$Reference,
    [switch]$SyntheticOnly,
    [switch]$Ablations
)
$ErrorActionPreference='Stop'
$mlRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runDir=Join-Path (Join-Path $mlRoot 'data') $RunName
if(Test-Path -LiteralPath $runDir){throw "Choose a new RunName. Refusing to overwrite $runDir"}
$python=Join-Path $mlRoot '.venv/Scripts/python.exe'
if(-not(Test-Path -LiteralPath $python)){throw 'Prepare the local ML environment first.'}
if(-not $SyntheticOnly){
    if(-not $Dev){$Dev=Join-Path $mlRoot '.tmp/dev-ml-repair-2111/dev.json'}
    if(-not $Reference){$Reference=Join-Path $mlRoot 'data/blind15-comparison-20261108/reference.json'}
    $Dev=(Resolve-Path -LiteralPath $Dev).Path
    $Reference=(Resolve-Path -LiteralPath $Reference).Path
}
[void](New-Item -ItemType Directory -Path $runDir)
function Invoke-Variant([string]$Set,[string]$Name,[string]$Components,[string[]]$Switches){
    $file=Join-Path $runDir "wire-$Set-$Name.jsonl"
    $params=@('-m','benchmarks.audit_run','--port',$WorkerPort,'--profile','source-events',
              '--variant',$Components,'--out',$file) + $Switches
    if(-not $SyntheticOnly){$params+=@('--dev',$Dev,'--reference',$Reference)}
    & $python @params
    if($LASTEXITCODE -ne 0){throw "Incomplete run: $file"}
}
Push-Location $mlRoot
try{
    $sets=if($SyntheticOnly){@('pairs')}else{@('human','pairs','voice')}
    foreach($set in $sets){
        $options=switch($set){'pairs'{'--pairs'} 'voice'{'--voices'} default{}}
        Invoke-Variant $set 'baseline' 'baseline' @($options | Where-Object {$_})
        Invoke-Variant $set 'final' 'trace,policy,score,sentinel' @($options | Where-Object {$_})
        if($set -ne 'voice'){
            $argsCompare=@('-m','benchmarks.audit_compare','--baseline',(Join-Path $runDir "wire-$set-baseline.jsonl"),
                    '--candidate',(Join-Path $runDir "wire-$set-final.jsonl"))
            if($Dev){$argsCompare+=@('--dev',$Dev)}
            & $python @argsCompare | Set-Content (Join-Path $runDir "metrics-$set.json") -Encoding utf8
            if($LASTEXITCODE -ne 0){throw "Failed comparison: $set"}
        }
    }
    if($Ablations){
        foreach($components in 'policy,score,sentinel','trace,score,sentinel','trace,policy,sentinel','trace,policy,score'){
            Invoke-Variant 'ablation' ($components.Replace(',','-')) $components @('--pairs','--ablation')
        }
    }
}finally{Pop-Location}
