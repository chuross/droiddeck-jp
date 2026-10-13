package com.droiddeck.launcher.session

import android.content.ClipData
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import java.io.File

/** Launch only the Google Quick Share receiver, bypassing vendor/default share handlers. */
object QuickShareLogs {
    private const val PACKAGE = "com.google.android.gms"
    private const val SHARE_SHEET = "com.google.android.gms.nearby.sharing.ShareSheetActivity"

    // The Android sharesheet uses this component explicitly; it need not advertise ACTION_SEND.
    private fun systemComponent(context: Context): ComponentName? {
        val configured = runCatching {
            Settings.Secure.getString(context.contentResolver, "nearby_sharing_component")
        }.getOrNull()
        val value = configured?.takeIf { it.isNotBlank() } ?: runCatching {
            val id = context.resources.getIdentifier("config_defaultNearbySharingComponent", "string", "android")
            if (id == 0) null else context.getString(id)
        }.getOrNull()
        return value?.let(ComponentName::unflattenFromString)?.takeIf { it.packageName == PACKAGE }
    }

    fun intent(context: Context, zip: File): Intent? {
        val uri = FileProvider.getUriForFile(context, context.packageName + ".logs", zip)
        val send = Intent(Intent.ACTION_SEND)
            .setType("application/zip")
            .setPackage(PACKAGE)
            .putExtra(Intent.EXTRA_STREAM, uri)
            .putExtra(Intent.EXTRA_SUBJECT, zip.nameWithoutExtension)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        send.clipData = ClipData.newRawUri(zip.name, uri)
        val manager = context.packageManager
        val explicit = listOfNotNull(systemComponent(context), ComponentName(PACKAGE, SHARE_SHEET)).distinct()
        for (component in explicit) {
            val target = Intent(send).setComponent(component)
            val activity = manager.resolveActivity(target, 0)?.activityInfo
            if (activity != null && activity.packageName == PACKAGE && activity.exported && activity.enabled) {
                return target
            }
        }
        val candidates = manager.queryIntentActivities(send, PackageManager.MATCH_DEFAULT_ONLY)
            .mapNotNull { it.activityInfo }
            .filter { it.packageName == PACKAGE && it.exported && it.enabled &&
                it.name.startsWith("com.google.android.gms.nearby.sharing.") }
        val receiver = candidates.firstOrNull { it.name == SHARE_SHEET }
            ?: candidates.singleOrNull()
        if (receiver == null) {
            Log.w("QuickShareLogs", "No Quick Share receiver; explicit candidates=" +
                explicit.joinToString { it.flattenToShortString() } + "; share candidates=" +
                candidates.joinToString { it.name })
            return null
        }
        return send.setComponent(ComponentName(receiver.packageName, receiver.name))
    }
}
