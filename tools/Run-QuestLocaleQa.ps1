[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$AuthorArchive,
    [string]$InstalledPack,
    [string]$Jdk = $env:JAVA_HOME
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path "$PSScriptRoot/../../..").Path
$packSource = (Resolve-Path "$PSScriptRoot/..").Path
$author = (Resolve-Path -LiteralPath $AuthorArchive).Path
if (!$InstalledPack) { $InstalledPack = "$project/Minecraft/Packs/Escape The End" }
$installed = (Resolve-Path -LiteralPath $InstalledPack).Path
if (!$Jdk) { $Jdk = Split-Path -Parent (Split-Path -Parent (Get-Command javac.exe).Source) }

$cache = "$project/Minecraft/Launcher"
$shared = "$cache/Shared"
$java = "$cache/JavaRuntimes/runtime/windows-x64/java-17/bin/java.exe"
$profileId = '1.20.1-forge-47.4.10'
$assemblyRoot = "$cache/Runtime/e86ff2d180aeab13cf0d77bf6f0d1b1fceaae5ec8ae8d8969cb68bb9ecf49cab"
$forgeJar = "$shared/libraries/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-client.jar"
foreach ($inputFile in @(
    @{Path=$java; Algorithm='SHA256'; Hash='1977F302375ADBB920D41DAC65C7E22EB9C2ED8E1E8D6258964154FF16F14406'},
    @{Path=$forgeJar; Algorithm='SHA1'; Hash='4D8A9A63DC16A45D7FC5C54C627234F601D0CC17'},
    @{Path="$assemblyRoot/LANMinecraft.dll"; Algorithm='SHA256'; Hash='86690AEE6344917788EA4B733D7589FC14CF69226991B99B597C0AFE23E6D593'}
)) {
    if ((Get-FileHash -LiteralPath $inputFile.Path -Algorithm $inputFile.Algorithm).Hash -ne $inputFile.Hash) {
        throw "Unexpected QA input $($inputFile.Path)"
    }
}
$free = [math]::Floor((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
if ($free -lt 4096) { throw "Available RAM is $free MiB; this smoke test needs 4096 MiB." }

# This fixture exercises the exact shipped quest engine and resource loader in Forge.
$lock = Get-Content -Raw -Encoding utf8 -LiteralPath "$PSScriptRoot/upstream-lock.json" | ConvertFrom-Json
$families = 'architectury', 'ftb-library', 'ftb-quests', 'ftb-teams', 'ftb-xmod-compat', 'item-filters', 'kubejs', 'rhino'
$mods = foreach ($family in $families) {
    $escaped = [Regex]::Escape($family)
    $matchesForFamily = @($lock.files | Where-Object path -Match "^mods/${escaped}-(?:forge-)?[0-9][^/\\]*\.jar$")
    if ($matchesForFamily.Count -ne 1) { throw "Expected one pinned $family JAR." }
    $matchesForFamily[0]
}
$storage = python -B "$project/tools/workspace_storage.py" --check
if ($LASTEXITCODE) { throw "Workspace storage check failed. $storage" }
$storage = $storage | Out-String | ConvertFrom-Json
$maximumBytes = 128MB
if ($storage.budget_bytes - $storage.unique_bytes -lt $maximumBytes) { throw 'The workspace needs 128 MiB for this fixture.' }

$runs = (Resolve-Path "$project/artifacts/agent-runs").Path
if (!$runs.Equals("$project\artifacts\agent-runs", [StringComparison]::OrdinalIgnoreCase) -or
    (@("$project/artifacts", $runs) | Where-Object { (Get-Item -LiteralPath $_).Attributes -band [IO.FileAttributes]::ReparsePoint })) {
    throw 'The QA root must be the project-owned artifacts/agent-runs directory.'
}
$run = Join-Path $runs "escape-quest-qa-$([Guid]::NewGuid().ToString('N').Substring(0, 12))"
$game = "$run/game"
$evidence = "$run/evidence"
$privateHome = "$run/home"
$privateTemp = "$run/tmp"
$classes = "$run/classes"
$now = [DateTime]::UtcNow
$null = New-Item -ItemType Directory -Path $run
$resultPath = "$run/result.json"
$process = $null
$argFile = $null
$started = $false
$stdout = $null
$stderr = $null
$result = [ordered]@{
    status = 'FAIL'
    minecraft = '1.20.1'
    forge = '47.4.10'
    java = '17'
    engine = 'FTB Quests and KubeJS, exact locked mod JARs'
    authorArchiveSha256 = $lock.client.sha256
    supportMods = $mods | Select-Object path, sha256
}
try {
    @{
        owner='Escape The End quest localization QA'
        purpose='Isolated Forge locale switch smoke test with exact FTB/KubeJS dependencies'
        createdUtc=$now.ToString('o')
        deleteAfterUtc=$now.AddDays(1).ToString('o')
        maximumBytes=$maximumBytes
        requiredResults=@('result.json')
    } | ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath "$run/RUN.json"
    foreach ($path in $game, "$game/mods", "$game/config", $evidence, $classes, $privateTemp,
        "$privateHome/AppData/Local", "$privateHome/AppData/Roaming") {
        $null = [IO.Directory]::CreateDirectory($path)
    }
    foreach ($mod in $mods) {
        $item = Get-Item -LiteralPath (Join-Path $installed $mod.path)
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -ne $mod.sizeBytes -or
            (Get-FileHash -LiteralPath $item.FullName).Hash -ne $mod.sha256) {
            throw "Installed source JAR differs from the author lock: $($mod.path)"
        }
        [IO.File]::Copy($item.FullName, (Join-Path $game $mod.path))
    }
    # Extract only original quest files, then apply the same translations as the published overlay.
    @'
from pathlib import Path
import runpy
import sys
script, archive, game = map(Path, sys.argv[1:])
module = runpy.run_path(str(script))
for relative, data in module["quest_sources"](archive).items():
    target = game / "config/ftbquests/quests" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
'@ | python -B - "$PSScriptRoot/localize_quests.py" $author $game
    if ($LASTEXITCODE) { throw 'Could not read locked author quests.' }
    python -B "$PSScriptRoot/localize_quests.py" render $author --game $game
    if ($LASTEXITCODE) { throw 'Could not apply the translated quest overlay.' }
    [IO.File]::Copy("$packSource/config/log4j2.xml", "$game/config/log4j2.xml")
    [IO.File]::WriteAllText("$game/options.txt", "lang:en_us`nfullscreen:false`n")
    $result.overlaySha256 = (Get-FileHash -LiteralPath "$PSScriptRoot/overlay.json").Hash.ToLowerInvariant()

    & "$Jdk/bin/javac.exe" --release 17 -encoding UTF-8 -d $classes "$PSScriptRoot/EscapeEndQuestLocaleQa.java"
    if ($LASTEXITCODE) { throw 'The quest locale QA probe did not compile.' }
    @('Manifest-Version: 1.0', 'Premain-Class: lanminecraft.qa.EscapeEndQuestLocaleQa', '') |
        Set-Content -LiteralPath "$run/agent.mf" -Encoding ascii
    $agent = "$run/quest-locale-qa.jar"
    & "$Jdk/bin/jar.exe" cfm $agent "$run/agent.mf" -C $classes .
    if ($LASTEXITCODE) { throw 'The quest locale QA probe could not be packaged.' }

    foreach ($name in 'CmlLib.Core.Commons.dll', 'CmlLib.Core.dll', 'LANMinecraft.dll') {
        $assembly = [Reflection.Assembly]::LoadFrom((Join-Path $assemblyRoot $name))
    }
    $paths = [CmlLib.Core.MinecraftPath]::new($game)
    $paths.Library = "$shared/libraries"
    $paths.Versions = "$shared/versions"
    $paths.Assets = "$shared/assets"
    $paths.Resource = "$shared/resources"
    $paths.Runtime = "$shared/runtime"
    $parameters = [CmlLib.Core.MinecraftLauncherParameters]::CreateDefault($paths)
    $parameters.VersionLoader = [CmlLib.Core.VersionLoader.LocalJsonVersionLoader]::new($paths)
    $launcher = [CmlLib.Core.MinecraftLauncher]::new($parameters)
    $profile = $launcher.GetVersionAsync($profileId, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
    $options = [CmlLib.Core.ProcessBuilder.MLaunchOption]::new()
    $options.Path = $paths
    $options.NativesDirectory = "$privateTemp/natives"
    $options.JavaPath = $java
    $options.Session = [CmlLib.Core.Auth.MSession]::CreateOfflineSession('EscapeQuestQa')
    $options.MinimumRamMb = 512
    $options.MaximumRamMb = 2048
    $options.ScreenWidth = 960
    $options.ScreenHeight = 540
    $options.FullScreen = $false
    $options.VersionType = $profileId
    $extra = [Collections.Generic.List[CmlLib.Core.ProcessBuilder.MArgument]]::new()
    foreach ($line in @(
        "-Duser.home=$privateHome", "-Djava.io.tmpdir=$privateTemp", '-Dfile.encoding=UTF-8',
        '-XX:ActiveProcessorCount=4', '-XX:-HeapDumpOnOutOfMemoryError', "-XX:ErrorFile=$evidence/hs_err_pid%p.log",
        '-Dlog4j.configurationFile=config/log4j2.xml', "-javaagent:$agent",
        "-Dlanminecraft.questqa.run=$run", "-Dlanminecraft.questqa.game=$game", "-Dlanminecraft.questqa.evidence=$evidence"
    )) {
        $extra.Add([CmlLib.Core.ProcessBuilder.MArgument]::new($line))
    }
    $options.ExtraJvmArguments = $extra
    $flags = [Reflection.BindingFlags]'NonPublic, Static'
    $build = $assembly.GetType('Minecraft.MinecraftLaunchArguments', $true).GetMethod('BuildProcess', $flags)
    $process = $build.Invoke($null, [object[]]@($launcher, $profile, $options))
    $start = $process.StartInfo
    $start.WorkingDirectory = $game
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($key in 'SteamAppId', 'SteamGameId', 'SteamOverlayGameId', 'JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS') {
        $null = $start.Environment.Remove($key)
    }
    foreach ($pair in @{ HOME=$privateHome; USERPROFILE=$privateHome; APPDATA="$privateHome/AppData/Roaming";
        LOCALAPPDATA="$privateHome/AppData/Local"; TEMP=$privateTemp; TMP=$privateTemp }.GetEnumerator()) {
        $start.Environment[$pair.Key] = $pair.Value
    }
    $appPaths = [Minecraft.AppPaths]::new($run)
    $launchLogger = [Minecraft.Logger]::new("$evidence/launcher.log")
    $complete = $assembly.GetType('Minecraft.MinecraftProcessService', $true).GetMethod('CompleteJvmArguments', $flags)
    $arguments = [string[]]$complete.Invoke($null, [object[]]@($start, $profile, $game, [Minecraft.PackJvmArgumentsService]::new(), $null))
    $main = [Array]::IndexOf($arguments, [string]$profile.MainClass)
    $prepare = $assembly.GetType('Minecraft.JavaLaunchArgumentFile', $true).GetMethod('Prepare', $flags)
    $argFile = $prepare.Invoke($null, [object[]]@($appPaths, $start, $arguments, 17, $main, $launchLogger))

    $stdout = [IO.File]::Open("$evidence/stdout.log", 'CreateNew', 'Write', 'Read')
    $stderr = [IO.File]::Open("$evidence/stderr.log", 'CreateNew', 'Write', 'Read')
    if (!$process.Start()) { throw 'Could not start the isolated client.' }
    $started = $true
    $process.PriorityClass = [Diagnostics.ProcessPriorityClass]::BelowNormal
    "Quest locale QA process $($process.Id), evidence in $run"
    $copyOut = $process.StandardOutput.BaseStream.CopyToAsync($stdout)
    $copyErr = $process.StandardError.BaseStream.CopyToAsync($stderr)
    $deadline = [DateTime]::UtcNow.AddMinutes(12)
    while (!$process.WaitForExit(1000)) {
        $size = (Get-ChildItem -LiteralPath $run -Recurse -File | Measure-Object -Property Length -Sum).Sum
        if ([DateTime]::UtcNow -ge $deadline -or $stdout.Length + $stderr.Length -gt 16MB -or $size -gt $maximumBytes) {
            throw 'The quest locale probe reached its time or storage limit.'
        }
    }
    [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($copyOut, $copyErr))
    $result.exitCode = $process.ExitCode
    $result.phases = foreach ($phase in 'initial', 'switched', 'restored') {
        $path = "$evidence/$phase.tsv"
        if (Test-Path -LiteralPath $path) {
            $values = (Get-Content -Encoding utf8 -LiteralPath $path) -split "`t"
            [ordered]@{ phase=$phase; language=$values[0]; group=$values[1]; chapters=@($values[2], $values[3]);
                title=$values[4]; description=$values[5]; coloredIntro=$values[6] }
        }
    }
    if ($process.ExitCode -or @($result.phases).Count -ne 3) { throw 'The native locale check did not complete.' }
    $result.status = 'PASS'
} catch {
    $result.error = $_.Exception.Message
    if (Test-Path -LiteralPath "$evidence/failure.txt") {
        $result.assertion = Get-Content -Raw -Encoding utf8 -LiteralPath "$evidence/failure.txt"
    }
    $result.consoleTail = foreach ($path in "$evidence/stdout.log", "$evidence/stderr.log") {
        if (Test-Path -LiteralPath $path) { Get-Content -Encoding utf8 -LiteralPath $path -Tail 24 }
    }
    throw
} finally {
    if ($started -and !$process.HasExited) { $process.Kill($true); $process.WaitForExit() }
    if ($stdout) { $stdout.Dispose() }
    if ($stderr) { $stderr.Dispose() }
    if ($argFile) { $argFile.Dispose() }
    if ($process) { $process.Dispose() }
    $result | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 -LiteralPath $resultPath
    $root = (Resolve-Path -LiteralPath $run).Path
    if ([IO.Path]::GetDirectoryName($root) -ine $runs -or
        ((Get-Item -LiteralPath $root).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'The QA data no longer resolves inside the owned workspace run.'
    }
    foreach ($entry in Get-ChildItem -LiteralPath $root -Force) {
        if ($entry.Name -in 'RUN.json', 'result.json') { continue }
        # PowerShell does not descend into reparse points in Get-ChildItem, and explicit removals avoid traversal.
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Remove-Item -LiteralPath $entry.FullName -Force
        } elseif ($entry.PSIsContainer) {
            $links = @(Get-ChildItem -LiteralPath $entry.FullName -Recurse -Attributes ReparsePoint -Force)
            foreach ($link in $links) { Remove-Item -LiteralPath $link.FullName -Force }
            Remove-Item -LiteralPath $entry.FullName -Recurse -Force
        } else {
            Remove-Item -LiteralPath $entry.FullName -Force
        }
    }
    "Quest locale QA receipt: $resultPath"
}
