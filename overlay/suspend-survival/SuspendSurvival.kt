package com.droiddeck.launcher.session

import android.app.ActivityManager
import android.app.ApplicationExitInfo
import android.content.Context
import android.os.Build
import android.os.Process
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import com.droiddeck.launcher.core.LogRedactor
import org.json.JSONObject
import java.io.File
import java.io.FileNotFoundException
import java.io.FileOutputStream

/** Best-effort evidence; no diagnostic failure may stop, resume or terminate a session. */
internal object SuspendSurvival {
    private const val TAG = "SuspendSurvival"
    private const val IDENTITY = "session-process.json"
    // Non-secret correlation key that survives the existing GUID/token log redactor.
    private val processInstance = "pid_${Process.myPid()}_uptime_${Process.getStartUptimeMillis()}"
    private val processStartedAt = System.currentTimeMillis() -
        (SystemClock.uptimeMillis() - Process.getStartUptimeMillis())

    internal data class Identity(
        val instance: String, val pid: Int, val processName: String,
        val processStartedAt: Long, val sessionStartedAt: Long, val bootCount: Int,
    )

    internal data class Exit(
        val pid: Int, val processName: String, val timestamp: Long, val reason: Int,
        val status: Int, val importance: Int, val pssKb: Long, val rssKb: Long,
        val description: String?, val instance: String? = null,
    )

    internal data class Match(val outcome: String, val exit: Exit? = null)

    /** Never attribute the latest app death to every abandoned folder. Ambiguity stays unknown. */
    internal fun matchExit(identity: Identity, exits: List<Exit>, bootCount: Int, now: Long): Match {
        if (identity.bootCount < 0 || bootCount < 0) return Match("boot_identity_unavailable")
        if (identity.bootCount != bootCount) return Match("device_rebooted")
        if (identity.sessionStartedAt < identity.processStartedAt || now < identity.sessionStartedAt)
            return Match("clock_or_identity_invalid")
        val candidates = exits.filter {
            it.pid == identity.pid && it.processName == identity.processName &&
                it.timestamp >= identity.sessionStartedAt &&
                it.timestamp >= identity.processStartedAt && it.timestamp <= now &&
                (it.instance == null || it.instance == identity.instance)
        }
        val exact = candidates.filter { it.instance == identity.instance }
        if (exact.size == 1) return Match("matched_process_instance", exact.single())
        return when (candidates.size) {
            0 -> Match("history_unavailable")
            1 -> Match("unverified_pid_name_time_boot", candidates.single())
            else -> Match("ambiguous_pid_reuse")
        }
    }

    private fun bootCount(context: Context): Int =
        runCatching { Settings.Global.getInt(context.contentResolver, Settings.Global.BOOT_COUNT, -1) }
            .getOrDefault(-1)

    fun remember(context: Context, dir: File) {
        runCatching {
            val file = File(dir, IDENTITY)
            if (file.exists()) return
            val summaryInstalled = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && runCatching {
                val manager = context.getSystemService(ActivityManager::class.java) ?: return@runCatching false
                manager.setProcessStateSummary(processInstance.toByteArray(Charsets.UTF_8))
                true
            }.getOrDefault(false)
            val identity = JSONObject()
                .put("processInstance", processInstance)
                .put("processStateSummaryInstalled", summaryInstalled)
                .put("appPid", Process.myPid())
                .put("processName", context.applicationInfo.processName ?: context.packageName)
                .put("processStartedAt", processStartedAt)
                .put("sessionStartedAt", System.currentTimeMillis())
                .put("elapsedRealtime", SystemClock.elapsedRealtime())
                .put("bootCount", bootCount(context))
            writeJson(file, identity)
        }.onFailure { Log.w(TAG, "could not persist session process identity", it) }
    }

