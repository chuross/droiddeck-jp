package com.droiddeck.launcher.session

import android.content.Context
import android.os.Bundle
import android.view.ViewGroup
import androidx.activity.ComponentActivity
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.LocalSaveableStateRegistry
import androidx.compose.runtime.saveable.SaveableStateRegistry
import androidx.compose.ui.platform.ComposeView
import coil.imageLoader

/** Keep saved navigation values, never the stopped home's composition or painters. */
class LowMemoryHome {
    private var saved: Map<String, List<Any?>>? = null
    private var active: SaveableStateRegistry? = null
    private var suspended: ComposeView? = null
    private var host: ViewGroup? = null
    private var launchPending = false

    fun sessionLaunched() { launchPending = true }

    fun attach(activity: ComponentActivity) {
        @Suppress("DEPRECATION", "UNCHECKED_CAST")
        val restored = activity.savedStateRegistry.consumeRestoredStateForKey("low-memory-home")
            ?.getSerializable("values") as? Map<String, List<Any?>>
        saved = restored
        // Activity saving can happen AFTER onStop and composition disposal.
        // Register outside the composition so saved navigation survives that too.
        activity.savedStateRegistry.registerSavedStateProvider("low-memory-home") {
            Bundle().apply {
                (active?.performSave() ?: saved)?.let { putSerializable("values", HashMap(it)) }
            }
        }
    }

    @Composable
    fun Content(content: @Composable () -> Unit) {
        val parent = LocalSaveableStateRegistry.current
        val registry = remember {
            SaveableStateRegistry(saved) { parent?.canBeSaved(it) ?: false }
        }
        CompositionLocalProvider(LocalSaveableStateRegistry provides registry, content = content)
        // Register after descendants, like SaveableStateHolder: disposal must
        // snapshot their savers before those savers unregister themselves.
        DisposableEffect(registry, parent) {
            active = registry
            onDispose {
                saved = registry.performSave()
                active = null
            }
        }
    }

    fun stop(activity: ComponentActivity, sessionRunning: Boolean): Boolean {
        if ((!sessionRunning && !launchPending) || !SessionPrefs.lowMemoryEnabled(activity)) return false
        val root = activity.findViewById<ViewGroup>(android.R.id.content)
        val view = root.getChildAt(0) as? ComposeView ?: return false
        // A state-driven conditional would wait for a frame, but the stopped
        // activity's recomposer is paused. Dispose synchronously instead.
        saved = active?.performSave() ?: saved
        view.disposeComposition()
        // Detach the hidden view too, so Android can discard its hardware
        // display lists instead of retaining the last home's rendered images.
        root.removeView(view)
        host = root
        suspended = view
        activity.imageLoader.memoryCache?.clear()
        android.util.Log.i("LowMemoryUi", "hidden home detached; image cache cleared (24MiB limit)")
        return true
    }

    fun start() {
        launchPending = false
        val view = suspended ?: return
        suspended = null
        host?.addView(view)
        host = null
        if (view.isAttachedToWindow) view.createComposition()
    }
}

/** Switch budgets without replacing the ImageLoader or cancelling live requests. */
class LowMemoryImageCache(context: Context) : coil.memory.MemoryCache {
    private val app = context.applicationContext
    private var mode: Boolean? = null
    private var delegate: coil.memory.MemoryCache? = null

    private fun cache(): coil.memory.MemoryCache {
        val low = SessionPrefs.lowMemoryEnabled(app)
        if (mode != low) {
            delegate?.clear()
            delegate = coil.memory.MemoryCache.Builder(app).apply {
                if (low) maxSizeBytes(24 * 1024 * 1024)
            }.build()
            mode = low
        }
        return checkNotNull(delegate)
    }

    @get:Synchronized override val size: Int get() = cache().size
    @get:Synchronized override val maxSize: Int get() = cache().maxSize
    @get:Synchronized override val keys: Set<coil.memory.MemoryCache.Key> get() = cache().keys
    @Synchronized override fun get(key: coil.memory.MemoryCache.Key) = cache()[key]
    @Synchronized override fun set(key: coil.memory.MemoryCache.Key, value: coil.memory.MemoryCache.Value) {
        cache()[key] = value
    }
    @Synchronized override fun remove(key: coil.memory.MemoryCache.Key) = cache().remove(key)
    @Synchronized override fun clear() = cache().clear()
    @Synchronized override fun trimMemory(level: Int) = cache().trimMemory(level)
}
