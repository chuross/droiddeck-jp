package com.droiddeck.launcher.gpu

import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])

class TurnipSourceTest {
    private fun bundle(source: String, version: String): TurnipReleases.Asset {
        val tag = "DD-Turnip-v$version"
        val registry = TurnipReleases::class.java.getDeclaredField("SOURCES").apply { isAccessible = true }
        val sources = registry.get(TurnipReleases) as List<*>
        fun field(entry: Any, name: String) = entry.javaClass.getDeclaredField(name).apply { isAccessible = true }.get(entry) as String
        val registered = sources.filterNotNull().single { field(it, "label") == source }
        val file = field(registered, "cachePrefix") + "$tag.zip"
        return TurnipReleases.Asset(source, tag, file, "https://example/$source/$tag", 100,
            false, "Android + Linux", "0".repeat(64), DriverPairs.ddTurnip(tag), bundle = true)
    }
    private fun pairs(vararg assets: TurnipReleases.Asset) = DriverPairs.from(
        TurnipReleases.Check(assets.toList(), emptyList(), emptyList(), 0))

    @Test fun firstSourceWinsSameVersionWithoutSpecialDriverPairLogic() {
        val patched = bundle("DroidDeck Patch", "0.2.0")
        val official = bundle("DroidDeck", "0.2.0")
        assertSame(patched, pairs(patched, official).single().bundle)
        assertSame(official, pairs(official).single().bundle)
    }
    @Test fun newerOfficialIsAvailableAndWinsAutoSelection() {
        val patched = bundle("DroidDeck Patch", "0.2.0")
        val official = bundle("DroidDeck", "0.3.0")
        val choices = pairs(patched, official)
        assertEquals(2, choices.size)
        assertEquals(official.pair, DriverPairs.newestDdTurnip(choices))
        assertSame(official, choices.single { it.key == official.pair }.bundle)
    }

    private fun cache(context: Context, vararg assets: TurnipReleases.Asset) {
        val list = JSONArray()
        assets.forEach { a -> list.put(JSONObject().put("source", a.source).put("tag", a.tag).put("name", a.name)
            .put("url", a.url).put("size", a.size).put("linux", a.linux).put("label", a.label)
            .put("sha256", a.sha256).put("pair", a.pair).put("bundle", a.bundle)) }
        context.getSharedPreferences("turnip_releases", Context.MODE_PRIVATE).edit()
            .putString("latest", JSONObject().put("assets", list).toString()).commit()
    }
    @Test fun existingOfficialFilenameCannotMasqueradeAsPatchedDownload() {
        val context = RuntimeEnvironment.getApplication()
        val prefs = context.getSharedPreferences("turnip_releases", Context.MODE_PRIVATE)
        val official = bundle("DroidDeck", "0.2.0")
        val patched = bundle("DroidDeck Patch", "0.2.0")
        prefs.edit().clear().putString("downloads", JSONObject().put(official.name, "official-id").toString()).commit()
        cache(context, patched)
        assertNull(TurnipReleases.installedId(context, patched) { true })
        cache(context, patched, official)
        assertEquals("DroidDeck-Patch-DD-Turnip-v0.2.0.zip", patched.name)
        assertEquals("DD-Turnip-v0.2.0.zip", official.name)
        assertNull(TurnipReleases.installedId(context, patched) { true })
        TurnipReleases.recordDownload(context, patched, "patched-id")
        assertEquals("patched-id", TurnipReleases.installedId(context, patched) { true })
        assertEquals("official-id", TurnipReleases.installedId(context, official) { true })
        // Removing the extra source leaves the original key usable without a migration.
        cache(context, official)
        assertEquals("official-id", TurnipReleases.installedId(context, official) { true })
        assertTrue(TurnipReleases.isDownloaded(context, "patched-id"))
        TurnipReleases.forget(context, "patched-id")
        assertFalse(TurnipReleases.isDownloaded(context, "patched-id"))
        assertEquals("official-id", TurnipReleases.installedId(context, official) { true })
    }
    @Test fun newerUniqueOfficialKeepsItsExistingDownloadRecord() {
        val context = RuntimeEnvironment.getApplication()
        val official = bundle("DroidDeck", "0.3.0")
        val patched = bundle("DroidDeck Patch", "0.2.0")
        context.getSharedPreferences("turnip_releases", Context.MODE_PRIVATE).edit().clear()
            .putString("downloads", JSONObject().put(official.name, "newer-official").toString()).commit()
        cache(context, patched, official)
        assertEquals("newer-official", TurnipReleases.installedId(context, official) { true })
    }
}
