package lanminecraft.qa;

import java.lang.instrument.Instrumentation;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;

/** Verifies the actual FTB quest book in a disposable Forge 1.20.1 client and world. */
public final class EscapeEndQuestLocaleQa {
    private static final String MC = "net.minecraft.client.Minecraft";
    private static final String QUESTS = "dev.ftb.mods.ftbquests.";
    private static final String WORLD = "EscapeEndQuestLocaleQa";
    private final Object client;
    private final ClassLoader loader;
    private final Path evidence;

    private EscapeEndQuestLocaleQa(Object client, Path evidence) {
        this.client = client;
        this.loader = client.getClass().getClassLoader();
        this.evidence = evidence;
    }

    public static void premain(String args, Instrumentation instrumentation) {
        Thread worker = new Thread(() -> run(instrumentation), "Escape The End quest locale QA");
        worker.setDaemon(true);
        worker.start();
    }

    private static void run(Instrumentation instrumentation) {
        Path root = Path.of(System.getProperty("lanminecraft.questqa.run"));
        Path game = Path.of(System.getProperty("lanminecraft.questqa.game"));
        Path evidence = Path.of(System.getProperty("lanminecraft.questqa.evidence"));
        try {
            Path canonical = root.toRealPath();
            if (!Files.isRegularFile(root.resolve("RUN.json"))
                    || !game.toRealPath().equals(canonical.resolve("game"))
                    || !evidence.toRealPath().equals(canonical.resolve("evidence"))
                    || Files.exists(game.resolve("saves"))) {
                throw new IllegalStateException("Expected an empty marked QA instance");
            }
            // Wait for Forge to initialize the client before resolving transformed game classes.
            Path log = game.resolve("logs/latest.log");
            waitFor("Forge resource loading", () -> Files.isRegularFile(log)
                    && Files.readString(log).contains("shulker_boxes.png-atlas"));
            Class<?> minecraft = null;
            for (Class<?> loaded : instrumentation.getAllLoadedClasses()) {
                if (loaded.getName().equals(MC)) {
                    minecraft = loaded;
                    break;
                }
            }
            if (minecraft == null) throw new IllegalStateException("Minecraft client was not initialized");
            Object client = minecraft.getMethod("m_91087_").invoke(null);
            new EscapeEndQuestLocaleQa(client, evidence).check();
        } catch (Throwable failure) {
            try {
                Files.writeString(evidence.resolve("failure.txt"), failure.toString());
            } catch (Exception ignored) {
                // The console retains the failure if the receipt cannot be written.
            }
            failure.printStackTrace();
            System.exit(1);
        }
    }

    private void check() throws Exception {
        waitFor("main menu", () -> onClient(() -> {
            var screen = client.getClass().getDeclaredField("f_91080_");
            screen.setAccessible(true);
            return type("net.minecraft.client.gui.screens.TitleScreen").isInstance(screen.get(client))
                    && invoke(client, "m_91265_") == null;
        }));
        onClient(() -> {
            createWorld();
            return null;
        });
        Class<?> questFile = type(QUESTS + "client.ClientQuestFile");
        waitFor("FTB Quests sync", () -> onClient(() -> (boolean) questFile.getMethod("exists").invoke(null)));
        waitFor("FTB Quests team data", () -> onClient(() -> {
            var team = questFile.getDeclaredField("selfTeamData");
            team.setAccessible(true);
            return !(boolean) invoke(team.get(questFile.getField("INSTANCE").get(null)), "isLocked");
        }));

        for (String phase : List.of("initial", "switched", "restored")) {
            String locale = phase.equals("switched") ? "ru_ru" : "en_us";
            if (!phase.equals("initial")) changeLanguage(locale);
            onClient(() -> {
                inspect(questFile, locale, phase);
                return null;
            });
        }
        onClient(() -> invoke(client, "m_91395_"));
    }

    private void changeLanguage(String locale) throws Exception {
        CompletableFuture<?> reload = onClient(() -> {
            Object languageManager = invoke(client, "m_91102_");
            languageManager.getClass().getMethod("m_264110_", String.class).invoke(languageManager, locale);
            return (CompletableFuture<?>) invoke(client, "m_91391_");
        });
        reload.get(240, TimeUnit.SECONDS);
    }

