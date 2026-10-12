package com.droiddeck.launcher.session

import android.content.Context
import android.app.ActivityManager
import android.app.ApplicationExitInfo
import android.os.PowerManager
import android.net.wifi.WifiManager
import android.provider.Settings
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLooper
import org.robolectric.Shadows.shadowOf
import org.robolectric.util.ReflectionHelpers
import java.io.File
import java.util.concurrent.TimeUnit
import java.nio.file.Files

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], manifest = Config.NONE)
class SuspendSurvivalTest {
    private val identity = SuspendSurvival.Identity("old-process", 123, "example.app", 100, 200, 4)
    private fun exit(pid: Int = 123, name: String = "example.app", time: Long = 300) =
        SuspendSurvival.Exit(pid, name, time, 3, 9, 125, 64, 128, "test")

    @Before fun reset() {
        SessionState.running = false
        SessionState.suspended = false
        SessionState.logDirectory = null
        SessionState.eventsFile = null
        RuntimeEnvironment.getApplication().getSharedPreferences("session", Context.MODE_PRIVATE).edit().clear().commit()
    }

    @Test fun matchesOnlyThisPidNameBootAndSessionTime() {
        val exits = listOf(exit(pid = 456), exit(name = "other.app"), exit(time = 150), exit(time = 600), exit())
        assertEquals(exit(), SuspendSurvival.matchExit(identity, exits, 4, 500).exit)
        assertNull(SuspendSurvival.matchExit(identity, exits, 5, 500).exit)
        assertNull(SuspendSurvival.matchExit(identity, exits, -1, 500).exit)
        assertNull(SuspendSurvival.matchExit(identity, exits, 4, 199).exit)
    }

    @Test fun ambiguousPidReuseAndMissingHistoryStayUnknown() {
        assertEquals("ambiguous_pid_reuse",
            SuspendSurvival.matchExit(identity, listOf(exit(), exit(time = 400)), 4, 500).outcome)
        assertEquals("history_unavailable", SuspendSurvival.matchExit(identity, emptyList(), 4, 500).outcome)
    }

    @Test fun systemProcessSummaryDisambiguatesReusedPidAndMarksFallbackUnverified() {
        val real = exit().copy(instance = identity.instance)
        val reused = exit(time = 400).copy(instance = "a-different-process")
        val match = SuspendSurvival.matchExit(identity, listOf(real, reused), 4, 500)
        assertEquals("matched_process_instance", match.outcome)
        assertEquals(real, match.exit)
        assertNull(SuspendSurvival.matchExit(identity, listOf(reused), 4, 500).exit)
        assertEquals("unverified_pid_name_time_boot",
            SuspendSurvival.matchExit(identity, listOf(exit()), 4, 500).outcome)
    }

    @Test fun processEvidenceDistinguishesStopDeathReuseAndUnreadable() {
        for (state in listOf('T', 't')) assertEquals("stopped",
            SuspendSurvival.classify(42, SuspendSurvival.Stat(42, state, 1)))
        for (state in listOf('Z', 'X')) assertEquals("dead",
            SuspendSurvival.classify(42, SuspendSurvival.Stat(42, state, 1)))
        assertEquals("running", SuspendSurvival.classify(42, SuspendSurvival.Stat(42, 'S', 1)))
        assertEquals("pid_reused", SuspendSurvival.classify(42, SuspendSurvival.Stat(43, 'S', 1)))
        assertEquals("unreadable", SuspendSurvival.classify(42, null))
        // comm includes both spaces and ')' characters.
        val text = "123 (a name) with parens)) T 1 " + List(17) { "0" }.joinToString(" ") + " 42"
        assertEquals(SuspendSurvival.Stat(42, 'T', 1), SuspendSurvival.parseStat(text))
        assertNull(SuspendSurvival.parseStat("malformed"))
    }

    @Test fun procSnapshotIncludesOomScoreWithoutCommandArguments() {
        val root = Files.createTempDirectory("survival-proc").toFile()
        try {
            val proc = File(root, "123").apply { mkdirs() }
            File(proc, "stat").writeText("123 (game with spaces) S 1 " + List(17) { "0" }.joinToString(" ") + " 42")
            File(proc, "oom_score_adj").writeText("200\n")
            File(proc, "cmdline").writeText("secret-token")
            val evidence = SuspendSurvival.inspect(123, 42, "SESSION_ROOT", root)
            assertEquals("running", evidence["outcome"])
            assertEquals(200, evidence["oomScoreAdj"])
            assertFalse(evidence.toString().contains("secret-token"))
            File(proc, "stat").writeText("unreadable stat")
            assertEquals("unreadable", SuspendSurvival.inspect(123, 42, proc = root)["outcome"])
        } finally { root.deleteRecursively() }
    }

    @Test fun keepaliveIsOptInAndSharedAcrossSessionModes() {
        val context = RuntimeEnvironment.getApplication()
        assertFalse(SessionPrefs.keepCpuAwakeWhileSuspended(context))
        SessionPrefs.setKeepCpuAwakeWhileSuspended(context, true)
        assertTrue(SessionPrefs.keepCpuAwakeWhileSuspended(context))
        SessionPrefs.setKeepCpuAwakeWhileSuspended(context, false)
        assertFalse(SessionPrefs.keepCpuAwakeWhileSuspended(context))
    }

