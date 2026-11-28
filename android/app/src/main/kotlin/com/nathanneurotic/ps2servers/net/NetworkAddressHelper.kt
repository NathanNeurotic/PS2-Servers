package com.nathanneurotic.ps2servers.net

import com.nathanneurotic.ps2servers.config.LauncherConfig
import java.net.Inet4Address
import java.net.NetworkInterface

enum class InterfaceKind(val priority: Int, val label: String) {
    USB_ETHERNET(0, "USB-Ethernet / Tethering"),
    HOTSPOT(1, "Wi-Fi Hotspot"),
    WIFI(2, "Wi-Fi"),
    OTHER(3, "LAN")
}

data class NetworkAddressCandidate(
    val interfaceName: String,
    val displayName: String,
    val ipv4: String,
    val kind: InterfaceKind
) {
    fun summary(): String = "$ipv4 ($interfaceName · ${kind.label})"
}

object NetworkAddressHelper {
    private val CELLULAR_PREFIXES = listOf("rmnet", "ccmni", "pdp", "clat", "v4-rmnet", "tun", "ppp")
    private val USB_PREFIXES = listOf("eth", "rndis", "usb", "ncm", "ecm", "enp")
    private val HOTSPOT_PREFIXES = listOf("ap", "swlan", "softap", "p2p")

    fun classifyInterface(name: String, displayName: String = ""): InterfaceKind {
        val lower = name.lowercase()
        val disp = displayName.lowercase()
        if (USB_PREFIXES.any { lower.startsWith(it) } || disp.contains("ethernet") || disp.contains("rndis")) {
            return InterfaceKind.USB_ETHERNET
        }
        if (HOTSPOT_PREFIXES.any { lower.startsWith(it) } || lower == "wlan1" || disp.contains("hotspot")) {
            return InterfaceKind.HOTSPOT
        }
        if (lower.startsWith("wlan") || lower.startsWith("wifi") || disp.contains("wi-fi") || disp.contains("wlan")) {
            return InterfaceKind.WIFI
        }
        return InterfaceKind.OTHER
    }

    fun isExcludedInterface(name: String): Boolean {
        val lower = name.lowercase()
        return lower == "lo" || CELLULAR_PREFIXES.any { lower.startsWith(it) }
    }

    fun isPrivateOrLinkLocalLan(ipv4: String): Boolean {
        val parts = ipv4.split(".")
        if (parts.size != 4) return false
        val octets = parts.map { it.toIntOrNull() ?: return false }
        if (octets.any { it !in 0..255 }) return false
        val a = octets[0]
        val b = octets[1]
        if (a == 127) return false
        if (a == 10) return true
        if (a == 172 && b in 16..31) return true
        if (a == 192 && b == 168) return true
        if (a == 169 && b == 254) return true
        // Carrier-grade NAT space used by some USB tethering / hotspot bridges (100.64.0.0/10)
        if (a == 100 && b in 64..127) return true
        return false
    }

    fun rankCandidates(candidates: List<NetworkAddressCandidate>): List<NetworkAddressCandidate> {
        return candidates
            .filter { !isExcludedInterface(it.interfaceName) }
            .distinctBy { "${it.interfaceName}:${it.ipv4}" }
            .sortedWith(
                compareBy<NetworkAddressCandidate>(
                    { if (isPrivateOrLinkLocalLan(it.ipv4)) 0 else 1 },
                    { it.kind.priority },
                    { it.interfaceName },
                    { it.ipv4 }
                )
            )
    }

    fun detectLanAddresses(): List<NetworkAddressCandidate> {
        val found = ArrayList<NetworkAddressCandidate>()
        val ifaces = try {
            NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()
        } catch (_: Exception) {
            emptyList()
        }
        for (iface in ifaces) {
            try {
                if (!iface.isUp || iface.isLoopback) continue
                val name = iface.name ?: continue
                if (isExcludedInterface(name)) continue
                val disp = iface.displayName ?: name
                val kind = classifyInterface(name, disp)
                for (addr in iface.inetAddresses.toList()) {
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        val host = addr.hostAddress ?: continue
                        found.add(NetworkAddressCandidate(name, disp, host, kind))
                    }
                }
            } catch (_: Exception) {
                // Skip interface that fails enumeration on restricted devices.
            }
        }
        return rankCandidates(found)
    }
}

object OplHintFormatter {
    fun formatHint(serverKey: String, ip: String, settings: Map<String, Any?>, boundPort: Int? = null): String {
        val shownIp = ip.ifBlank { "<phone-ip>" }
        return when (serverKey) {
            "smbv1", "smbv2", "smbv3" -> {
                val redirect445 = LauncherConfig.asBool(settings["port_445_redirect"], false)
                val defaultPort = if (serverKey == "smbv1") 1025 else 1445
                val port = if (redirect445) {
                    "445 (-> ${boundPort ?: LauncherConfig.asInt(settings["port"], defaultPort)})"
                } else {
                    (boundPort ?: LauncherConfig.asInt(settings["port"], defaultPort)).toString()
                }
                val share = LauncherConfig.asString(settings["share_name"], "games").trim().ifEmpty { "games" }
                val user = LauncherConfig.asString(settings["username"]).trim()
                val password = if (user.isNotEmpty()) LauncherConfig.asString(settings["password"]) else ""
                val creds = if (LauncherConfig.asBool(settings["open_share"], false) && user.isEmpty()) {
                    "Anonymous / Guest (No Auth)"
                } else {
                    val userDisplay = user.ifEmpty { "guest" }
                    "User '$userDisplay' · Password ${if (password.isNotEmpty()) "as set" else "blank"}"
                }
                val clientNote = if (serverKey == "smbv3" && redirect445) {
                    " · Neutrino bsd-smb ready (port 445)"
                } else ""
                "In OPL/Launchstation -> IP $shownIp · Port $port · Share '$share' · NetBIOS off · $creds$clientNote"
            }
            "udpfs" -> {
                val port = boundPort ?: LauncherConfig.asInt(settings["port"], 0xF5F6)
                "In OPL/Neutrino -> select UDPFS · IP $shownIp · Port $port (0x${port.toString(16).uppercase()})"
            }
            "http" -> {
                val port = boundPort ?: LauncherConfig.asInt(settings["port"], 1100)
                "In OPL -> Protocol 'HTTP' · IP $shownIp · Port $port · set Port to $port, not 0"
            }
            "nfs" -> {
                val port = boundPort ?: 2049
                "In RiptOPL/Neutrino/Launchstation -> Protocol 'NFS' · IP $shownIp · Port $port · Export '/'"
            }
            "udpbd" -> {
                val port = boundPort ?: 0xBDBD
                "In OPL/Neutrino -> select UDPBD · auto-discovered on UDP $port (0x${port.toString(16).uppercase()})"
            }
            else -> ""
        }
    }
}
