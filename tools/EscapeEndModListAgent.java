package lanminecraft.qa;

import java.lang.instrument.Instrumentation;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.HexFormat;
import java.util.List;
import java.util.TreeMap;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

/** Opt-in, read-only observation of Forge's actual loaded registry at the native title. */
public final class EscapeEndModListAgent {
    private static final String MINECRAFT = "net.minecraft.client.Minecraft";
    private static final String TITLE = "net.minecraft.client.gui.screens.TitleScreen";
    private static final String PREFIX = "lanminecraft.steamqa.";
    private static final HexFormat HEX = HexFormat.of();

    public static void premain(String ignored, Instrumentation instrumentation) {
        Thread thread = new Thread(() -> run(instrumentation), "Escape The End native ModList QA");
        thread.setDaemon(true);
        thread.start();
    }

    private static void run(Instrumentation instrumentation) {
        Object client = null;
        boolean hostStarted = false;
        try {
            Path run = Path.of(System.getProperty(PREFIX + "run")).toRealPath();
            Path game = Path.of(System.getProperty(PREFIX + "game")).toRealPath();
            Path evidence = Path.of(System.getProperty(PREFIX + "evidence")).toRealPath();
            if (!Files.isRegularFile(run.resolve("RUN.json")) ||
                    !(game.startsWith(run.resolve("game")) || game.startsWith(run.resolve("runtime-qa/Minecraft/Personal/Instances"))) ||
                    !(evidence.startsWith(run.resolve("evidence")) || evidence.startsWith(run.resolve("runtime-qa/evidence"))) ||
                    !System.getProperty(PREFIX + "minecraftVersion").equals("1.20.1")) {
                throw new IllegalStateException("Expected a marked disposable instance");
            }
            if (Boolean.getBoolean(PREFIX + "discoveryOnly")) {
                collectDiscovery(instrumentation, game, evidence);
                return;
            }
            long until = System.nanoTime() + Duration.ofMinutes(10).toNanos();
            while (System.nanoTime() < until && client == null) {
                // Class presence during FML discovery does not establish that the actual client initialized.
                boolean running = Thread.getAllStackTraces().values().stream().flatMap(java.util.Arrays::stream)
                        .anyMatch(frame -> frame.getClassName().equals(MINECRAFT) &&
                                frame.getMethodName().equals("m_91374_"));
                if (running) {
                    for (Class<?> type : instrumentation.getAllLoadedClasses()) {
                        if (type.getName().equals(MINECRAFT)) client = type.getMethod("m_91087_").invoke(null);
                    }
                }
                if (client == null) Thread.sleep(300);
            }
            if (client == null) throw new IllegalStateException("Native Minecraft did not initialize");
            Object gameClient = client;
            ClassLoader loader = client.getClass().getClassLoader();
            while (System.nanoTime() < until) {
                CompletableFuture<Boolean> attempt = new CompletableFuture<>();
                ((Executor) gameClient).execute(() -> {
                    try {
                        attempt.complete(collect(gameClient, loader, game, evidence));
                    } catch (Throwable failure) {
                        attempt.completeExceptionally(failure);
                    }
                });
                if (attempt.get(45, TimeUnit.SECONDS)) {
                    if (Boolean.getBoolean(PREFIX + "nativeHost")) {
                        Class.forName("lanminecraft.qa.Forge118SteamQaAgent")
                                .getMethod("premain", String.class, Instrumentation.class)
                                .invoke(null, null, instrumentation);
                        hostStarted = true;
                    }
                    return;
                }
                Thread.sleep(300);
            }
            throw new IllegalStateException("Native Minecraft title did not finish loading");
        } catch (Exception failure) {
            failure.printStackTrace(System.err);
        } finally {
            if (client != null && !hostStarted) {
                Object gameClient = client;
                ((Executor) gameClient).execute(() -> {
                    try {
                        call(gameClient, "m_91395_");
                    } catch (ReflectiveOperationException failure) {
                        failure.printStackTrace(System.err);
                    }
                });
            }
        }
    }

