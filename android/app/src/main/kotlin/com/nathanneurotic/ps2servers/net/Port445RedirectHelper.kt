package com.nathanneurotic.ps2servers.net

import java.io.IOException

data class ShellResult(
    val exitCode: Int,
    val stdout: String,
    val stderr: String
)

interface ShellRunner {
    fun run(command: List<String>): ShellResult
}

object ProcessShellRunner : ShellRunner {
    override fun run(command: List<String>): ShellResult {
        return try {
            val proc = ProcessBuilder(command).start()
            val stdout = proc.inputStream.bufferedReader(Charsets.UTF_8).readText()
            val stderr = proc.errorStream.bufferedReader(Charsets.UTF_8).readText()
            val code = proc.waitFor()
            ShellResult(code, stdout.trim(), stderr.trim())
        } catch (e: IOException) {
            ShellResult(-1, "", e.message ?: "command failed")
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            ShellResult(-1, "", "interrupted")
        }
    }
}

sealed class PortBindCheck {
    object Allowed : PortBindCheck()
    data class PrivilegedPortRefused(val port: Int, val message: String) : PortBindCheck()
}

data class RedirectStatus(
    val active: Boolean,
    val targetPort: Int,
    val message: String
)

/**
 * Manages optional root iptables PREROUTING redirection from privileged TCP 445
 * to the app's unprivileged SMB listener port (e.g. 1445 or 1025).
 *
 * Keeping the server bound above 1024 inside the Android app preserves Storage
 * Access Framework (SAF) URI permissions while letting clients hardcoded to
 * TCP 445 (such as Neutrino's bsd-smb driver) connect on rooted devices.
 */
class Port445RedirectHelper(
    private val runner: ShellRunner = ProcessShellRunner,
    private val isRootCheck: (() -> Boolean)? = null
) {
    fun checkPortBindability(port: Int, redirect445Enabled: Boolean): PortBindCheck {
        if (port !in 1..65535) {
            return PortBindCheck.PrivilegedPortRefused(
                port,
                "Port $port is out of valid range (1-65535)."
            )
        }
        if (port < 1024 && !redirect445Enabled) {
            return PortBindCheck.PrivilegedPortRefused(
                port,
                "Android blocks unprivileged apps from binding TCP/UDP ports below 1024 (port $port). " +
                    "Use the default custom port (1025 for SMBv1, 1445 for SMBv2/3) in OPL/RiptOPL/Launchstation, " +
                    "or enable 'Root port 445 redirect' on a rooted device."
            )
        }
        return PortBindCheck.Allowed
    }

    fun hasRootAccess(): Boolean {
        if (isRootCheck != null) return isRootCheck.invoke()
        val res = runner.run(listOf("su", "-c", "id -u"))
        return res.exitCode == 0 && res.stdout.trim() == "0"
    }

    fun applyRedirect(targetPort: Int): RedirectStatus {
        require(targetPort in 1024..65535) {
            "Redirect target port must be unprivileged (1024-65535), got $targetPort"
        }
        if (!hasRootAccess()) {
            return RedirectStatus(
                active = false,
                targetPort = targetPort,
                message = "Port 445 redirect requires root access (su not available). " +
                    "Use custom port $targetPort in your PS2 client instead."
            )
        }
        val checkCmd = "iptables -t nat -C PREROUTING -p tcp --dport 445 -j REDIRECT --to-ports $targetPort"
        val checkRes = runner.run(listOf("su", "-c", checkCmd))
        if (checkRes.exitCode == 0) {
            return RedirectStatus(
                active = true,
                targetPort = targetPort,
                message = "Root iptables redirect active: TCP 445 -> $targetPort"
            )
        }
        val addCmd = "iptables -t nat -A PREROUTING -p tcp --dport 445 -j REDIRECT --to-ports $targetPort"
        val addRes = runner.run(listOf("su", "-c", addCmd))
        if (addRes.exitCode != 0) {
            val detail = addRes.stderr.ifBlank { addRes.stdout }.ifBlank { "exit ${addRes.exitCode}" }
            return RedirectStatus(
                active = false,
                targetPort = targetPort,
                message = "Failed to add iptables port 445 redirect: $detail"
            )
        }
        return RedirectStatus(
            active = true,
            targetPort = targetPort,
            message = "Root iptables redirect active: TCP 445 -> $targetPort"
        )
    }

    fun removeRedirect(targetPort: Int): RedirectStatus {
        if (targetPort !in 1024..65535) {
            return RedirectStatus(false, targetPort, "No redirect active for port $targetPort")
        }
        if (!hasRootAccess()) {
            return RedirectStatus(false, targetPort, "Root unavailable; skipping iptables cleanup")
        }
        val delCmd = "iptables -t nat -D PREROUTING -p tcp --dport 445 -j REDIRECT --to-ports $targetPort"
        val res = runner.run(listOf("su", "-c", delCmd))
        return if (res.exitCode == 0) {
            RedirectStatus(false, targetPort, "Removed iptables redirect TCP 445 -> $targetPort")
        } else {
            val detail = res.stderr.ifBlank { res.stdout }
            RedirectStatus(false, targetPort, "iptables cleanup returned ${res.exitCode}: $detail")
        }
    }
}
