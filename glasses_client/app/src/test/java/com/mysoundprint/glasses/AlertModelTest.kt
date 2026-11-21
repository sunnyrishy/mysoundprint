package com.mysoundprint.glasses

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Runs on the computer, not on the glasses. It checks the message rules, nothing about the lens. */
class AlertModelTest {
    private fun alert(id: String?, l1: String, l2: String? = null): String {
        val idPart = if (id == null) "null" else "\"$id\""
        val l2Part = if (l2 == null) "null" else "\"$l2\""
        return """{"type":"alert","event_id":$idPart,"line1":"$l1","line2":$l2Part,"tier":1,"blink":true,"ttl_ms":3000}"""
    }

    @Test
    fun alertShowsAndAnotherAlertReplacesIt() {
        val m = AlertModel()
        assertTrue(m.apply(alert("e1", "HELLO")))
        assertEquals("HELLO", m.shown?.line1)
        assertTrue(m.apply(alert("e2", "DOORBELL")))
        assertEquals("DOORBELL", m.shown?.line1)
        assertEquals("e2", m.shown?.eventId)
    }

    @Test
    fun clearForTheShownEventClearsIt() {
        val m = AlertModel()
        m.apply(alert("e1", "HELLO"))
        assertTrue(m.apply("""{"type":"clear","event_id":"e1"}"""))
        assertNull(m.shown)
    }

    @Test
    fun clearForAnOlderEventNeverRemovesANewerMessage() {
        val m = AlertModel()
        m.apply(alert("e1", "HELLO"))
        m.apply(alert("e2", "DOORBELL"))
        assertFalse(m.apply("""{"type":"clear","event_id":"e1"}"""))
        assertEquals("DOORBELL", m.shown?.line1)
    }

    @Test
    fun clearWithNoEventIdClearsEverything() {
        val m = AlertModel()
        m.apply(alert("e1", "HELLO"))
        assertTrue(m.apply("""{"type":"clear","event_id":null}"""))
        assertNull(m.shown)
        assertFalse(m.apply("""{"type":"clear","event_id":null}""")) // nothing left to clear
    }

    @Test
    fun jsonNullIsNotTheTextNull() {
        val m = AlertModel()
        m.apply(alert(null, "HELLO", null))
        assertNull(m.shown?.eventId)
        assertNull(m.shown?.line2)
    }

    @Test
    fun secondLineIsKept() {
        val m = AlertModel()
        m.apply(alert("e1", "FIRE ALARM", "NEARBY"))
        assertEquals("NEARBY", m.shown?.line2)
    }

    @Test
    fun everythingElseIsIgnored() {
        val m = AlertModel()
        m.apply(alert("e1", "HELLO"))
        for (raw in listOf(
            """{"type":"event","event":{"id":"e9"}}""",
            """{"type":"stats","events_total":3}""",
            """{"type":"profile","active":"home"}""",
            "not json",
            "",
            """{"type":"alert","event_id":"e5","line1":""}""",
        )) {
            assertFalse(raw, m.apply(raw))
        }
        assertEquals("HELLO", m.shown?.line1)
    }

    @Test
    fun resetEmptiesTheScreenOnlyWhenSomethingWasShown() {
        val m = AlertModel()
        assertFalse(m.reset())
        m.apply(alert("e1", "HELLO"))
        assertTrue(m.reset())
        assertNull(m.shown)
    }
}