    private static boolean collect(Object client, ClassLoader loader, Path game, Path evidence) throws Exception {
        Object screen = client.getClass().getField("f_91080_").get(client);
        if (screen == null || !Class.forName(TITLE, false, loader).isInstance(screen) ||
                call(client, "m_91265_") != null) return false;
        if (!((java.io.File) client.getClass().getField("f_91069_").get(client)).toPath().toRealPath().equals(game)) {
            throw new IllegalStateException("Native client came from the wrong work directory");
        }

        Object modList = Class.forName("net.minecraftforge.fml.ModList", false, loader).getMethod("get").invoke(null);
        writeReceipt(modList, evidence.resolve("native-mods.tsv"));
        return true;
    }

    private static void collectDiscovery(Instrumentation instrumentation, Path game, Path evidence) throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(95).toNanos();
        while (System.nanoTime() < deadline) {
            for (Class<?> type : instrumentation.getAllLoadedClasses()) {
                if (!type.getName().equals("net.minecraftforge.fml.loading.FMLLoader")) continue;
                Object list = type.getMethod("getLoadingModList").invoke(null);
                if (list == null) continue;
                Path path = (Path) type.getMethod("getGamePath").invoke(null);
                if (!path.toRealPath().equals(game) || !String.valueOf(type.getMethod("getDist").invoke(null)).equals("CLIENT") ||
                        !((List<?>) call(list, "getErrors")).isEmpty() || !((List<?>) call(list, "getBrokenFiles")).isEmpty()) {
                    throw new IllegalStateException("Forge client discovery did not complete cleanly for the isolated game");
                }
                writeReceipt(list, evidence.resolve("forge-discovery.tsv"));
                System.exit(0);
            }
            Thread.sleep(40);
        }
        throw new IllegalStateException("Forge did not finish client-side discovery during the diagnostic window");
    }

    private static void writeReceipt(Object modList, Path destination) throws Exception {
        TreeMap<String, String> mods = new TreeMap<>();
        for (Object info : (Iterable<?>) call(modList, "getMods")) {
            String id = (String) call(info, "getModId");
            String version = String.valueOf(call(info, "getVersion"));
            String name = (String) call(info, "getDisplayName");
            Object owning = call(info, "getOwningFile");
            Path path = (Path) call(call(owning, "getFile"), "getFilePath");
            String ownerUrl = sanitize(path.toUri().toString());
            String nativeFileName = path.getFileName() == null ? "" : path.getFileName().toString();
            String row = List.of(id, name, version, nativeFileName, ownerUrl).stream()
                    .map(value -> HEX.formatHex(value.getBytes(java.nio.charset.StandardCharsets.UTF_8)))
                    .collect(Collectors.joining("\t"));
            if (mods.put(id, row) != null) throw new IllegalStateException("Duplicate Forge-loaded ID " + id);
        }
        if (!mods.keySet().containsAll(List.of("minecraft", "forge", "default_spawn_dimension", "endmod",
                "heartofender", "overend", "endstonetools", "ftbquests"))) {
            throw new IllegalStateException("The actual Forge registry lacks one of the pinned core mods");
        }
        if (Boolean.getBoolean(PREFIX + "expectSteam") &&
                (!mods.containsKey("e4steam") || mods.containsKey("e4mc") || mods.containsKey("lanserverproperties"))) {
            throw new IllegalStateException("The actual Forge registry differs from the pinned Steam transport policy");
        }
        Files.write(destination, mods.values());
        System.out.println("[EscapeTheEndQA] Reviewed Forge mod IDs " + mods.size());
    }

    private static String sanitize(String uri) {
        // SecureJarHandler may embed the isolated parent JAR URI in a nested archive path.
        // Strip any filesystem prefix before the game's mods directory and leave JarJar suffixes.
        String original = uri.replace('\\', '/');
        int origin = original.toLowerCase(java.util.Locale.ROOT).lastIndexOf("/mods/");
        return origin < 0 ? "" : original.substring(origin + 1);
    }

    private static Object call(Object owner, String name) throws ReflectiveOperationException {
        Method method = owner.getClass().getMethod(name);
        method.trySetAccessible();
        return method.invoke(owner);
    }
}
