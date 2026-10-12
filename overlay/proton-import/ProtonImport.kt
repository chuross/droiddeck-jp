package com.droiddeck.launcher.session

import android.content.Context
import android.net.Uri
import android.os.StatFs
import android.provider.OpenableColumns
import android.util.Log
import com.droiddeck.launcher.R
import com.droiddeck.launcher.runtime.LinuxRuntime
import java.io.File
import org.json.JSONObject

/** Local imports share ProtonExtras' lock so installs cannot edit the same tools concurrently. */
object ProtonImport {
    const val BUSY_ID = "local-import"
    private const val PREFIX = "DDJP-"
    private const val MARKER = ".droiddeck-patch-import.json"
    private const val MAX_ARCHIVE = 4L * 1024 * 1024 * 1024
    private const val RESERVE = 256L * 1024 * 1024
    @Volatile var inProgress = false
        private set

    data class Installed(val directory: String, val name: String) {
        val id: String get() = "local:$directory"
    }

    private fun tools(context: Context) = File(LinuxRuntime.rootDir(context), "root/.local/share/Steam/compatibilitytools.d")

    fun installed(context: Context): List<Installed> = tools(context).listFiles().orEmpty()
        .filter { it.name.startsWith(PREFIX) && it.isDirectory && !java.nio.file.Files.isSymbolicLink(it.toPath()) }
        .mapNotNull { tree ->
            try {
                val marker = File(tree, MARKER)
                if (java.nio.file.Files.isSymbolicLink(marker.toPath()) || marker.length() > 4096) return@mapNotNull null
                val metadata = JSONObject(marker.readText())
                if (metadata.optString("directory") != tree.name || !File(tree, "toolmanifest.vdf").isFile) null
                else Installed(tree.name, metadata.getString("name"))
            } catch (_: Exception) { null }
        }.sortedBy { it.name }

