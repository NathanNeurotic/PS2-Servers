package com.nathanneurotic.ps2servers.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MobileInternetHelperTest {
    @Test
    fun cidrMaskAndPs2StaticIpSuggestionAvoidCollisions() {
        assertEquals("255.255.255.0", MobileInternetHelper.prefixLengthToMask(24))
        assertEquals("255.255.0.0", MobileInternetHelper.prefixLengthToMask(16))
        assertEquals("255.255.255.128", MobileInternetHelper.prefixLengthToMask(25))

        assertEquals(
            "192.168.42.50",
            MobileInternetHelper.suggestPs2StaticIp("192.168.42.129", 24)
        )
        assertEquals(
            "192.168.42.51",
            MobileInternetHelper.suggestPs2StaticIp("192.168.42.50", 24)
        )
    }

    @Test
    fun validatorsRejectShellInjectionAndMalformedIps() {
        assertTrue(MobileInternetHelper.isValidInterfaceName("eth0"))
        assertTrue(MobileInternetHelper.isValidInterfaceName("rmnet_data0"))
        assertFalse(MobileInternetHelper.isValidInterfaceName("eth0; id"))
        assertFalse(MobileInternetHelper.isValidInterfaceName(""))

        assertTrue(MobileInternetHelper.isValidIpv4("45.7.228.197"))
        assertFalse(MobileInternetHelper.isValidIpv4("45.7.228.999"))
        assertFalse(MobileInternetHelper.isValidIpv4("045.7.228.197"))
        assertFalse(MobileInternetHelper.isValidIpv4("1.2.3;reboot"))
    }

    @Test
    fun formatNetcnfGuideIncludesDhcpAndStaticGatewayWithCustomDns() {
        val tether = TetherSubnetInfo(
            interfaceName = "eth0",
            kind = InterfaceKind.USB_ETHERNET,
            phoneIp = "192.168.42.129",
            prefixLength = 24,
            subnetMask = "255.255.255.0",
            suggestedPs2Ip = "192.168.42.50"
        )
        val guide = MobileInternetHelper.formatPs2NetcnfGuide(
            cellular = CellularUpstreamInfo(connected = true, interfaceName = "rmnet_data0"),
            tether = tether,
            customDns = "45.7.228.197",
            natStatus = null
        )
        assertTrue(guide.contains("Connected (rmnet_data0)"))
        assertTrue(guide.contains("IP 192.168.42.50"))
        assertTrue(guide.contains("Mask 255.255.255.0"))
        assertTrue(guide.contains("Gateway 192.168.42.129"))
        assertTrue(guide.contains("Primary DNS 45.7.228.197"))
    }

    @Test
    fun rootNatControllerConfiguresMasqueradeForwardAndDnsRedirectAndCleansUp() {
        val ran = mutableListOf<String>()
        val controller = MobileDataNatController(object : ShellRunner {
            override fun run(command: List<String>): ShellResult {
                val joined = command.joinToString(" ")
                ran.add(joined)
                return when {
                    joined.contains("id -u") -> ShellResult(0, "0\n", "")
                    joined.contains("iptables -t nat -C") -> ShellResult(1, "", "")
                    joined.contains("iptables -C") -> ShellResult(1, "", "")
                    else -> ShellResult(0, "", "")
                }
            }
        })

        val enabled = controller.enableNat("eth0", "rmnet_data0", "45.7.228.197")
        assertTrue(enabled.active)
        assertTrue(ran.any { it.contains("sysctl -w net.ipv4.ip_forward=1") })
        assertTrue(ran.any { it.contains("iptables -t nat -A POSTROUTING -o rmnet_data0 -j MASQUERADE") })
        assertTrue(ran.any { it.contains("iptables -I FORWARD 1 -i eth0 -o rmnet_data0 -j ACCEPT") })
        assertTrue(ran.any { it.contains("-p udp --dport 53 -j DNAT --to-destination 45.7.228.197:53") })
        assertTrue(ran.any { it.contains("-p tcp --dport 53 -j DNAT --to-destination 45.7.228.197:53") })

        val disabled = controller.disableNat("eth0", "rmnet_data0", "45.7.228.197")
        assertFalse(disabled.active)
        assertTrue(ran.any { it.contains("iptables -t nat -D POSTROUTING -o rmnet_data0 -j MASQUERADE") })
        assertTrue(ran.any { it.contains("iptables -D FORWARD -i eth0 -o rmnet_data0 -j ACCEPT") })
        assertTrue(ran.any { it.contains("-D PREROUTING -i eth0 -p udp --dport 53 -j DNAT") })
    }
}
