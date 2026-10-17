package com.droiddeck.launcher.ui

import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import com.droiddeck.launcher.R
import com.droiddeck.launcher.core.BackgroundAppLimit
import com.droiddeck.launcher.core.WirelessAdbFix

@Composable
internal fun BackgroundMemorySettings(host: MenuHost) {
    val context = LocalContext.current
    val paired = WirelessAdbFix.hasSavedPairing(context)
    var clear by remember { mutableStateOf(BackgroundAppLimit.clearCached(context)) }
    var limit by remember { mutableStateOf(BackgroundAppLimit.limit(context)) }
    SettingsGroup(stringResource(R.string.patch_bg_group)) {
        ToggleRow(host, "patch-bg-clear", stringResource(R.string.patch_bg_clear),
            stringResource(if (paired) R.string.patch_bg_clear_hint else R.string.patch_bg_unpaired),
            clear, enabled = paired || clear) {
            if (!it || paired) { BackgroundAppLimit.setClearCached(context, it); clear = it }
        }
        ChoiceRow(host, "patch-bg-limit", stringResource(R.string.patch_bg_limit),
            stringResource(if (paired) R.string.patch_bg_limit_hint else R.string.patch_bg_unpaired),
            (if (paired) BackgroundAppLimit.CHOICES else listOf(BackgroundAppLimit.OFF)).map {
                it to stringResource(when (it) {
                    4 -> R.string.patch_bg_four
                    1 -> R.string.patch_bg_one
                    0 -> R.string.patch_bg_none
                    else -> R.string.game_env_off
                })
            }, limit, enabled = paired || limit != BackgroundAppLimit.OFF) {
            if (it == BackgroundAppLimit.OFF || paired) { BackgroundAppLimit.setLimit(context, it); limit = it }
        }
    }
}
