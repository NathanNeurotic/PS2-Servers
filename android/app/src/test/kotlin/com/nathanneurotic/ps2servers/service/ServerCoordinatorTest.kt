package com.nathanneurotic.ps2servers.service

import com.nathanneurotic.ps2servers.config.LauncherConfig
import com.nathanneurotic.ps2servers.net.Port445RedirectHelper
import com.nathanneurotic.ps2servers.net.ShellResult
import com.nathanneurotic.ps2servers.net.ShellRunner
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class ServerCoordinatorTest {
    @Rule
    @JvmField
    val tempFolder = TemporaryFolder()

    @Test
    fun startStopLifecycleAndPersistenceAcrossAllFolderBackedServers() {
        val gamesDir = tempFolder.newFolder("games")
        File(gamesDir, "DVD").mkdirs()
        File(gamesDir, "DVD/TEST.ISO").writeBytes(ByteArray(2048))

        val configFile = File(tempFolder.root, "launcher.json")
        val coordinator = ServerCoordinator(
            configFile = configFile,
            safBridge = null,
            redirectHelper = Port445RedirectHelper(object : ShellRunner {
                override fun run(command: List<String>): ShellResult =
                    ShellResult(1, "", "not rooted: ${command.joinToString(" ")}")
            })
        )

        coordinator.updateTopLevel("android_fallback_path", gamesDir.absolutePath)
        coordinator.updateServerSetting("smbv2", "open_share", true)
        coordinator.updateServerSetting("smbv3", "password", "ps2pass")
        for (key in listOf("udpfs", "smbv1", "smbv2", "smbv3", "http", "nfs", "udpbd")) {
            coordinator.updateServerSetting(key, "port", 0)
        }

        for (key in listOf("udpfs", "smbv1", "smbv2", "smbv3", "http", "nfs", "udpbd")) {
            val status = coordinator.startServer(key)
            assertTrue("$key should report running", status.running)
            assertTrue("$key should have bound ephemeral port > 0", (status.boundPort ?: 0) > 0)
        }

        assertTrue(coordinator.isAnyRunning())
        assertTrue(coordinator.needsMulticastLock())

        val reloaded = LauncherConfig.load(configFile)
        val savedActive = (reloaded["last_active_servers"] as List<*>).map { it.toString() }.toSet()
        assertEquals(setOf("udpfs", "smbv1", "smbv2", "smbv3", "http", "nfs", "udpbd"), savedActive)

        coordinator.stopServer("udpfs")
        assertTrue(coordinator.needsMulticastLock())
        coordinator.stopServer("udpbd")
        assertFalse(coordinator.needsMulticastLock())

        coordinator.stopAll()
        assertFalse(coordinator.isAnyRunning())
        assertTrue(coordinator.statuses().none { it.running })
    }

    @Test(expected = IllegalStateException::class)
    fun privilegedPortWithoutRootIsRejectedWithClearError() {
        val gamesDir = tempFolder.newFolder("games-priv")
        val configFile = File(tempFolder.root, "launcher.json")
        val coordinator = ServerCoordinator(
            configFile = configFile,
            safBridge = null,
            redirectHelper = Port445RedirectHelper(object : ShellRunner {
                override fun run(command: List<String>): ShellResult =
                    ShellResult(1, "", "su denied")
            })
        )
        coordinator.updateTopLevel("android_fallback_path", gamesDir.absolutePath)
        coordinator.updateServerSetting("smbv2", "port", 445)
        coordinator.startServer("smbv2")
    }

    @Test
    fun root445RedirectAppliesOnStartAndCleansUpOnStop() {
        val gamesDir = tempFolder.newFolder("games-root")
        val configFile = File(tempFolder.root, "launcher.json")
        val executedCommands = mutableListOf<String>()
        val coordinator = ServerCoordinator(
            configFile = configFile,
            safBridge = null,
            redirectHelper = Port445RedirectHelper(object : ShellRunner {
                override fun run(command: List<String>): ShellResult {
                    val joined = command.joinToString(" ")
                    executedCommands.add(joined)
                    return when {
                        joined.contains("id -u") -> ShellResult(0, "0\n", "")
                        joined.contains("iptables -t nat -C") -> ShellResult(1, "", "")
                        else -> ShellResult(0, "", "")
                    }
                }
            })
        )
        coordinator.updateTopLevel("android_fallback_path", gamesDir.absolutePath)
        coordinator.updateServerSetting("smbv3", "open_share", true)
        coordinator.updateServerSetting("smbv3", "port", 0)
        coordinator.updateServerSetting("smbv3", "port_445_redirect", true)

        val status = coordinator.startServer("smbv3")
        assertTrue(status.redirect445Active)
        val bound = status.boundPort!!
        assertTrue(executedCommands.any { it.contains("iptables -t nat -A PREROUTING -p tcp --dport 445 -j REDIRECT --to-ports $bound") })

        coordinator.stopServer("smbv3")
        assertTrue(executedCommands.any { it.contains("iptables -t nat -D PREROUTING -p tcp --dport 445 -j REDIRECT --to-ports $bound") })
    }
}
