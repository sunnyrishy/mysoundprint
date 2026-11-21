package com.mysoundprint.glasses

import org.json.JSONObject

/** What is on the screen. Plain logic, no views, so it is easy to read and to test. */
data class Shown(val eventId: String?, val line1: String, val line2: String?)

class AlertModel {
    var shown: Shown? = null
        private set

    /** Show replaces whatever is there. Returns true if the screen changed. */
    fun show(eventId: String?, line1: String, line2: String?): Boolean {
        if (line1.isEmpty()) return false
        shown = Shown(eventId, line1, line2)
        return true
    }

    /**
     * A clear only removes the message if it is for the message on screen, or for everything
     * (null id). A clear for an older event never removes a newer message.
     */
    fun clear(eventId: String?): Boolean {
        val current = shown ?: return false
        if (eventId != null && eventId != current.eventId) return false
        shown = null
        return true
    }

    /** Clears everything, for example when the link to the laptop drops. */
    fun reset(): Boolean {
        val had = shown != null
        shown = null
        return had
    }

    /**
     * Reads one MySoundprint WebSocket message. Only "alert" and "clear" matter. Everything else
     * ("event", "stats", "profile") and anything unreadable is ignored. Returns true if the screen
     * changed.
     */
    fun apply(raw: String): Boolean {
        val msg = try { JSONObject(raw) } catch (e: Exception) { return false }
        return when (text(msg, "type")) {
            "alert" -> show(text(msg, "event_id"), text(msg, "line1") ?: "", text(msg, "line2"))
            "clear" -> clear(text(msg, "event_id"))
            else -> false
        }
    }

    // JSONObject.optString turns a JSON null into the text "null", so check isNull first.
    private fun text(msg: JSONObject, key: String): String? =
        if (msg.isNull(key)) null else msg.getString(key).ifEmpty { null }
}
