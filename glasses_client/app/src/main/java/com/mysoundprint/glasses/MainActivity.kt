package com.mysoundprint.glasses

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/**
 * MySoundprint glasses display. A thin client: it connects to the laptop's WebSocket
 * (ws://127.0.0.1:8765/ws through `adb reverse`), draws white text on black, and nothing else.
 * Black is see-through on the glasses. It only reads "alert" and "clear" messages and never
 * receives audio. No blink, no animation.
 *
 * Local test without the laptop (see glasses_client/README.md):
 *   adb shell am start -n com.mysoundprint.glasses/.MainActivity --es line1 HELLO
 *   adb shell am start -n com.mysoundprint.glasses/.MainActivity --es line1 DOORBELL
 *   adb shell am start -n com.mysoundprint.glasses/.MainActivity --ez clear true
 */
class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private val model = AlertModel()
    private val client = OkHttpClient.Builder().pingInterval(10, TimeUnit.SECONDS).build()
    private var socket: WebSocket? = null
    private var generation = 0 // an old socket's late callbacks are ignored
    private var url = "ws://127.0.0.1:8765/ws"
    private var started = false

    private lateinit var line1View: TextView
    private lateinit var line2View: TextView
    private lateinit var statusView: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.BLACK)
        }
        line1View = label(56f, true)
        line2View = label(32f, false)
        statusView = label(14f, false).apply { setTextColor(Color.GRAY) }
        root.addView(line1View)
        root.addView(line2View)
        root.addView(statusView)
        setContentView(root)
        handleLocal(intent)
        render()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleLocal(intent)
        render()
    }

    override fun onStart() {
        super.onStart()
        started = true
        connect()
    }

    override fun onStop() {
        started = false
        generation++
        socket?.cancel()
        socket = null
        super.onStop()
    }

    private fun label(sizeSp: Float, bold: Boolean) = TextView(this).apply {
        setTextColor(Color.WHITE)
        textSize = sizeSp
        gravity = Gravity.CENTER
        if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD)
    }

    /** adb extras for a first test with no laptop. They go through the same model. */
    private fun handleLocal(i: Intent?) {
        i ?: return
        i.getStringExtra("url")?.let { url = it }
        if (i.getBooleanExtra("clear", false)) model.clear(null)
        i.getStringExtra("line1")?.let { model.show(i.getStringExtra("id"), it, i.getStringExtra("line2")) }
    }

    private fun render() {
        val s = model.shown
        line1View.text = s?.line1 ?: ""
        line2View.text = s?.line2 ?: ""
        line2View.visibility = if (s?.line2 == null) View.GONE else View.VISIBLE
    }

    private fun status(text: String, clearAfterMs: Long) {
        statusView.text = text
        if (clearAfterMs > 0) {
            val shownText = text
            ui.postDelayed({ if (statusView.text == shownText) statusView.text = "" }, clearAfterMs)
        }
    }

    private fun connect() {
        if (!started) return
        val mine = ++generation
        socket = client.newWebSocket(
            Request.Builder().url(url).build(),
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    ui.post {
                        if (mine != generation) return@post
                        model.reset() // after a (re)connect the screen state is unknown: start empty
                        render()
                        status("MySoundprint connected", 3000)
                    }
                }

                // OkHttp calls this on one thread, in order; posting keeps that order on screen.
                override fun onMessage(webSocket: WebSocket, text: String) {
                    ui.post { if (mine == generation && model.apply(text)) render() }
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost(mine)

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = lost(mine)
            },
        )
    }

    private fun lost(mine: Int) {
        ui.post {
            if (mine != generation) return@post // already handled, or the screen was stopped
            generation++ // ignore any further callback of this socket
            model.reset() // never leave an old alert on screen after the link drops
            render()
            status("no link", 0)
            ui.postDelayed({ connect() }, 1000)
        }
    }
}
