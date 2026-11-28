package com.nathanneurotic.ps2servers.net

import com.nathanneurotic.ps2servers.config.LauncherConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NetworkAddressHelperTest {
    @Test
    fun prioritizesUsbEthernetThenHotspotThenWifiAndExcludesCellular() {
        val candidates = listOf(
            NetworkAddressCandidate("rmnet_data0", "rmnet_data0", "10.120.1.5", InterfaceKind.OTHER),
            NetworkAddressCandidate("lo", "lo", "127.0.0.1", InterfaceKind.OTHER),
            NetworkAddressCandidate("wlan0", "wlan0", "192.168.1.44", InterfaceKind.WIFI),
            NetworkAddressCandidate("ap0", "ap0", "192.168.43.1", InterfaceKind.HOTSPOT),
            NetworkAddressCandidate("rndis0", "rndis0", "192.168.42.129", InterfaceKind.USB_ETHERNET)
        )

        val ranked = NetworkAddressHelper.rankCandidates(candidates)
        assertEquals(listOf("rndis0", "ap0", "wlan0"), ranked.map { it.interfaceName })
        assertFalse(NetworkAddressHelper.isPrivateOrLinkLocalLan("8.8.8.8"))
        assertTrue(NetworkAddressHelper.isPrivateOrLinkLocalLan("192.168.137.1"))
    }

    @Test
    fun oplHintReflectsPortAndRedirectState() {
        val settings = LauncherConfig.defaultServerSettings("smbv3")
        val normal = OplHintFormatter.formatHint("smbv3", "192.168.42.129", settings, 1445)
        assertTrue(normal.contains("192.168.42.129"))
        assertTrue(normal.contains("Port 1445"))
        assertTrue(normal.contains("User 'ps2'"))

        settings["port_445_redirect"] = true
        val redirected = OplHintFormatter.formatHint("smbv3", "192.168.42.129", settings, 1445)
        assertTrue(redirected.contains("445 (-> 1445)"))
        assertTrue(redirected.contains("Neutrino bsd-smb ready"))
    }
}
