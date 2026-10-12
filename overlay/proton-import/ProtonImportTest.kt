package com.droiddeck.launcher.session

import com.droiddeck.launcher.runtime.LinuxRuntime
import java.io.File
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class ProtonImportTest {
    private val context get() = RuntimeEnvironment.getApplication()
    private val tools get() = File(LinuxRuntime.rootDir(context), "root/.local/share/Steam/compatibilitytools.d")
    private val directory = "DDJP-Test-Proton"

    @Before fun prepare() { tools.mkdirs() }
    @After fun clean() { tools.deleteRecursively() }

    private fun imported(marker: String): File {
        val tree = File(tools, directory).apply { mkdirs() }
        File(tree, "toolmanifest.vdf").writeText("manifest")
        File(tree, marker).writeText(JSONObject().put("directory", directory).put("name", "Test-Proton").toString())
        return tree
    }

    @Test fun legacyImportRemainsVisibleAfterBrandingRename() {
        imported(".droiddeck-jp-import.json")
        val rows = ProtonImport.installed(context)
        assertEquals(1, rows.size)
        assertEquals("local:$directory", rows.single().id)
        assertEquals("Test-Proton", rows.single().name)
    }

    @Test fun newImportAndBothMarkerVersionsProduceOneRow() {
        imported(".droiddeck-patch-import.json")
        assertEquals(1, ProtonImport.installed(context).size)
        imported(".droiddeck-jp-import.json")
        assertEquals(1, ProtonImport.installed(context).size)
    }

    @Test fun malformedNewMarkerDoesNotHideValidLegacyImport() {
        val tree = imported(".droiddeck-jp-import.json")
        File(tree, ".droiddeck-patch-import.json").writeText("broken")
        assertEquals(1, ProtonImport.installed(context).size)
    }

    @Test fun mismatchedMarkerCannotClaimAnUnmanagedDirectory() {
        val tree = imported(".droiddeck-jp-import.json")
        File(tree, ".droiddeck-jp-import.json").writeText(
            JSONObject().put("directory", "DDJP-other").put("name", "Test-Proton").toString())
        assertTrue(ProtonImport.installed(context).isEmpty())
    }
}
