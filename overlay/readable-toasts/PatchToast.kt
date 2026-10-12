package com.droiddeck.launcher.ui

import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.view.ContextThemeWrapper
import android.widget.TextView
import android.widget.Toast

/** Foreground launcher messages must remain readable with vendor toast themes. */
object PatchToast {
    fun makeText(context: Context, resource: Int, duration: Int): Toast =
        makeText(context, context.getText(resource), duration)

    @Suppress("DEPRECATION")
    fun makeText(context: Context, message: CharSequence, duration: Int): Toast {
        val themed = ContextThemeWrapper(context, android.R.style.Theme_Material_Light_NoActionBar)
        val density = context.resources.displayMetrics.density
        val label = TextView(themed).apply {
            text = message
            textSize = 16f
            setTextColor(Color.WHITE)
            setPadding((20 * density).toInt(), (12 * density).toInt(),
                (20 * density).toInt(), (12 * density).toInt())
            background = GradientDrawable().apply {
                setColor(Color.rgb(48, 48, 48))
                cornerRadius = 12 * density
            }
            if (Build.VERSION.SDK_INT >= 29) isForceDarkAllowed = false
        }
        return Toast(themed).apply {
            this.duration = duration
            view = label
        }
    }
}
