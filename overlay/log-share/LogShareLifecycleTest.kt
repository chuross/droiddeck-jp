package com.droiddeck.launcher.session

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import java.util.zip.ZipFile

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], manifest = Config.NONE)
class LogShareLifecycleTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test fun collectionDuringZipNeverReplacesLogsWithAnOpenWriter() {
        val context = RuntimeEnvironment.getApplication()
        val folder = tmp.newFolder("2026-10-12-01-steam")
        val log = File(folder, "app.log").apply { writeText("token=abcdef123456\n") }
        val events = File(folder, "events.jsonl").apply { writeText("{\"event\":\"initial\"}\n") }
        val failure = AtomicReference<Throwable?>()
        val collected = CountDownLatch(1)
        val original = log.readText()
        FileOutputStream(log, true).use { writer ->
            var started = false
            val zip = SessionLogShare.zipFolder(context, folder) {
                if (!started) {
                    started = true
                    Thread {
                        try { SessionArtifacts.collect(context, folder, "test concurrent teardown") }
                        catch (error: Throwable) { failure.set(error) }
                        finally { collected.countDown() }
                    }.start()
                    assertTrue(collected.await(20, TimeUnit.SECONDS))
                    writer.write("after teardown\n".toByteArray())
                }
            }!!
            assertTrue(started)
            assertNull(failure.get())
            assertEquals(original + "after teardown\n", log.readText())
            assertTrue(events.readText().startsWith("{\"event\":\"initial\"}\n"))
            ZipFile(zip).use { archive ->
                val entry = archive.getEntry("${folder.name}/events.jsonl")
                assertNotNull(entry)
                assertTrue(entry.size > 0)
                assertTrue(archive.getInputStream(entry).bufferedReader().readText().contains("initial"))
                assertFalse(archive.getInputStream(archive.getEntry("${folder.name}/app.log"))
                    .bufferedReader().readText().contains("abcdef123456"))
            }
        }
    }

    @Test fun failedFileDoesNotPreventSharingRemainingLogsOrChangeLifecycle() {
        val context = RuntimeEnvironment.getApplication()
        val folder = tmp.newFolder("2026-10-12-02-steam")
        File(folder, "events.jsonl").writeText("{\"event\":\"initial\"}\n")
        File(folder, "oversized.log").bufferedWriter().use { out -> repeat(128) { out.write("x".repeat(1024)) } }
        SessionState.running = true
        val phase = SessionState.phase
        try {
            assertTrue(SessionState.running)
            assertEquals(phase, SessionState.phase)
            val zip = SessionLogShare.zipFolder(context, folder)!!
            ZipFile(zip).use { archive ->
                assertNull(archive.getEntry("${folder.name}/oversized.log"))
                assertTrue(archive.getEntry("${folder.name}/events.jsonl").size > 0)
            }
        } finally { SessionState.running = false }
    }

    @Test fun includesRecentStoresAndToolsWithUpstreamSharePrivacyRules() {
        val context = RuntimeEnvironment.getApplication()
        val folder = tmp.newFolder("2026-10-12-05-steam")
        File(folder, "events.jsonl").writeText("{\"event\":\"initial\"}\n")
        val logs = com.droiddeck.launcher.runtime.LinuxRuntime.logDir(context)
        val stores = File(logs, SessionLogShare.STORES_DIR).apply { mkdirs() }
        val tools = File(logs, SessionPaths.TOOLS_DIR).apply { mkdirs() }
        val store = File(stores, "recent-store.log").apply { writeText("address 192.168.1.42\n") }
        val tool = File(tools, "recent-tool.log").apply { writeText("url https://example.org/a?private=value\n") }
        val expired = File(stores, "expired-store.log").apply {
            writeText("old log\n")
            setLastModified(System.currentTimeMillis() - 8 * 86_400_000L)
        }
        try {
            ZipFile(SessionLogShare.zipFolder(context, folder)!!).use { archive ->
                val storeEntry = archive.getEntry("stores/${store.name}")
                val toolEntry = archive.getEntry("tools/${tool.name}")
                assertNotNull(storeEntry)
                assertNotNull(toolEntry)
                assertNull(archive.getEntry("stores/${expired.name}"))
                assertFalse(archive.getInputStream(storeEntry).bufferedReader().readText().contains("192.168.1.42"))
                assertFalse(archive.getInputStream(toolEntry).bufferedReader().readText().contains("private=value"))
            }
            assertTrue(store.readText().contains("192.168.1.42"))
            assertTrue(tool.readText().contains("private=value"))
        } finally { store.delete(); tool.delete(); expired.delete() }
    }

    @Test fun simultaneousSharesKeepBothArchivesAndExcludeDiagnosticTemporaries() {
        val context = RuntimeEnvironment.getApplication()
        val folder = tmp.newFolder("2026-10-12-03-steam")
        File(folder, "events.jsonl").writeText("{\"event\":\"initial\"}\n")
        File(folder, ".diagnostic-test.tmp").writeText("do not share")
        val results = java.util.Collections.synchronizedList(mutableListOf<File>())
        val failure = AtomicReference<Throwable?>()
        val threads = List(2) { Thread {
            try { results.add(SessionLogShare.zipFolder(context, folder)!!) }
            catch (error: Throwable) { failure.set(error) }
        } }
        threads.forEach { it.start() }
        threads.forEach { it.join(20000); assertFalse(it.isAlive) }
        assertNull(failure.get())
        assertEquals(2, results.distinct().size)
        results.forEach { zip -> ZipFile(zip).use { archive ->
            assertTrue(archive.getEntry("${folder.name}/events.jsonl").size > 0)
            assertNull(archive.getEntry("${folder.name}/.diagnostic-test.tmp"))
        } }
    }
}
