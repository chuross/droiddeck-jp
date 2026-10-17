package com.droiddeck.launcher.core

import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import android.content.Context
import com.droiddeck.launcher.session.SessionState

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])

class BackgroundAppLimitTest {
    private class Journal : BackgroundAppLimit.Journal {
        var active = false
        var value: String? = null
        var writable = true
        override fun pending() = active
        override fun previous() = value
        override fun save(previous: String?): Boolean {
            if (!writable) return false
            active = true; value = previous; return true
        }
        override fun clear(): Boolean { active = false; value = null; return true }
    }
    private class Device(var value: String? = "16") {
        val commands = mutableListOf<String>()
        var accept = true
        fun shell(command: String): String {
            commands += command
            when {
                command.startsWith("device_config get") -> return value ?: "null\n"
                !accept -> error("Wireless debugging unavailable")
                command.startsWith("device_config delete") -> value = null
                command.startsWith("device_config put") -> value = command.substringAfterLast(' ')
            }
            return ""
        }
    }
    @Test fun commandsAndParsing() {
        assertEquals("device_config get activity_manager max_cached_processes", BackgroundAppLimit.readCommand())
        assertEquals("device_config put activity_manager max_cached_processes 4", BackgroundAppLimit.setCommand(4))
        assertEquals("device_config put activity_manager max_cached_processes 0", BackgroundAppLimit.setCommand(0))
        assertNull(BackgroundAppLimit.parse("null\n"))
        assertNull(BackgroundAppLimit.parse(""))
        assertEquals("16", BackgroundAppLimit.parse("16\n"))
        assertEquals("device_config delete activity_manager max_cached_processes", BackgroundAppLimit.restoreCommand(null))
        assertEquals("device_config put activity_manager max_cached_processes 16", BackgroundAppLimit.restoreCommand("16"))
        assertThrows(IllegalArgumentException::class.java) { BackgroundAppLimit.parse("Permission denied") }
        assertThrows(IllegalArgumentException::class.java) { BackgroundAppLimit.restoreCommand("16; reboot") }
        assertThrows(IllegalArgumentException::class.java) { BackgroundAppLimit.setCommand(7) }
        assertEquals(BackgroundAppLimit.OFF, BackgroundAppLimit.normalize(7))
    }
    @Test fun pairedApplyAndRestoreBothPresentAndAbsentPrevious() {
        for (previous in listOf("16", null)) {
            val journal = Journal(); val device = Device(previous)
            val policy = BackgroundAppLimit.Policy(journal, device::shell)
            policy.apply(4, true)
            assertTrue(journal.active); assertEquals("4", device.value)
            policy.restore()
            assertEquals(previous, device.value); assertFalse(journal.active)
        }
    }
    @Test fun unpairedOffAndInvalidDoNotChangeDevice() {
        val journal = Journal(); val device = Device()
        val policy = BackgroundAppLimit.Policy(journal, device::shell)
        policy.apply(4, false); policy.apply(-1, true); policy.apply(17, true)
        assertTrue(device.commands.isEmpty()); assertFalse(journal.active)
    }
    @Test fun journalFailurePreventsMutation() {
        val journal = Journal().apply { writable = false }; val device = Device()
        assertThrows(IllegalStateException::class.java) { BackgroundAppLimit.Policy(journal, device::shell).apply(1, true) }
        assertEquals(listOf(BackgroundAppLimit.readCommand()), device.commands)
        assertEquals("16", device.value)
    }
    @Test fun crashRecoveryRetriesAndNewSessionDoesNotOverwriteOriginal() {
        val journal = Journal(); val device = Device()
        BackgroundAppLimit.Policy(journal, device::shell).apply(0, true)
        device.accept = false
        assertThrows(IllegalStateException::class.java) { BackgroundAppLimit.Policy(journal, device::shell).restore() }
        assertTrue(journal.active)
        device.accept = true
        val nextProcess = BackgroundAppLimit.Policy(journal, device::shell)
        nextProcess.apply(1, true)
        assertEquals("16", journal.value)
        nextProcess.restore()
        assertEquals("16", device.value)
    }
    @Test fun unsuccessfulCheckKeepsJournalForNextLaunch() {
        val journal = Journal()
        val policy = BackgroundAppLimit.Policy(journal) { if (it.startsWith("device_config get")) "16" else "" }
        assertThrows(IllegalStateException::class.java) { policy.apply(4, true) }
        assertTrue(journal.active); assertEquals("16", journal.value)
    }

    @Test fun defaultsCorruptPreferencesAndUnpairedEnablement() {
        val context = RuntimeEnvironment.getApplication()
        val prefs = context.getSharedPreferences("droiddeck-patch.background-memory", Context.MODE_PRIVATE)
        prefs.edit().clear().commit()
        assertEquals(BackgroundAppLimit.OFF, BackgroundAppLimit.limit(context))
        assertFalse(BackgroundAppLimit.clearCached(context))
        assertFalse(WirelessAdbFix.hasSavedPairing(context))
        BackgroundAppLimit.setLimit(context, 4)
        BackgroundAppLimit.setClearCached(context, true)
        assertEquals(BackgroundAppLimit.OFF, BackgroundAppLimit.limit(context))
        assertFalse(BackgroundAppLimit.clearCached(context))
        prefs.edit().putInt("limit", 7).commit()
        assertEquals(BackgroundAppLimit.OFF, BackgroundAppLimit.limit(context))
        prefs.edit().putString("limit", "invalid").commit()
        assertEquals(BackgroundAppLimit.OFF, BackgroundAppLimit.limit(context))
    }
    @Test fun activityRecoveryCannotRestoreRunningSessionAndRetainsFailedJournal() {
        val context = RuntimeEnvironment.getApplication()
        val prefs = context.getSharedPreferences("droiddeck-patch.background-memory", Context.MODE_PRIVATE)
        prefs.edit().clear().putBoolean("pending", true).putString("previous", "16").commit()
        SessionState.running = true
        try {
            BackgroundAppLimit.recoverAsync(context).get()
            assertTrue(prefs.getBoolean("pending", false))
        } finally { SessionState.running = false }
        // No pairing: recovery keeps the journal available for a later launch.
        BackgroundAppLimit.recoverAsync(context).get()
        assertTrue(prefs.getBoolean("pending", false))
        assertEquals("16", prefs.getString("previous", null))
    }
}
