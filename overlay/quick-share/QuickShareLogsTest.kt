package com.droiddeck.launcher.session

import android.app.Application
import android.content.Context
import android.content.Intent
import android.content.ComponentName
import android.content.pm.ActivityInfo
import android.content.pm.ResolveInfo
import android.provider.Settings
import androidx.test.core.app.ApplicationProvider
import androidx.core.content.FileProvider
import org.junit.Assert.*
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], application = Application::class)
class QuickShareLogsTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val zip get() = File(context.cacheDir, "share/test.zip").apply {
        parentFile!!.mkdirs()
        writeText("test")
    }

    // AndroidX keeps this process-wide cache while Robolectric gives each test a new cacheDir.
    // Preserve the real manifest/provider checks, but never reuse another test's root.
    @Before @After
    fun resetProviderPaths() {
        val field = FileProvider::class.java.getDeclaredField("sCache").apply { isAccessible = true }
        (field.get(null) as MutableMap<*, *>).clear()
        Settings.Secure.putString(context.contentResolver, "nearby_sharing_component", null)
    }

    private fun explicitReceiver(name: String, exported: Boolean = true, enabled: Boolean = true) {
        shadowOf(context.packageManager).addOrUpdateActivity(ActivityInfo().apply {
            this.name = name
            packageName = "com.google.android.gms"
            this.exported = exported
            this.enabled = enabled
        })
    }

    @Test fun sharesheetReceiverWithoutSendIntentFilterIsUsedDirectly() {
        val name = "com.google.android.gms.nearby.sharing.ShareSheetActivity"
        explicitReceiver(name)
        assertTrue(context.packageManager.queryIntentActivities(
            Intent(Intent.ACTION_SEND).setType("application/zip").setPackage("com.google.android.gms"), 0).isEmpty())
        assertEquals(name, QuickShareLogs.intent(context, zip)!!.component!!.className)
    }

    @Test fun deviceConfiguredReceiverAliasIsPreferred() {
        val name = "com.google.android.gms.quickshare.DeviceShareAlias"
        explicitReceiver(name)
        explicitReceiver("com.google.android.gms.nearby.sharing.ShareSheetActivity")
        Settings.Secure.putString(context.contentResolver, "nearby_sharing_component",
            ComponentName("com.google.android.gms", name).flattenToString())
        assertEquals(name, QuickShareLogs.intent(context, zip)!!.component!!.className)
    }

    @Test fun disabledExplicitReceiverIsUnavailable() {
        explicitReceiver("com.google.android.gms.nearby.sharing.ShareSheetActivity", enabled = false)
        assertNull(QuickShareLogs.intent(context, zip))
    }

    @Test fun privateExplicitReceiverIsUnavailable() {
        explicitReceiver("com.google.android.gms.nearby.sharing.ShareSheetActivity", exported = false)
        assertNull(QuickShareLogs.intent(context, zip))
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