    fun recoverExit(context: Context, dir: File) {
        runCatching {
            val report = JSONObject().put("capturedAt", System.currentTimeMillis())
            val metadata = File(dir, IDENTITY)
            if (!metadata.isFile) {
                report.put("outcome", "session_identity_unavailable")
            } else if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
                report.put("outcome", "unsupported_before_android_11")
            } else {
                val json = JSONObject(metadata.readText())
                val identity = Identity(
                    json.getString("processInstance"), json.getInt("appPid"),
                    json.getString("processName"), json.getLong("processStartedAt"),
                    json.getLong("sessionStartedAt"), json.getInt("bootCount"),
                )
                report.put("processInstance", identity.instance).put("appPid", identity.pid)
                if (identity.instance == processInstance) {
                    report.put("outcome", "same_process_alive")
                } else {
                    val manager = context.getSystemService(ActivityManager::class.java)
                    val exits = manager?.getHistoricalProcessExitReasons(context.packageName, identity.pid, 32)
                        .orEmpty().map {
                            Exit(it.pid, it.processName, it.timestamp, it.reason, it.status,
                                it.importance, it.pss, it.rss, it.description,
                                it.processStateSummary?.toString(Charsets.UTF_8))
                        }
                    val match = matchExit(identity, exits, bootCount(context), System.currentTimeMillis())
                    report.put("outcome", match.outcome)
                    match.exit?.let { exit ->
                        report.put("timestamp", exit.timestamp).put("processName", exit.processName)
                            .put("reason", exit.reason).put("reasonName", reasonName(exit.reason))
                            .put("status", exit.status).put("importance", exit.importance)
                            .put("pssKb", exit.pssKb).put("rssKb", exit.rssKb)
                            .put("description", exit.description?.take(2048) ?: JSONObject.NULL)
                    }
                }
            }
            writeJson(File(dir, "process-exit.json"), report)
            SessionEvents.record("session.process_exit_history", mapOf("outcome" to report.getString("outcome")), dir)
        }.onFailure {
            Log.w(TAG, "could not recover process exit history", it)
            runCatching { writeJson(File(dir, "process-exit.json"),
                JSONObject().put("outcome", "diagnostic_failed").put("error", it.javaClass.simpleName)) }
        }
    }

    private fun reasonName(reason: Int): String = when (reason) {
        ApplicationExitInfo.REASON_LOW_MEMORY -> "LOW_MEMORY"
        ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE -> "EXCESSIVE_RESOURCE_USAGE"
        ApplicationExitInfo.REASON_SIGNALED -> "SIGNALED"
        ApplicationExitInfo.REASON_CRASH_NATIVE -> "CRASH_NATIVE"
        ApplicationExitInfo.REASON_CRASH -> "CRASH"
        ApplicationExitInfo.REASON_ANR -> "ANR"
        ApplicationExitInfo.REASON_USER_REQUESTED -> "USER_REQUESTED"
        ApplicationExitInfo.REASON_USER_STOPPED -> "USER_STOPPED"
        ApplicationExitInfo.REASON_EXIT_SELF -> "EXIT_SELF"
        ApplicationExitInfo.REASON_DEPENDENCY_DIED -> "DEPENDENCY_DIED"
        ApplicationExitInfo.REASON_INITIALIZATION_FAILURE -> "INITIALIZATION_FAILURE"
        ApplicationExitInfo.REASON_PERMISSION_CHANGE -> "PERMISSION_CHANGE"
        else -> "OTHER_OR_UNKNOWN"
    }

    private fun writeJson(file: File, value: JSONObject) {
        file.parentFile?.mkdirs()
        val temporary = File(file.parentFile, file.name + ".tmp")
        FileOutputStream(temporary).use {
            it.write((LogRedactor.redact(value.toString()) + "\n").toByteArray(Charsets.UTF_8))
            it.fd.sync()
        }
        check(temporary.renameTo(file)) { "could not publish diagnostic" }
    }

    internal data class Stat(val started: Long, val state: Char, val parent: Int)

    internal fun parseStat(text: String): Stat? = runCatching {
        val close = text.lastIndexOf(')')
        if (close < 0) return null
        val fields = text.substring(close + 1).trim().split(Regex("\\s+"))
        Stat(fields[19].toLong(), fields[0].single(), fields[1].toInt())
    }.getOrNull()

    internal fun classify(expectedStarted: Long?, stat: Stat?): String = when {
        stat == null -> "unreadable"
        expectedStarted != null && stat.started != expectedStarted -> "pid_reused"
        stat.state == 'Z' || stat.state == 'X' -> "dead"
        stat.state == 'T' || stat.state == 't' -> "stopped"
        else -> "running"
    }

    /** Does not record command lines: guest arguments may contain credentials. */
    fun inspect(pid: Int, expectedStarted: Long? = null, role: String = "UNKNOWN",
                proc: File = File("/proc")): Map<String, Any?> {
        val directory = File(proc, pid.toString())
        val result = linkedMapOf<String, Any?>("pid" to pid, "role" to role,
            "expectedStarted" to expectedStarted)
        try {
            val stat = parseStat(File(directory, "stat").readText())
            result["outcome"] = classify(expectedStarted, stat)
            result["started"] = stat?.started
            result["state"] = stat?.state?.toString()
            result["parent"] = stat?.parent
        } catch (error: FileNotFoundException) {
            // ENOENT and permission denials both surface as FileNotFoundException on Android.
            result["outcome"] = if (error.message?.contains("ENOENT") == true ||
                error.message?.contains("No such file") == true) "gone" else "unreadable"
        } catch (_: Exception) {
            result["outcome"] = "unreadable"
        }
        result["oomScoreAdj"] = runCatching {
            File(directory, "oom_score_adj").readText().trim().toInt()
        }.getOrNull()
        return result
    }
}
