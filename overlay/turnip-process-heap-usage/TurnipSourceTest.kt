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
        val file = if (source == "DroidDeck Patch") "DroidDeck-Patch-$tag.zip" else "$tag.zip"
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
    @Test fun bundledMetadataUsesNormalPairsAndContentSpecificCacheKey() {
        val json = JSONObject().put("tag", "DD-Turnip-v0.2.0").put("sha256", "a".repeat(64)).put("size", 100)
        val patched = BundledTurnip.asset(json)
        val official = bundle("DroidDeck", "0.2.0")
        assertTrue(patched.url.startsWith("apk://"))
        assertSame(patched, pairs(patched, official).single().bundle)
        assertNotEquals(official.name, patched.name)
        assertNotEquals(patched.name, BundledTurnip.asset(json.put("sha256", "b".repeat(64))).name)
    }
    @Test(expected = IllegalArgumentException::class)
    fun invalidBundledTagIsRejected() {
        BundledTurnip.asset(JSONObject().put("tag", "../escape").put("sha256", "a".repeat(64)).put("size", 1))
    }

    @Test fun apkBundledDriverDownloadsOfflineAndUsesExistingBundleImporter() {
        val context = RuntimeEnvironment.getApplication()
        val asset = BundledTurnip.merge(context, null)?.assets?.single()
        org.junit.Assume.assumeNotNull(asset)
        val bundled = asset!!
        var completed = false
        val zip = TurnipReleases.download(context, bundled) { completed = it == 100 }
        try {
            assertTrue(completed)
            assertEquals(bundled.sha256, com.droiddeck.launcher.core.Hashes.sha256(zip))
            val installed = DriverBundle.install(context, android.net.Uri.fromFile(zip))
            try {
                assertEquals(bundled.tag.removePrefix("DD-Turnip-v"), installed.version)
                assertTrue(TurnipDriver(context).isInstalled(installed.androidId))
                assertTrue(LinuxVulkanDriverManager(context).isInstalled(installed.linuxId))
                TurnipReleases.recordDownload(context, bundled, installed.id)
                assertEquals(installed.id, TurnipReleases.installedId(context, bundled) { DriverBundle.get(context, it) != null })
            } finally { DriverBundle.remove(context, installed) }
        } finally { zip.delete() }
    }

}
