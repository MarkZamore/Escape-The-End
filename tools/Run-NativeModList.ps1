[CmdletBinding()]
param(
    [switch]$Plan, [switch]$SteamCandidate, [switch]$NativeHost, [switch]$DiscoveryOnly,
    [string]$RunLabel = 'escape-the-end-20261114',
    [string]$Jdk = $env:JAVA_HOME
)
$ErrorActionPreference = 'Stop'
if ($NativeHost -and !$SteamCandidate) { throw 'Native host QA needs the Steam candidate.' }
if ($DiscoveryOnly -and (!$SteamCandidate -or $NativeHost)) { throw 'Discovery mode needs only a Steam candidate.' }
$project = (Resolve-Path "$PSScriptRoot/../../..").Path
if ($RunLabel -notmatch '^[A-Za-z0-9_-]+$') { throw 'Expected a single run directory name.' }
$run = (Resolve-Path "$project/artifacts/agent-runs/$RunLabel").Path
if (![IO.Path]::GetDirectoryName($run).Equals("$project\artifacts\agent-runs",[StringComparison]::OrdinalIgnoreCase) -or
    !(Test-Path -LiteralPath "$run/RUN.json")) { throw 'Use the owned QA run.' }
$game = "$run/game/NativeModList"
$evidence = "$run/evidence/NativeModList"
if ($SteamCandidate) {
    $evidence = "$run/runtime-qa/evidence/NativeModListCandidate"
    $game = "$run/runtime-qa/Minecraft/Personal/Instances/Escape The End"
}
if ($DiscoveryOnly) { $evidence = "$run/runtime-qa/evidence/SteamCandidateDiscovery" }
$heapMiB = if ($DiscoveryOnly) { 1024 } else { 5120 }
$compiler = if ($Jdk) { "$Jdk/bin" } else { 'C:/Tools/jdk-25.0.2/bin' }
$cache = "$project/Minecraft/Launcher"
$java = "$cache/JavaRuntimes/runtime/windows-x64/java-17/bin/java.exe"
$profileId = '1.20.1-forge-47.4.10'
$loader = "$run/native-loader"
$assemblyRoot = "$cache/Runtime/e86ff2d180aeab13cf0d77bf6f0d1b1fceaae5ec8ae8d8969cb68bb9ecf49cab"
$stage = "$run/stage/game"
$stageLock = "$run/stage/intake.json"
$checks = @{
    "$assemblyRoot/LANMinecraft.dll" = '86690AEE6344917788EA4B733D7589FC14CF69226991B99B597C0AFE23E6D593'
    $java = '1977F302375ADBB920D41DAC65C7E22EB9C2ED8E1E8D6258964154FF16F14406'
    $stageLock = '7B92091FD165B282F470F634F43B6F91B0F57BADAA7730BFC498C579C32314E6'
}
if ($SteamCandidate) { $checks[$stageLock] = '0707BF45A15F7398785A6C6F013C70DED5A21AFA4991F778FF9B71A4265A9FF4' }
foreach ($file in $checks.GetEnumerator()) {
    if ((Get-FileHash -LiteralPath $file.Key -Algorithm SHA256).Hash -ne $file.Value) {
        throw "Unexpected input $($file.Key)"
    }
}
$forgeJar = "$loader/libraries/net/minecraftforge/forge/1.20.1-47.4.10/forge-1.20.1-47.4.10-client.jar"
if ((Get-FileHash -LiteralPath $forgeJar -Algorithm SHA1).Hash -ne '4D8A9A63DC16A45D7FC5C54C627234F601D0CC17') {
    throw 'Patched Forge client does not match the official checksum.'
}
foreach ($name in 'CmlLib.Core.Commons.dll','CmlLib.Core.dll','LANMinecraft.dll') {
    $assembly = [Reflection.Assembly]::LoadFrom((Join-Path $assemblyRoot $name))
}
$service = $assembly.GetType('Minecraft.MinecraftLaunchArguments', $true)
$build = $service.GetMethod('BuildProcess', [Reflection.BindingFlags]'NonPublic, Static')
$paths = [CmlLib.Core.MinecraftPath]::new($game)
$paths.Library = "$loader/libraries"
$paths.Versions = "$loader/versions"
$paths.Assets = "$cache/Shared/assets"
$paths.Resource = "$cache/Shared/resources"
$paths.Runtime = "$cache/Shared/runtime"
$parameters = [CmlLib.Core.MinecraftLauncherParameters]::CreateDefault($paths)
$parameters.VersionLoader = [CmlLib.Core.VersionLoader.LocalJsonVersionLoader]::new($paths)
$launcher = [CmlLib.Core.MinecraftLauncher]::new($parameters)
$profile = $launcher.GetVersionAsync($profileId, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
$appPaths = [Minecraft.AppPaths]::new($run)
$descriptor = [Minecraft.PackManifestService]::Load($stage)
if ($SteamCandidate) {
    $transport = [Minecraft.SteamTransportCatalog]::Find($descriptor)
    if ($transport.Sha256 -ne '3f397fc193f14d3c543b5ef9b4613bc149a21ca8e14bd93c39de3d1bc6f9553c') {
        throw 'Public launcher does not select the expected Steam component.'
    }
    $managed = "$cache/ManagedComponents/e4steam/284738310465590484/$($transport.FileName)"
    if ((Get-FileHash -LiteralPath $managed -Algorithm SHA256).Hash -ine $transport.Sha256 -or
        (Test-Path -LiteralPath "$stage/mods/e4mc-forge-6.2.0.jar") -or
        (Test-Path -LiteralPath "$stage/mods/lanserverproperties-1.11.1-forge.jar")) {
        throw 'The Steam candidate does not match the staged component policy.'
    }
}
if ($Plan) {
    @{profile=$profile.Id; base=$profile.ParentVersion.Id; launcher=500; exactForge=$true; heapMiB=$heapMiB;
      steam=$SteamCandidate.IsPresent; nativeHost=$NativeHost.IsPresent; discovery=$DiscoveryOnly.IsPresent;
      evidence=$evidence} | ConvertTo-Json -Compress
    return
}
$free = [math]::Floor((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
$minimumAvailable = if ($DiscoveryOnly) { 4096 } else { 10496 }
if ($free -lt $minimumAvailable) { throw "Available RAM is $free MiB; required $minimumAvailable MiB." }
if (Get-Process java,javaw -ErrorAction SilentlyContinue | Where-Object MainWindowTitle -match 'Desolate[- ]Planet') {
    throw 'Wait for the other Minecraft run to finish.'
}
if ((Test-Path -LiteralPath $game) -or (Test-Path -LiteralPath $evidence)) {
    throw 'Use a fresh disposable instance.'
}
$null = [IO.Directory]::CreateDirectory($evidence)
$privateHome = "$evidence/home"
$privateTemp = "$evidence/tmp"
$classes = "$evidence/classes"
foreach ($path in $privateHome, $privateTemp, "$privateHome/AppData/Local", "$privateHome/AppData/Roaming", $classes, $game) {
    $null = [IO.Directory]::CreateDirectory($path)
}
if ($DiscoveryOnly) {
    $null = [IO.Directory]::CreateDirectory("$game/config")
    if (Test-Path -LiteralPath "$stage/config/fml.toml") {
        [IO.File]::Copy("$stage/config/fml.toml", "$game/config/fml.toml")
    }
} else {
    foreach ($file in Get-ChildItem -LiteralPath $stage -Force) {
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked author stage entry.' }
        if ($file.Name -ne 'mods') { Copy-Item -LiteralPath $file.FullName -Destination $game -Recurse }
    }
}
$modDirectory = "$game/mods"
$null = [IO.Directory]::CreateDirectory($modDirectory)
foreach ($file in Get-ChildItem -LiteralPath "$stage/mods" -File) {
    if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked author JAR.' }
    if ($file.Extension -ne '.jar') {
        Copy-Item -LiteralPath $file.FullName -Destination $modDirectory
    } else {
        $null = New-Item -ItemType HardLink -Path "$modDirectory/$($file.Name)" -Target $file.FullName
    }
}
$logger = [Minecraft.Logger]::new("$evidence/launcher.log")
$adapterArguments = @()
if ($SteamCandidate) {
    [IO.File]::Copy($managed, "$modDirectory/$($transport.FileName)")
    $adapterArguments = @('-Dlink.e4steam.launcherPlayerName=LanMcPackQa','-Dlanminecraft.steamqa.expectSteam=true')
}
if ($SteamCandidate -and !$DiscoveryOnly) {
    $baseJar = "$loader/versions/1.20.1/1.20.1.jar"
    $runtime = [Minecraft.PreparedRuntime]::new($loader, $profileId, $java, $baseJar, $descriptor)
    $adapter = [Minecraft.PortableIdentityAdapterService]::new($appPaths, $logger)
    try {
        $adapterArguments += @($adapter.PrepareJvmArgumentsAsync($runtime, $game, [Threading.CancellationToken]::None).GetAwaiter().GetResult())
    } finally { $adapter.Dispose() }
}
if ($DiscoveryOnly) {
    # Forge runs real client-side discovery, but no display or world can start in this mode.
    $fml = "$game/config/fml.toml"
    $config = if ([IO.File]::Exists($fml)) { [IO.File]::ReadAllText($fml) } else { '' }
    if ($config -match '(?m)^earlyWindowControl\s*=') {
        $config = $config -replace '(?m)^earlyWindowControl\s*=.*$', 'earlyWindowControl=false'
    } else { $config += "`nearlyWindowControl=false`n" }
    [IO.File]::WriteAllText($fml, $config)
    $adapterArguments += @('-Dlanminecraft.steamqa.discoveryOnly=true','-Djava.awt.headless=true',
        "-Dorg.lwjgl.glfw.libname=$privateTemp/unavailable-glfw.dll", '-XX:ActiveProcessorCount=2')
}
if (!$DiscoveryOnly) {
    $optionFile = "$game/options.txt"
    $optionsText = [IO.File]::ReadAllText($optionFile)
    $optionsText = $optionsText -replace '(?m)^fullscreen:true(?=\r?$)','fullscreen:false'
    [IO.File]::WriteAllText($optionFile, $optionsText)
}
$agentSources = @("$PSScriptRoot/EscapeEndModListAgent.java")
if ($NativeHost) {
    $agentSources += "$project/tools/PackData/CurseForge/Forge118SteamQaAgent.java"
    $adapterArguments += @('-Dlanminecraft.steamqa.nativeHost=true','-Dlanminecraft.steamqa.worldSuffix=EscapeEnd')
}
& "$compiler/javac.exe" --release 17 -encoding UTF-8 -d $classes @agentSources
if ($LASTEXITCODE) { throw 'Native registry reader did not compile.' }
$agent = "$evidence/native-mod-list.jar"
@('Manifest-Version: 1.0','Premain-Class: lanminecraft.qa.EscapeEndModListAgent','') |
    Set-Content -LiteralPath "$evidence/agent.mf" -Encoding ascii
& "$compiler/jar.exe" cfm $agent "$evidence/agent.mf" -C $classes .
if ($LASTEXITCODE) { throw 'Native registry reader could not be packaged.' }

$options = [CmlLib.Core.ProcessBuilder.MLaunchOption]::new()
$options.Path = $paths
$options.NativesDirectory = "$privateTemp/natives"
$options.JavaPath = $java
$options.Session = [CmlLib.Core.Auth.MSession]::CreateOfflineSession('LanMcPackQa')
$options.Session.UUID = '70254ec69d1f449b92def1ce35f8bcc4'
$options.MinimumRamMb = [Math]::Min(1024, $heapMiB / 2)
$options.MaximumRamMb = $heapMiB
$options.ScreenWidth = 960
$options.ScreenHeight = 540
$options.FullScreen = $false
$options.GameLauncherName = 'LANMinecraft'
$options.GameLauncherVersion = '500'
$options.VersionType = $profileId
$extra = [Collections.Generic.List[CmlLib.Core.ProcessBuilder.MArgument]]::new()
$extraArgs = @("-Duser.home=$privateHome", "-Djava.io.tmpdir=$privateTemp", '-Dfile.encoding=UTF-8',
    "-XX:ErrorFile=$evidence/hs_err_pid%p.log", "-javaagent:$agent",
    "-Dlanminecraft.steamqa.run=$run", "-Dlanminecraft.steamqa.game=$game",
    "-Dlanminecraft.steamqa.evidence=$evidence", '-Dlanminecraft.steamqa.minecraftVersion=1.20.1')
$extraArgs += [Minecraft.PackJvmArgumentsService]::new().Load($game)
$extraArgs += $adapterArguments
foreach ($line in $extraArgs) { $extra.Add([CmlLib.Core.ProcessBuilder.MArgument]::new($line)) }
$options.ExtraJvmArguments = $extra
$process = $build.Invoke($null, [object[]]@($launcher, $profile, $options))
$start = $process.StartInfo
$start.WorkingDirectory = $game
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
foreach ($key in @('SteamAppId','SteamGameId','SteamOverlayGameId','JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS')) {
    $null = $start.Environment.Remove($key)
}
if ($NativeHost) {
    $appId = [Minecraft.SteamworksApiFacade]::SharedAppId.ToString([Globalization.CultureInfo]::InvariantCulture)
    $start.Environment['SteamAppId'] = $appId
    $start.Environment['SteamGameId'] = $appId
}
foreach ($pair in @{ HOME=$privateHome; USERPROFILE=$privateHome; APPDATA="$privateHome/AppData/Roaming";
    LOCALAPPDATA="$privateHome/AppData/Local"; TEMP=$privateTemp; TMP=$privateTemp }.GetEnumerator()) {
    $start.Environment[$pair.Key] = $pair.Value
}
$flags = [Reflection.BindingFlags]'NonPublic, Static'
$builder = $assembly.GetType('Minecraft.JavaLaunchArgumentFile', $true).GetMethod('Prepare', $flags)
$launcherService = $assembly.GetType('Minecraft.MinecraftProcessService', $true)
$complete = $launcherService.GetMethod('CompleteJvmArguments', $flags)
$arguments = [string[]]$complete.Invoke($null, [object[]]@($start, $profile, $game, [Minecraft.PackJvmArgumentsService]::new(), $null))
$main = [Array]::IndexOf($arguments, [string]$profile.MainClass)
$argFile = $builder.Invoke($null, [object[]]@($appPaths, $start, $arguments, 17, $main, $logger))
$stdout = [IO.File]::OpenWrite("$evidence/stdout.log")
$stderr = [IO.File]::OpenWrite("$evidence/stderr.log")
$started = $false
try {
    if (!$process.Start()) { throw 'Could not start the isolated client' }
    $started = $true
    $process.PriorityClass = [Diagnostics.ProcessPriorityClass]::BelowNormal
    $process.Id | Set-Content -LiteralPath "$evidence/owned.pid"
    @{ pid=$process.Id; heapMiB=$heapMiB; freeMiBAtStart=$free; priority='BelowNormal'; profile=$profile.Id; discovery=$DiscoveryOnly.IsPresent } |
        ConvertTo-Json | Set-Content -LiteralPath "$evidence/process.json"
    $copyOutput = $process.StandardOutput.BaseStream.CopyToAsync($stdout)
    $copyError = $process.StandardError.BaseStream.CopyToAsync($stderr)
    $deadline = [DateTime]::UtcNow.AddMinutes($(if ($DiscoveryOnly) { 2 } else { 11 }))
    while (!$process.WaitForExit(1000)) {
        if ($stdout.Length + $stderr.Length -gt 10MB -or [DateTime]::UtcNow -ge $deadline) {
            throw 'Native probe reached its diagnostic limit.'
        }
    }
    [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($copyOutput, $copyError))
    $receiptFile = if ($DiscoveryOnly) { 'forge-discovery.tsv' } else { 'native-mods.tsv' }
    if ($process.ExitCode -or !(Test-Path -LiteralPath "$evidence/$receiptFile")) {
        throw "Native registry receipt missing after exit $($process.ExitCode)."
    }
    "Forge mod inventory: $evidence/$receiptFile"
    if ($NativeHost -and !(Select-String -LiteralPath "$evidence/native-steam-qa.jsonl" -SimpleMatch '"nativeSteamHost":"PASS"')) {
        throw "See failed native host receipt $evidence/native-steam-qa.jsonl. ModList is still valid."
    }
} finally {
    if ($started -and !$process.HasExited) { $process.Kill($true); $process.WaitForExit() }
    if ($argFile) { $argFile.Dispose() }
    $stdout.Dispose()
    $stderr.Dispose()
    $process.Dispose()
}
