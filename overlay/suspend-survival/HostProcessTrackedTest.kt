package com.droiddeck.launcher.core

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class HostProcessTrackedTest {
    @Test fun immediateExitReportsThePidOfItsOwnStart() {
        val callbacks = List(12) { CountDownLatch(1) }
        val exitedPids = List(12) { AtomicInteger(Int.MIN_VALUE) }
        val statuses = List(12) { AtomicInteger(Int.MIN_VALUE) }
        val pids = callbacks.indices.map { index ->
            HostProcess.startTracked("/bin/sh -c exit\\ $index", null, null, { pid, status ->
                exitedPids[index].set(pid)
                statuses[index].set(status)
                callbacks[index].countDown()
            }, null)
        }
        callbacks.indices.forEach { index ->
            assertTrue("missing exit callback", callbacks[index].await(5, TimeUnit.SECONDS))
            assertEquals(pids[index], exitedPids[index].get())
            assertEquals(index, statuses[index].get())
        }
    }

    @Test fun existingStatusOnlyCallbackStillWorks() {
        val done = CountDownLatch(1)
        val status = AtomicInteger(-1)
        HostProcess.start("/bin/sh -c exit\\ 7", null, null, {
            status.set(it)
            done.countDown()
        }, null)
        assertTrue(done.await(5, TimeUnit.SECONDS))
        assertEquals(7, status.get())
    }
}
