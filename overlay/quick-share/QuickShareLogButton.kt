package com.droiddeck.launcher.ui

import android.widget.Toast
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.droiddeck.launcher.R
import com.droiddeck.launcher.session.QuickShareLogs
import com.droiddeck.launcher.session.SessionLogShare
import com.droiddeck.launcher.session.SessionPaths

@Composable
fun QuickShareLogButton(
    currentSession: Boolean = false,
    modifier: Modifier = Modifier,
    beforeShare: () -> Unit = {},
) {
    val context = LocalContext.current
    SecondaryButton("Quick Share", modifier = modifier, progress = SessionLogShare.progress) {
        beforeShare()
        SessionLogShare.prepare(context, {
            if (currentSession) SessionPaths.current() ?: SessionPaths.lastEnded()
            else SessionLogShare.latest(context)
        }) { zip ->
            if (zip == null) {
                PatchToast.makeText(context, R.string.session_logs_archive_failed, Toast.LENGTH_LONG).show()
            } else {
                val launched = runCatching {
                    val intent = QuickShareLogs.intent(context, zip) ?: return@runCatching false
                    context.startActivity(intent)
                    true
                }.getOrDefault(false)
                if (!launched) PatchToast.makeText(context, R.string.quick_share_unavailable, Toast.LENGTH_LONG).show()
            }
        }
    }
}
