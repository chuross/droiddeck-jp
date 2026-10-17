package com.droiddeck.launcher.core

import android.content.Context
import android.util.Log
import com.droiddeck.launcher.session.SessionState
import java.util.concurrent.Executors
import java.util.concurrent.Future

/** Temporary session policy. All preferences and the durable restore journal belong to this overlay. */
object BackgroundAppLimit {
    const val OFF = -1
    val CHOICES = listOf(OFF, 4, 1, 0)
    private const val STATE = "droiddeck-patch.background-memory"
    private val worker = Executors.newSingleThreadExecutor { Thread(it, "background-memory").apply { isDaemon = true } }
    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(STATE, Context.MODE_PRIVATE)

    internal fun normalize(value: Int) = value.takeIf { it in CHOICES } ?: OFF
    fun limit(context: Context) = normalize(runCatching { prefs(context).getInt("limit", OFF) }.getOrDefault(OFF))
    fun clearCached(context: Context) = runCatching { prefs(context).getBoolean("clear", false) }.getOrDefault(false)
    fun setLimit(context: Context, value: Int) {
        require(value in CHOICES)
        if (value != OFF && !WirelessAdbFix.hasSavedPairing(context)) return
        prefs(context).edit().putInt("limit", value).apply()
        if (value == OFF) restoreAsync(context)
    }
    fun setClearCached(context: Context, value: Boolean) {
        if (value && !WirelessAdbFix.hasSavedPairing(context)) return
        prefs(context).edit().putBoolean("clear", value).apply()
    }

    internal fun readCommand() = "device_config get activity_manager max_cached_processes"
    internal fun setCommand(value: Int): String {
        require(value in CHOICES && value != OFF)
        return "device_config put activity_manager max_cached_processes $value"
    }
    // Unexpected/error output must never be mistaken for an absent value.
    internal fun parse(output: String): String? {
        val value = output.trim()
        if (value == "null" || value.isEmpty()) return null
        require(Regex("-?\\d+").matches(value) && value.toIntOrNull() != null) { "Invalid device_config value" }
        return value
    }
    internal fun restoreCommand(previous: String?): String = if (previous == null)
        "device_config delete activity_manager max_cached_processes"
    else {
        require(parse(previous) == previous)
        "device_config put activity_manager max_cached_processes $previous"
    }

    internal interface Journal {
        fun pending(): Boolean
        fun previous(): String?
        fun save(previous: String?): Boolean
        fun clear(): Boolean
    }
    internal class Policy(private val journal: Journal, private val shell: (String) -> String) {
        fun restore() {
            if (!journal.pending()) return
            val previous = journal.previous()
            shell(restoreCommand(previous))
            check(parse(shell(readCommand())) == previous) { "Restore not confirmed" }
            check(journal.clear()) { "Restore journal not cleared" }
        }
        fun apply(value: Int, paired: Boolean) {
            if (!paired) return
            // Recover a preceding crashed session before taking a new snapshot.
            restore()
            if (normalize(value) == OFF) return
            val previous = parse(shell(readCommand()))
            check(journal.save(previous)) { "Previous value not saved; limit unchanged" }
            shell(setCommand(value))
            check(parse(shell(readCommand())) == value.toString()) { "Limit not confirmed" }
        }
    }
    private fun policy(context: Context): Policy {
        val state = prefs(context)
        return Policy(object : Journal {
            override fun pending() = state.getBoolean("pending", false)
            override fun previous() = state.getString("previous", null)
            override fun save(previous: String?) = state.edit().putBoolean("pending", true).putString("previous", previous).commit()
            override fun clear() = state.edit().remove("pending").remove("previous").commit()
        }) { WirelessAdbFix.shell(context, it) }
    }
    private fun enqueue(context: Context, action: (Context) -> Unit): Future<*> {
        val app = context.applicationContext
        return worker.submit { runCatching { action(app) }.onFailure { Log.w("BackgroundAppLimit", "Memory policy: ${it.message}") } }
    }
    /** Enqueued at session start; its caller waits on the session worker before launching the guest. */
    fun applyAsync(context: Context): Future<*> = enqueue(context) { app ->
        val paired = WirelessAdbFix.hasSavedPairing(app)
        policy(app).apply(limit(app), paired)
        if (paired && clearCached(app)) WirelessAdbFix.shell(app, "am kill-all")
    }
    fun restoreAsync(context: Context): Future<*> = enqueue(context) { policy(it).restore() }
    /** Check again on the worker so an Activity restart cannot restore a running session's limit. */
    fun recoverAsync(context: Context): Future<*> = enqueue(context) {
        if (!SessionState.running) policy(it).restore()
    }
}