    fun install(context: Context, uri: Uri, onProgress: (String, Int) -> Unit): String? =
        synchronized(ProtonExtras) {
            if (inProgress) return@synchronized context.getString(R.string.pextra_busy)
            if (SessionState.running) return@synchronized context.getString(R.string.pextra_stop_session)
            if (!LinuxRuntime.isInstalled(context)) return@synchronized context.getString(R.string.user_apps_runtime_required)
            inProgress = true
            var archive: File? = null
            try {
                var name = ""
                var size = -1L
                context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
                    if (cursor.moveToFirst()) {
                        val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                        val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                        if (nameIndex >= 0) name = cursor.getString(nameIndex) ?: ""
                        if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) size = cursor.getLong(sizeIndex)
                    }
                }
                if (name.isNotEmpty() && !name.endsWith(".tar.gz", true) && !name.endsWith(".tar.xz", true))
                    return@synchronized context.getString(R.string.proton_import_format)
                if (size > MAX_ARCHIVE) return@synchronized context.getString(R.string.proton_import_too_large)
                val root = LinuxRuntime.rootDir(context)
                if (StatFs(root.path).availableBytes < RESERVE + size.coerceAtLeast(0L))
                    return@synchronized context.getString(R.string.proton_import_no_space)
                val downloads = File(context.filesDir, "proton-downloads").apply { mkdirs() }
                val local = File.createTempFile("local-", ".tar", downloads)
                archive = local
                val copying = context.getString(R.string.proton_import_copying)
                onProgress(copying, -1)
                context.contentResolver.openInputStream(uri)?.use { input ->
                    local.outputStream().use { output ->
                        val buffer = ByteArray(1024 * 1024)
                        var copied = 0L
                        var lastPercent = -1
                        while (true) {
                            val count = input.read(buffer)
                            if (count < 0) break
                            copied += count
                            if (copied > MAX_ARCHIVE) return@synchronized context.getString(R.string.proton_import_too_large)
                            if (StatFs(root.path).availableBytes < RESERVE + count)
                                return@synchronized context.getString(R.string.proton_import_no_space)
                            output.write(buffer, 0, count)
                            val percent = if (size > 0) (copied * 100 / size).coerceIn(0, 100).toInt() else -1
                            if (percent != lastPercent) { onProgress(copying, percent); lastPercent = percent }
                        }
                        if (size >= 0 && copied != size) return@synchronized context.getString(R.string.proton_import_invalid)
                    }
                } ?: return@synchronized context.getString(R.string.proton_import_read_failed)
                val header = ByteArray(6)
                val length = local.inputStream().use { it.read(header) }
                val gzip = length >= 2 && header[0] == 0x1f.toByte() && header[1] == 0x8b.toByte()
                val xz = length == 6 && header.contentEquals(byteArrayOf(0xfd.toByte(), 0x37, 0x7a, 0x58, 0x5a, 0))
                if (!gzip && !xz) return@synchronized context.getString(R.string.proton_import_format)
                if (SessionState.running) return@synchronized context.getString(R.string.pextra_session_started)
                onProgress(context.getString(R.string.proton_import_extracting), -1)
                runHelper(context, "install", local.absolutePath)
            } catch (error: Exception) {
                Log.e("ProtonImport", "import", error)
                context.getString(R.string.proton_import_read_failed)
            } finally {
                archive?.delete()
                inProgress = false
            }
        }

    fun remove(context: Context, id: String): String? = synchronized(ProtonExtras) {
        if (inProgress) return@synchronized context.getString(R.string.pextra_busy)
        if (SessionState.running) return@synchronized context.getString(R.string.pextra_stop_session)
        val imported = installed(context).firstOrNull { it.id == id }
            ?: return@synchronized context.getString(R.string.proton_import_invalid)
        inProgress = true
        try {
            runHelper(context, "remove", imported.directory)
        } catch (error: Exception) {
            Log.e("ProtonImport", "remove", error)
            context.getString(R.string.proton_import_failed)
        } finally { inProgress = false }
    }

    private fun runHelper(context: Context, operation: String, source: String): String? {
        val root = LinuxRuntime.rootDir(context)
        LinuxRuntime.writeAccounts(context)
        SessionFiles.stage(context, root)
        val runtimeDir = File(context.filesDir, ".proton-install-rt").apply { mkdirs() }
        val guest = listOf(
            "/usr/bin/env", "-i", "HOME=/root", "USER=root", "PATH=/usr/local/bin:/usr/bin:/bin",
            "LANG=C.UTF-8", "XDG_DATA_HOME=/root/.local/share",
            "/usr/local/bin/droiddeck-proton-import", operation, "/root/.local/share/Steam", source,
        )
        val builder = ProcessBuilder(LinuxRuntime.command(context, null, runtimeDir, null, guest))
            .directory(root).redirectErrorStream(true)
        builder.environment().apply {
            put("PROOT_LOADER", LinuxRuntime.prootLoader(context).path)
            put("PROOT_TMP_DIR", context.cacheDir.path)
            LinuxRuntime.prootLibraryPath(context).takeIf { it.isNotEmpty() }?.let { put("LD_LIBRARY_PATH", it) }
        }
        val child = builder.start()
        var result: JSONObject? = null
        val status = try {
            child.inputStream.bufferedReader().useLines { lines ->
                lines.forEach { line ->
                    Log.i("ProtonImport", line)
                    if (line.startsWith("DDJP_IMPORT ")) result = JSONObject(line.removePrefix("DDJP_IMPORT "))
                }
            }
            child.waitFor()
        } finally { child.destroy() }
        if (status == 0 && result?.optBoolean("ok") == true) return null
        val message = when (result?.optString("code")) {
            "not_arm64" -> R.string.proton_import_not_arm64
            "already_installed" -> R.string.proton_import_exists
            "no_space" -> R.string.proton_import_no_space
            "archive_too_large" -> R.string.proton_import_too_large
            "unsafe_archive", "invalid_archive", "invalid_proton" -> R.string.proton_import_invalid
            else -> R.string.proton_import_failed
        }
        return context.getString(message)
    }
}
