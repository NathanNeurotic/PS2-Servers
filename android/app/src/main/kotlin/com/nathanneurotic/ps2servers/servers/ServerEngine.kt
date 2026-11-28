package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.net.Port445RedirectHelper
import com.nathanneurotic.ps2servers.net.PortBindCheck
import java.io.IOException

fun interface LogSink {
    fun log(message: String)
}

interface ServerEngine {
    val key: String
    val displayName: String
    val boundPort: Int
    val isRunning: Boolean

    fun start()
    fun stop()
}

object ServerPortGuard {
    fun ensureBindable(port: Int, redirect445Enabled: Boolean = false) {
        if (port == 0) return // Ephemeral port binding for host tests
        when (val check = Port445RedirectHelper().checkPortBindability(port, redirect445Enabled)) {
            is PortBindCheck.Allowed -> return
            is PortBindCheck.PrivilegedPortRefused -> throw IOException(check.message)
        }
    }
}
