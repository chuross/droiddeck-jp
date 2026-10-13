package com.droiddeck.launcher.frontend

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class DependencyDetectorRootTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test fun parentDirectoryCannotAddAVcRuntimeYear() {
        val game = File(tmp.newFolder("2022"), "Game").apply { mkdirs() }
        File(game, "_CommonRedist/vcredist/2013/vcredist_x64.exe").apply {
            parentFile!!.mkdirs()
            writeText("x")
        }
        assertEquals(listOf("vcredist2013"), DependencyDetector.detect(game).map { it.componentName })
    }

    @Test fun parentDirectoryCannotHideBundledDependencies() {
        val game = File(tmp.newFolder("anticheat-dotnet-2022"), "Game").apply { mkdirs() }
        File(game, "_CommonRedist/vcredist/2013/vcredist_x64.exe").apply {
            parentFile!!.mkdirs()
            writeText("x")
        }
        assertEquals(listOf("vcredist2013"), DependencyDetector.detect(game).map { it.componentName })
    }

    @Test fun parentRuntimeNameCannotClassifyAnUnrelatedExecutable() {
        val game = File(tmp.newFolder("dotnet-4.8"), "Game").apply { mkdirs() }
        File(game, "Game.exe").writeText("x")
        assertEquals(emptyList<String>(), DependencyDetector.detect(game).map { it.componentName })
    }
}
