# Escape The End 0.1 native Forge QA

The pinned input is CurseForge file 9001599. The client is Minecraft 1.20.1 with Forge 47.4.10. The exact verified Forge installer SHA256 is `1912760b4cb6b803d8a826de603c9076b1da71ec2765e9a1f8c1ca78f65278e3`. The generated Forge client JAR matches official SHA1 `4d8a9a63dc16a45d7fc5c54c627234f601d0cc17`. The Windows runner uses the launcher 500 assembly SHA256 `86690aee6344917788ea4b733d7589fc14cf69226991b99b597c0afe23e6d593`.

| Stage | Result | Exact inventory SHA256 |
| --- | --- | --- |
| Author input | Reached the actual title in 54.405 seconds. Forge `ModList` reported 150 IDs. | `fd799ae260a66d0c1517e5e9c69147d536d819d4f017c9db12c054ede728ff85` |
| Steam candidate | The stock Forge client `completeScan` finished with no preload errors or broken files. `LoadingModList` reported 149 IDs. | `b8c63d0bf43709ee253a8336cd97ce63f7906b8f64360763ee95599aa44c11e9` |
| Library | 146 pack IDs from 136 verified shipped JARs. Minecraft, Forge and the launcher-managed Steam component are excluded from the pack list. | `79e93aabc9ba05b229ab264a90f758a49f006265866b9849e8ef78618b8fd65f` |

The two observed native registries differ only by removal of `e4mc` and `lanserverproperties`, and addition of `e4steam`. All retained native IDs, names, versions and filenames match. The candidate stage lock SHA256 is `0707bf45a15f7398785a6c6f013c70ded5a21afa4991f778ff9b71a4265a9ff4`. The managed transport SHA256 is `3f397fc193f14d3c543b5ef9b4613bc149a21ca8e14bd93c39de3d1bc6f9553c`.

The discovery pass used a separate instance, Forge's native client scanner, and 1024 MiB. It ended before rendering or joining a world. The candidate title and Steam host/guest interaction remain open in this receipt.

## Reproduce

Run from an LANMinecraft checkout with the pinned public launcher assembly and the managed transport in its ordinary cache. Verify the author download and stage the locked pack with the published LANMinecraft `release_overlay.py`. Use a marked run under `artifacts/agent-runs` and place the staged files and intake lock in its `stage` directory. Use the cached Java 17 runtime pinned in the script and a JDK 17 or later for the QA agent. Download the official `forge-1.20.1-47.4.10-installer.jar` from the Forge Maven repository to `native-loader/forge-installer.jar` in that run. The preparer reuses exact immutable artifacts from `Minecraft/Launcher/Shared`.

```powershell
python -B LocalSources/Escape-The-End/tools/prepare_native_forge.py "$run/native-loader" `
  Minecraft/Launcher/Shared Minecraft/Launcher/JavaRuntimes/runtime/windows-x64/java-17/bin/java.exe
pwsh -NoProfile -File LocalSources/Escape-The-End/tools/Run-NativeModList.ps1 -RunLabel $label -SteamCandidate -DiscoveryOnly
python -B LocalSources/Escape-The-End/tools/build_loaded_inventory.py "$run/stage" `
  "$run/runtime-qa/evidence/SteamCandidateDiscovery/forge-discovery.tsv" `
  LocalSources/Escape-The-End/library/loaded-mods.json
```

After freeing the discovery instance, pass `-SteamCandidate -NativeHost` with the same runner for a native title and host check. This uses the launcher's verified identity adapter and the shared `Forge118SteamQaAgent` to create a disposable world, open it through the ordinary native action and verify publication. The host check requires a signed-in Steam client and sufficient local memory. It does not exercise an incoming remote guest. Retain the small receipts and remove the disposable instance, native runtime view and raw diagnostic files.

## Questbook locale

```powershell
pwsh -NoProfile -File LocalSources/Escape-The-End/tools/Run-QuestLocaleQa.ps1 -AuthorArchive $authorZip
```

PASS on Forge 47.4.10 / Java 17 with the official questbook, overlay SHA256
`c6f948af9289638bf9bf3dcad4cc4764cc06e3df2c0d3d4c38b5a9db0e33966c` and the eight pinned
FTB/KubeJS dependencies. The probe opened the FTB quest screen in a disposable world
and switched `en_us → ru_ru → en_us` in the same session. It checked the book,
chapter group, chapter, quest and task titles, subtitle, description, colored intro,
an unchanged song title, and a supplemental Russian item name. The isolated
language test uses the exact quest and resource loader versions. Full pack
installation is verified separately in the release workflow.
