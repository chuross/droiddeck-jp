package com.droiddeck.launcher.session

import android.content.ClipData
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.content.FileProvider
import java.io.File

/** Launch only the Google Quick Share receiver, bypassing vendor/default share handlers. */
object QuickShareLogs {
    private const val PACKAGE = "com.google.android.gms"

    fun intent(context: Context, zip: File): Intent? {
        val uri = FileProvider.getUriForFile(context, context.packageName + ".logs", zip)
        val send = Intent(Intent.ACTION_SEND)
            .setType("application/zip")
            .setPackage(PACKAGE)
            .putExtra(Intent.EXTRA_STREAM, uri)
            .putExtra(Intent.EXTRA_SUBJECT, zip.nameWithoutExtension)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        send.clipData = ClipData.newRawUri(zip.name, uri)
        val candidates = context.packageManager.queryIntentActivities(send, PackageManager.MATCH_DEFAULT_ONLY)
            .mapNotNull { it.activityInfo }
            .filter { it.packageName == PACKAGE && it.exported && it.enabled &&
                it.name.startsWith("com.google.android.gms.nearby.sharing.") }
        val receiver = candidates.firstOrNull { it.name == "com.google.android.gms.nearby.sharing.ShareSheetActivity" }
            ?: candidates.singleOrNull() ?: return null
        return send.setComponent(ComponentName(receiver.packageName, receiver.name))
    }
}
