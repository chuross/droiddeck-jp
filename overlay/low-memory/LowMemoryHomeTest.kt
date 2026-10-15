package com.droiddeck.launcher.session

import android.content.Context
import android.app.Application
import android.graphics.Bitmap
import android.os.Bundle
import android.os.Looper
import android.os.Parcel
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.test.core.app.ApplicationProvider
import coil.memory.MemoryCache
import org.junit.Assert.*
import org.junit.Test
import org.junit.After
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Robolectric
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], application = Application::class)
class LowMemoryHomeTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private fun mode(on: Boolean) {
        context.getSharedPreferences("session", Context.MODE_PRIVATE).edit()
            .putBoolean("jpLowMemory", on).commit()
    }

    @After fun drainDestroyedActivityCallbacks() {
        // Lifecycle destruction cancels the window recomposer asynchronously.
        // Drain cancellation before Robolectric replaces the main looper, or
        // subsequent Compose tests can observe a stranded running recomposer.
        frames()
    }

    @Test fun imageBudgetBoundsRetainedBitmapsAndRestoresDefaultWithoutReplacingLoader() {
        mode(false)
        val cache = LowMemoryImageCache(context)
        val normal = MemoryCache.Builder(context).build().maxSize
        assertEquals(normal, cache.maxSize)
        mode(true)
        assertEquals(24 * 1024 * 1024, cache.maxSize)
        repeat(40) { index ->
            cache[MemoryCache.Key("cover-$index")] = MemoryCache.Value(
                Bitmap.createBitmap(512, 512, Bitmap.Config.ARGB_8888))
        }
        assertTrue(cache.size > 0)
        assertTrue(cache.size <= 24 * 1024 * 1024)
        mode(false)
        assertEquals(normal, cache.maxSize)
        assertTrue(cache.keys.isEmpty())
        mode(true)
        assertEquals(24 * 1024 * 1024, cache.maxSize)
        cache[MemoryCache.Key("cover")] = MemoryCache.Value(
            Bitmap.createBitmap(16, 16, Bitmap.Config.ARGB_8888))
        cache.clear()
        assertTrue(cache.keys.isEmpty())
    }

    private fun frames() = shadowOf(Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(100))

    @Test fun stoppedHomeDisposesImmediatelyAndRestoresSavedNavigation() {
        mode(true)
        val controller = Robolectric.buildActivity(MemoryHomeActivity::class.java).setup().visible()
        frames()
        val activity = controller.get()
        assertTrue(activity.composed)
        activity.selection!!.value = "app:2058190"
        frames()
        controller.pause().stop()
        assertFalse(activity.composed)
        controller.start().resume().visible()
        frames()
        assertTrue(activity.composed)
        assertEquals("app:2058190", activity.selection!!.value)
        controller.pause().stop().destroy()
    }

    @Test fun navigationSurvivesSavingAfterStoppedCompositionWasDisposed() {
        mode(true)
        val controller = Robolectric.buildActivity(MemoryHomeActivity::class.java).setup().visible()
        frames()
        controller.get().selection!!.value = "games"
        frames()
        controller.pause().stop()
        val bundle = Bundle()
        controller.saveInstanceState(bundle).destroy()
        // Exercise real Bundle serialization, including Compose's Parcelable state.
        val parcel = Parcel.obtain()
        parcel.writeBundle(bundle)
        parcel.setDataPosition(0)
        val serialized = checkNotNull(parcel.readBundle(javaClass.classLoader))
        parcel.recycle()
        val restored = Robolectric.buildActivity(MemoryHomeActivity::class.java).setup(serialized).visible()
        frames()
        assertEquals("games", restored.get().selection!!.value)
        restored.pause().stop().destroy()
    }

    @Test fun normalModeKeepsHomeComposition() {
        mode(false)
        val controller = Robolectric.buildActivity(MemoryHomeActivity::class.java).setup().visible()
        frames()
        controller.pause().stop()
        assertTrue(controller.get().composed)
        controller.destroy()
    }

    @Test fun lowMemoryWithoutRunningSessionKeepsHomeComposition() {
        mode(true)
        val controller = Robolectric.buildActivity(MemoryHomeActivity::class.java).setup().visible()
        controller.get().sessionRunning = false
        frames()
        controller.pause().stop()
        assertTrue(controller.get().composed)
        controller.destroy()
    }

    @Test fun launchingSessionReleasesHomeBeforeGuestServiceHasStarted() {
        mode(true)
        val controller = Robolectric.buildActivity(MemoryHomeActivity::class.java).setup().visible()
        frames()
        controller.get().sessionRunning = false
        controller.get().markSessionLaunch()
        controller.pause().stop()
        assertFalse(controller.get().composed)
        controller.destroy()
    }
}

class MemoryHomeActivity : ComponentActivity() {
    private val memory = LowMemoryHome()
    var composed = false
    var selection: MutableState<String>? = null
    var sessionRunning = true
    fun markSessionLaunch() { memory.sessionLaunched() }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        memory.attach(this)
        setContent {
            memory.Content {
                val value = rememberSaveable { mutableStateOf("steam") }
                SideEffect { selection = value }
                DisposableEffect(Unit) {
                    composed = true
                    onDispose { composed = false; selection = null }
                }
            }
        }
    }
    override fun onStart() { super.onStart(); memory.start() }
    override fun onStop() { memory.stop(this, sessionRunning); super.onStop() }
}
