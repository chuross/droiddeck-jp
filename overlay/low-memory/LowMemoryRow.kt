package com.droiddeck.launcher.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import com.droiddeck.launcher.R
import com.droiddeck.launcher.session.SessionPrefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** One shared session preference, visible in both Steam and Desktop settings. */
@Composable
fun LowMemoryRow() {
    val context = LocalContext.current
    val coroutine = rememberCoroutineScope()
    var enabled by remember { mutableStateOf(SessionPrefs.lowMemoryEnabled(context)) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf(false) }
    SettingsRow(stringResource(R.string.jp_low_memory), stringResource(R.string.jp_low_memory_hint)) {
        ToggleSwitch(enabled, enabled = !busy, label = stringResource(R.string.jp_low_memory)) { on ->
            busy = true
            coroutine.launch {
                val result = runCatching {
                    withContext(Dispatchers.IO) { SessionPrefs.setLowMemoryEnabled(context, on) }
                }
                // The durable preference is authoritative even if publishing fails;
                // SessionFiles republishes it at the next session start.
                enabled = SessionPrefs.lowMemoryEnabled(context)
                error = result.isFailure
                busy = false
            }
        }
    }
    if (error) Small(stringResource(R.string.game_env_error), error = true)
}
