package com.nathanneurotic.ps2servers.config

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class LauncherConfigTest {
    @Rule
    @JvmField
    val tmp = TemporaryFolder()

    @Test
    fun defaultsMatchDesktopRegistry() {
        val cfg = LauncherConfig.defaultConfig()
        val smbv1 = LauncherConfig.serverMap(cfg, "smbv1")
        assertEquals(1025, LauncherConfig.asInt(smbv1["port"], 0))
        assertEquals("games", smbv1["share_name"])
        assertEquals("guest", smbv1["username"])
        assertEquals("", smbv1["password"])
        assertFalse(LauncherConfig.asBool(smbv1["read_only"], true))

        val smbv2 = LauncherConfig.serverMap(cfg, "smbv2")
        assertEquals(1445, LauncherConfig.asInt(smbv2["port"], 0))
        assertEquals("ps2", smbv2["username"])
        assertFalse(LauncherConfig.asBool(smbv2["open_share"], true))

        val udpfs = LauncherConfig.serverMap(cfg, "udpfs")
        assertEquals(62966, LauncherConfig.asInt(udpfs["port"], 0))
        assertEquals(0, LauncherConfig.asInt(udpfs["data_port"], -1))
        assertEquals("Auto (recommended)", udpfs["protocol_mode"])
        assertTrue(LauncherConfig.asBool(udpfs["enable_compression"], false))
        assertEquals("3600", udpfs["peer_timeout"])

        val http = LauncherConfig.serverMap(cfg, "http")
        assertEquals(1100, LauncherConfig.asInt(http["port"], 0))
        assertEquals("Legacy flat (OPL-HTTP)", http["catalog_profile"])

        val nfs = LauncherConfig.serverMap(cfg, "nfs")
        assertFalse(LauncherConfig.asBool(nfs["allow_writes"], true))

        val udpbd = LauncherConfig.serverMap(cfg, "udpbd")
        assertFalse(LauncherConfig.asBool(udpbd["read_only"], true))
    }

    @Test
    fun roundTripSaveLoadPreservesUnknownDesktopKeysAndMigratesLegacyModulo() {
        val file = File(tmp.root, "launcher.json")
        val rawDesktop = """
            {
              "servers": {
                "udpfs": {
                  "root_dir": "/srv/ps2",
                  "modulo_mode": true,
                  "verbose": false
                },
                "retroachievements": {
                  "mode": "xerabora"
                }
              },
              "ip": "192.168.1.50",
              "game_library_folder": "/srv/ps2",
              "last_active_servers": ["udpfs", "smbv1"]
            }
        """.trimIndent()
        file.writeText(rawDesktop, Charsets.UTF_8)

        val loaded = LauncherConfig.load(file)
        assertEquals("192.168.1.50", loaded["ip"])
        assertEquals("/srv/ps2", loaded["game_library_folder"])
        assertEquals(listOf("udpfs", "smbv1"), loaded["last_active_servers"])

        val udpfs = LauncherConfig.serverMap(loaded, "udpfs")
        assertTrue(LauncherConfig.asBool(udpfs["enforce_modulo"], false))
        assertEquals("Modulo", udpfs["protocol_mode"])
        assertFalse(udpfs.containsKey("modulo_mode"))
        // One-time verbose migration applies because verbose_default_on was absent.
        assertTrue(LauncherConfig.asBool(udpfs["verbose"], false))

        // After migration marker is set, turning verbose off persists across save/load.
        udpfs["verbose"] = false
        LauncherConfig.save(file, loaded)

        val reloaded = LauncherConfig.load(file)
        val reloadedUdpfs = LauncherConfig.serverMap(reloaded, "udpfs")
        assertFalse(LauncherConfig.asBool(reloadedUdpfs["verbose"], true))
        assertEquals("/srv/ps2", reloaded["game_library_folder"])
    }
}
