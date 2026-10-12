package com.droiddeck.launcher.session

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

class SessionLogSnapshotTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test fun concurrentAppendPreservesEveryExistingJsonLineAndSourceInode() {
        val source = tmp.newFile("events.jsonl")
        val initial = (0 until 2000).joinToString("") { "{\"event\":\"initial\",\"n\":$it}\n" }
        source.writeText(initial)
        val started = CountDownLatch(1)
        val failure = AtomicReference<Throwable?>()
        val writer = Thread {
            try {
                FileOutputStream(source, true).use { output ->
                    started.countDown()
                    repeat(5000) { output.write("{\"event\":\"append\",\"n\":$it}\n".toByteArray()) }
                }
            } catch (error: Throwable) { failure.set(error) }
        }
        writer.start()
        assertTrue(started.await(5, TimeUnit.SECONDS))
        repeat(4) {
            val snapshot = tmp.newFile()
            SessionLogSnapshot.redact(source, snapshot)
            assertTrue(snapshot.readText().startsWith(initial))
            assertTrue(snapshot.length() > 0)
        }
        writer.join(5000)
        assertFalse(writer.isAlive)
        assertNull(failure.get())
        assertEquals(initial + (0 until 5000).joinToString("") { "{\"event\":\"append\",\"n\":$it}\n" }, source.readText())
    }

    @Test fun largeLogsAreStreamedAndSourceRemainsByteForByteUnchanged() {
        val line = "token=abcdef123456 " + "safe ".repeat(200) + "\n"
        for (mb in listOf(8, 16, 32)) {
            val source = tmp.newFile("large-$mb.log")
            val rows = mb * 1024 * 1024 / line.toByteArray().size + 1
            source.bufferedWriter().use { output -> repeat(rows) { output.write(line) } }
            val snapshot = tmp.newFile("redacted-$mb.log")
            SessionLogSnapshot.redact(source, snapshot)
            var count = 0
            snapshot.forEachLine {
                assertFalse(it.contains("abcdef123456"))
                assertTrue(it.startsWith("token=<redacted:token> "))
                count++
            }
            assertEquals(rows, count)
            source.forEachLine { assertEquals(line.removeSuffix("\n"), it) }
        }
    }

    @Test fun incompleteJsonAppendIsOmittedAndOversizedLineFailsClosed() {
        val source = tmp.newFile("events.jsonl").apply { writeText("{\"event\":\"kept\"}\n{\"event\":") }
        val snapshot = tmp.newFile()
        SessionLogSnapshot.redact(source, snapshot)
        assertEquals("{\"event\":\"kept\"}\n", snapshot.readText())
        source.bufferedWriter().use { out -> repeat(1024) { out.write("x".repeat(1024)) } }
        try { SessionLogSnapshot.redact(source, snapshot); fail("unbounded line was accepted") }
        catch (_: java.io.IOException) { }
        assertEquals(1024L * 1024, source.length())
    }
}