    @Test fun identityIsDurableAndNotReplacedByASecondRemember() {
        val dir = Files.createTempDirectory("session-identity").toFile()
        try {
            val context = RuntimeEnvironment.getApplication()
            SuspendSurvival.remember(context, dir)
            val file = File(dir, "session-process.json")
            val original = file.readText()
            assertTrue(JSONObject(original).has("processInstance"))
            SuspendSurvival.remember(context, dir)
            assertEquals(original, file.readText())
            SuspendSurvival.recoverExit(context, dir)
            assertEquals("unsupported_before_android_11",
                JSONObject(File(dir, "process-exit.json").readText()).getString("outcome"))
        } finally { dir.deleteRecursively() }
    }

    @Test fun missingIdentityDoesNotAttachUnrelatedExitHistory() {
        val dir = Files.createTempDirectory("missing-identity").toFile()
        try {
            SuspendSurvival.recoverExit(RuntimeEnvironment.getApplication(), dir)
            assertEquals("session_identity_unavailable",
                JSONObject(File(dir, "process-exit.json").readText()).getString("outcome"))
        } finally { dir.deleteRecursively() }
    }

    @Test @Config(sdk = [30]) fun currentProcessIsNotMistakenForAHistoricalDeath() {
        val dir = Files.createTempDirectory("alive-process").toFile()
        try {
            val context = RuntimeEnvironment.getApplication()
            SuspendSurvival.remember(context, dir)
            SuspendSurvival.recoverExit(context, dir)
            assertEquals("same_process_alive",
                JSONObject(File(dir, "process-exit.json").readText()).getString("outcome"))
        } finally { dir.deleteRecursively() }
    }

    @Test @Config(sdk = [30]) fun recoversSystemExitReasonIntoTheMatchingAbandonedFolder() {
        val dir = Files.createTempDirectory("historical-exit").toFile()
        try {
            val context = RuntimeEnvironment.getApplication()
            Settings.Global.putInt(context.contentResolver, Settings.Global.BOOT_COUNT, 4)
            SuspendSurvival.remember(context, dir)
            val file = File(dir, "session-process.json")
            val metadata = JSONObject(file.readText()).put("processInstance", "old-process")
                .put("appPid", 123).put("processStartedAt", 100).put("sessionStartedAt", 200)
            file.writeText(metadata.toString())
            val info = ReflectionHelpers.callConstructor(ApplicationExitInfo::class.java)
            ReflectionHelpers.setField(info, "mPid", 123)
            ReflectionHelpers.setField(info, "mProcessName", metadata.getString("processName"))
            ReflectionHelpers.setField(info, "mTimestamp", 300L)
            ReflectionHelpers.setField(info, "mReason", ApplicationExitInfo.REASON_LOW_MEMORY)
            ReflectionHelpers.setField(info, "mStatus", 9)
            ReflectionHelpers.setField(info, "mPss", 64L)
            ReflectionHelpers.setField(info, "mRss", 128L)
            ReflectionHelpers.setField(info, "mState", "old-process".toByteArray())
            val manager = context.getSystemService(ActivityManager::class.java)
            shadowOf(manager).addApplicationExitInfo(info)
            SuspendSurvival.recoverExit(context, dir)
            val report = JSONObject(File(dir, "process-exit.json").readText())
            assertEquals("matched_process_instance", report.getString("outcome"))
            assertEquals("LOW_MEMORY", report.getString("reasonName"))
            assertEquals(9, report.getInt("status"))
            assertEquals(64, report.getInt("pssKb"))
            assertEquals(128, report.getInt("rssKb"))
        } finally { dir.deleteRecursively() }
    }

    private fun field(service: SessionService, name: String) =
        SessionService::class.java.getDeclaredField(name).apply { isAccessible = true }

    private fun invoke(service: SessionService, name: String) {
        SessionService::class.java.getDeclaredMethod(name).apply { isAccessible = true }.invoke(service)
    }

    @Test fun offReleasesBothLocksOnRetainsOnlyCpuAndTeardownReleasesIt() {
        val service = Robolectric.buildService(SessionService::class.java).get()
        invoke(service, "acquireLocks")
        assertTrue((field(service, "wakeLock").get(service) as PowerManager.WakeLock).isHeld)
        assertTrue((field(service, "wifiLock").get(service) as WifiManager.WifiLock).isHeld)
        invoke(service, "applySuspendedLocks")
        assertNull(field(service, "wakeLock").get(service))
        assertNull(field(service, "wifiLock").get(service))

        field(service, "keepCpuAwakeWhileSuspended").setBoolean(service, true)
        SessionState.running = true
        SessionState.suspended = true
        invoke(service, "acquireLocks")
        invoke(service, "applySuspendedLocks")
        val cpu = field(service, "wakeLock").get(service) as PowerManager.WakeLock
        assertTrue(cpu.isHeld)
        assertNull(field(service, "wifiLock").get(service))
        // Renewal must still hold the lock beyond the original 12-hour timeout.
        ShadowLooper.idleMainLooper(13, TimeUnit.HOURS)
        assertTrue(cpu.isHeld)
        invoke(service, "releaseLocks")
        assertFalse(cpu.isHeld)
        ShadowLooper.idleMainLooper(13, TimeUnit.HOURS)
        assertNull(field(service, "wakeLock").get(service))
        SessionState.running = false
        SessionState.suspended = false
    }
}
