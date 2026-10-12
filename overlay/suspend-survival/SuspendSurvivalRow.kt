package com.droiddeck.launcher.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import com.droiddeck.launcher.R
import com.droiddeck.launcher.session.SessionPrefs

@Composable
fun SuspendSurvivalRow() {
    val context = LocalContext.current
    var enabled by remember { mutableStateOf(SessionPrefs.keepCpuAwakeWhileSuspended(context)) }
    SettingsRow(stringResource(R.string.jp_suspend_keepalive), stringResource(R.string.jp_suspend_keepalive_hint)) {
        ToggleSwitch(enabled, label = stringResource(R.string.jp_suspend_keepalive)) { on ->
            SessionPrefs.setKeepCpuAwakeWhileSuspended(context, on)
            enabled = on
        }
    }
}
