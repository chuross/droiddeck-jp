package com.droiddeck.launcher.session

import android.content.Context
import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.pm.ResolveInfo
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import java.io.File

@RunWith(RobolectricTestRunner::class)
class QuickShareLogsTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val zip get() = File(context.cacheDir, "share/test.zip").apply {
        parentFile!!.mkdirs()
        writeText("test")
    }

    private fun receiver(name: String, packageName: String = "com.google.android.gms", exported: Boolean = true) {
        val query = Intent(Intent.ACTION_SEND).setType("application/zip").setPackage("com.google.android.gms")
        shadowOf(context.packageManager).addResolveInfoForIntent(query, ResolveInfo().apply {
            activityInfo = ActivityInfo().apply {
                this.name = name
                this.packageName = packageName
                this.exported = exported
                enabled = true
            }
        })
    }

    @Test fun quickShareReceivesZipAndReadPermissionWithoutChooser() {
        receiver("com.google.android.gms.nearby.sharing.ShareSheetActivity")
        val intent = QuickShareLogs.intent(context, zip)!!
        assertEquals(Intent.ACTION_SEND, intent.action)
        assertEquals("application/zip", intent.type)
        assertEquals("com.google.android.gms.nearby.sharing.ShareSheetActivity", intent.component!!.className)
        assertEquals("com.google.android.gms", intent.component!!.packageName)
        val uri = intent.clipData!!.getItemAt(0).uri
        assertEquals("content", uri.scheme)
        assertEquals(context.packageName + ".logs", uri.authority)
        @Suppress("DEPRECATION")
        assertEquals(uri, intent.getParcelableExtra<android.net.Uri>(Intent.EXTRA_STREAM))
        assertTrue(intent.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION != 0)
    }

    @Test fun bluetoothAndUnrelatedGoogleReceiversAreNeverSelected() {
        receiver("com.android.bluetooth.opp.BluetoothOppLauncherActivity", "com.android.bluetooth")
        receiver("com.google.android.gms.unrelated.ShareActivity")
        assertNull(QuickShareLogs.intent(context, zip))
    }

    @Test fun nonExportedQuickShareIsUnavailable() {
        receiver("com.google.android.gms.nearby.sharing.ShareSheetActivity", exported = false)
        assertNull(QuickShareLogs.intent(context, zip))
    }

    @Test fun missingQuickShareIsUnavailable() {
        assertNull(QuickShareLogs.intent(context, zip))
    }
}
