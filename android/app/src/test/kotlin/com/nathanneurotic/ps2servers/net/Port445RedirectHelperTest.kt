package com.nathanneurotic.ps2servers.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class Port445RedirectHelperTest {
    private class FakeShellRunner(
        private val responses: Map<String, ShellResult>
    ) : ShellRunner {
        val commands = ArrayList<List<String>>()

        override fun run(command: List<String>): ShellResult {
            commands.add(command)
            val full = command.joinToString(" ")
            return responses[full] ?: ShellResult(1, "", "unexpected command: $full")
        }
    }

    @Test
    fun rejectsUnprivilegedPort445WithoutRedirect() {
        val helper = Port445RedirectHelper(FakeShellRunner(emptyMap()))
        val check = helper.checkPortBindability(445, redirect445Enabled = false)
        assertTrue(check is PortBindCheck.PrivilegedPortRefused)
        assertEquals(PortBindCheck.Allowed, helper.checkPortBindability(1445, redirect445Enabled = false))
    }

    @Test
    fun appliesAndRemovesIptablesRedirectWhenRooted() {
        val runner = FakeShellRunner(
            mapOf(
                "su -c id -u" to ShellResult(0, "0", ""),
                "su -c iptables -t nat -C PREROUTING -p tcp --dport 445 -j REDIRECT --to-ports 1445" to ShellResult(1, "", ""),
                "su -c iptables -t nat -A PREROUTING -p tcp --dport 445 -j REDIRECT --to-ports 1445" to ShellResult(0, "", ""),
                "su -c iptables -t nat -D PREROUTING -p tcp --dport 445 -j REDIRECT --to-ports 1445" to ShellResult(0, "", "")
            )
        )
        val helper = Port445RedirectHelper(runner)
        val applied = helper.applyRedirect(1445)
        assertTrue(applied.active)
        assertEquals(1445, applied.targetPort)

        val removed = helper.removeRedirect(1445)
        assertFalse(removed.active)
    }

    @Test
    fun failsCleanlyWhenRootUnavailable() {
        val runner = FakeShellRunner(
            mapOf("su -c id -u" to ShellResult(1, "", "su: not found"))
        )
        val helper = Port445RedirectHelper(runner)
        val applied = helper.applyRedirect(1025)
        assertFalse(applied.active)
        assertTrue(applied.message.contains("requires root access"))
    }
}