    private void inspect(Class<?> questFile, String locale, String phase) throws Exception {
        boolean russian = locale.equals("ru_ru");
        expect(locale, (String) invoke(invoke(client, "m_91102_"), "m_264236_"));
        Object file = questFile.getField("INSTANCE").get(null);
        Object artifacts = lookup(file, "getChapter", 0x5BDFB39C85B0FA2DL);
        Object discs = lookup(file, "getChapter", 0x0A98BB2FB6170055L);
        Object group = lookup(file, "getChapterGroup", 0x5019730B6BD4400BL);
        Object quest = lookup(file, "getQuest", 0x62452460AD6E84EBL);
        Object music = lookup(file, "getQuest", 0x02475AD57BC78987L);
        Object info = lookup(file, "getQuest", 0x2EF6D283059A0AE7L);
        Object intro = ((List<?>) invoke(info, "getDescription")).get(0);
        Object task = ((List<?>) invoke(quest, "getTasksAsList")).get(0);
        List<?> lines = (List<?>) invoke(quest, "getDescription");
        String description = russian ? "Соберите все 44 артефакта" : "Collect all 44 artifacts";
        String title = russian ? "Коллекция артефактов" : "Artifact Collection";
        String groupTitle = text(invoke(group, "getTitle"));
        String chapterTitle = text(invoke(artifacts, "getTitle"));
        String discTitle = text(invoke(discs, "getTitle"));
        String descriptionLine = text(lines.get(0));
        expect(russian ? "Побег из Края" : "Escape the End", text(invoke(file, "getTitle")));
        expect(russian ? "Коллекции" : "Collections", groupTitle);
        expect(russian ? "Артефакты" : "Artifacts", chapterTitle);
        expect(russian ? "Пластинки" : "Music Discs", discTitle);
        expect(title, text(invoke(quest, "getTitle")));
        expect(title, text(invoke(task, "getTitle")));
        expect(description, text(invoke(quest, "getSubtitle")));
        expect(description, descriptionLine);
        expect("Grasping at Stars", text(invoke(music, "getTitle")));
        String leadingText = russian ? "Вы начинаете в Крае" : "You begin in the End";
        if (!text(intro).startsWith(leadingText) || !hasTextColor(intro)) {
            throw new IllegalStateException("Intro text or its color differs: " + text(intro));
        }
        if (russian) {
            Class<?> i18n = type("net.minecraft.client.resources.language.I18n");
            String candy = (String) i18n.getMethod("m_118938_", String.class, Object[].class)
                    .invoke(null, "item.phantasm.pop_rock_candy", new Object[0]);
            expect("Взрывная карамель", candy);
        }

        Object screen = questFile.getMethod("openGui", type(QUESTS + "quest.Quest"), boolean.class)
                .invoke(null, quest, true);
        if (screen == null) throw new IllegalStateException("The FTB quest screen did not open");
        Files.writeString(evidence.resolve(phase + ".tsv"),
                String.join("\t", locale, groupTitle, chapterTitle, discTitle, title, descriptionLine, text(intro)) + "\n");
    }

    private boolean hasTextColor(Object component) throws ReflectiveOperationException {
        Class<?> type = type("net.minecraft.network.chat.Component");
        if (invoke(type.getMethod("m_7383_").invoke(component), "m_131135_") != null) return true;
        for (Object child : (List<?>) type.getMethod("m_7360_").invoke(component)) {
            if (hasTextColor(child)) return true;
        }
        return false;
    }

    private static Object lookup(Object file, String method, long id) throws ReflectiveOperationException {
        Object result = file.getClass().getMethod(method, long.class).invoke(file, id);
        if (result == null) throw new IllegalStateException("Missing " + method + " " + Long.toHexString(id));
        return result;
    }

    private void createWorld() throws Exception {
        Class<?> settings = type("net.minecraft.world.level.LevelSettings");
        Class<?> mode = type("net.minecraft.world.level.GameType");
        Class<?> difficulty = type("net.minecraft.world.Difficulty");
        Class<?> rules = type("net.minecraft.world.level.GameRules");
        Class<?> data = type("net.minecraft.world.level.WorldDataConfiguration");
        Class<?> options = type("net.minecraft.world.level.levelgen.WorldOptions");
        Class<?> registry = type("net.minecraft.core.RegistryAccess");
        Object levelSettings = settings.getDeclaredConstructor(String.class, mode, boolean.class,
                difficulty, boolean.class, rules, data).newInstance(WORLD, mode.getField("CREATIVE").get(null),
                false, difficulty.getField("NORMAL").get(null), false, rules.getConstructor().newInstance(),
                data.getField("f_244649_").get(null));
        Object worldOptions = options.getMethod("m_247394_").invoke(null);
        var normal = type("net.minecraft.world.level.levelgen.presets.WorldPresets")
                .getDeclaredMethod("m_246552_", registry);
        Function<Object, Object> dimensions = access -> {
            try {
                return normal.invoke(null, access);
            } catch (ReflectiveOperationException failure) {
                throw new IllegalStateException(failure);
            }
        };
        Object flows = invoke(client, "m_231466_");
        flows.getClass().getMethod("m_233157_", String.class, settings, options, Function.class)
                .invoke(flows, WORLD, levelSettings, worldOptions, dimensions);
    }

    private static void waitFor(String phase, Callable<Boolean> check) throws Exception {
        long limit = System.nanoTime() + TimeUnit.MINUTES.toNanos(7);
        while (!check.call()) {
            if (System.nanoTime() > limit) throw new IllegalStateException("Timed out waiting for " + phase);
            Thread.sleep(500);
        }
    }

    private <T> T onClient(Callable<T> work) throws Exception {
        var request = new FutureTask<>(work);
        ((Executor) client).execute(request);
        return request.get(180, TimeUnit.SECONDS);
    }

    private Class<?> type(String name) throws ClassNotFoundException {
        return Class.forName(name, false, loader);
    }

    private static Object invoke(Object target, String method) throws ReflectiveOperationException {
        return target.getClass().getMethod(method).invoke(target);
    }

    private String text(Object component) throws ReflectiveOperationException {
        return (String) type("net.minecraft.network.chat.Component").getMethod("getString").invoke(component);
    }

    private static void expect(String expected, String actual) {
        if (!expected.equals(actual)) throw new IllegalStateException("Expected " + expected + ", got " + actual);
    }
}
