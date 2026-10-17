package com.droiddeck.launcher.gpu

import android.content.Context
import com.droiddeck.launcher.core.Hashes
import org.json.JSONObject
import java.io.File
import java.io.IOException

/** Owned by the temporary heap-usage overlay. No installed-data migration is needed on removal. */
object BundledTurnip {
    private const val ROOT = "turnip-process-heap-usage"

    internal fun asset(json: JSONObject): TurnipReleases.Asset {
        val tag = json.getString("tag")
        require(Regex("DD-Turnip-v\\d+\\.\\d+\\.\\d+").matches(tag))
        val sha = json.getString("sha256")
        require(Regex("[0-9a-f]{64}").matches(sha))
        return TurnipReleases.Asset("DroidDeck Patch · APK", tag, "DroidDeck-Patch-$sha.zip",
            "apk://$ROOT/bundle.zip", json.getLong("size"), false, "Android + Linux · all Adreno",
            sha, DriverPairs.ddTurnip(tag), bundle = true)
    }

    fun merge(context: Context, cached: TurnipReleases.Check?): TurnipReleases.Check? {
        // Missing assets mean this APK did not include a patched build (upstream already fixed it).
        val metadata = try { context.assets.open("$ROOT/bundle.json") } catch (_: java.io.FileNotFoundException) { return cached }
        val bundled = metadata.bufferedReader().use { asset(JSONObject(it.readText())) }
        return TurnipReleases.Check(listOf(bundled) + cached?.assets.orEmpty(),
            cached?.latest.orEmpty(), cached?.failed.orEmpty(), cached?.checkedAt ?: 0)
    }

    fun download(context: Context, asset: TurnipReleases.Asset, progress: (Int) -> Unit): File? {
        if (asset.url != "apk://$ROOT/bundle.zip") return null
        val expected = merge(context, null)?.assets?.single() ?: throw IOException("Bundled Turnip unavailable")
        require(asset.sha256 == expected.sha256 && asset.name == expected.name)
        val target = File(context.cacheDir, asset.name)
        try {
            context.assets.open("$ROOT/bundle.zip").use { input -> target.outputStream().use { input.copyTo(it) } }
            if (target.length() != asset.size || Hashes.sha256(target) != asset.sha256) throw IOException("Bundled Turnip checksum mismatch")
            progress(100)
            return target
        } catch (error: Exception) {
            target.delete()
            throw error
        }
    }
}
