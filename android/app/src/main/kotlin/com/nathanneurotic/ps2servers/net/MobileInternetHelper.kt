package com.nathanneurotic.ps2servers.net

import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.provider.Settings
import java.net.Inet4Address
import java.net.NetworkInterface

data class TetherSubnetInfo(
    val interfaceName: String,
    val kind: InterfaceKind,
    val phoneIp: String,
    val prefixLength: Int,
    val subnetMask: String,
    val suggestedPs2Ip: String
) {
    fun summary(): String = "$interfaceName (${kind.label}) · $phoneIp/$prefixLength"
}

data class CellularUpstreamInfo(
    val connected: Boolean,
    val interfaceName: String?
)

data class NatShareStatus(
    val active: Boolean,
    val lanIface: String = "",
    val cellularIface: String = "",
    val dnsOverride: String = "",
    val message: String = "Mobile Data NAT bridge idle"
)

/**
 * Helpers for sharing the phone's Mobile Data internet connection with a PS2
 * alongside local game serving:
 * - Unrooted Android: detects USB-Ethernet tethering / Wi-Fi hotspot / LAN interfaces
 *   and computes matching DHCP/static PS2 Network Config (NETCNF) settings, plus launches
 *   Android's Tethering settings screen.
 * - Rooted Android: configures iptables NAT + IP forwarding (+ optional TCP/UDP 53 DNS
 *   redirect) via [MobileDataNatController] for Android 5-10 OTG adapters or custom DNS.
 */
object MobileInternetHelper {
    private val IFACE_NAME_REGEX = Regex("^[a-zA-Z0-9_.-]{1,32}$")
    private val CELLULAR_IFACE_PREFIXES = listOf("rmnet", "ccmni", "pdp", "v4-rmnet", "clat")

    fun isValidInterfaceName(name: String): Boolean = IFACE_NAME_REGEX.matches(name)

    fun isValidIpv4(ip: String): Boolean {
        val parts = ip.trim().split(".")
        if (parts.size != 4) return false
        return parts.all { part ->
            val n = part.toIntOrNull() ?: return false
            n in 0..255 && n.toString() == part
        }
    }

    fun prefixLengthToMask(prefixLength: Int): String {
        val clamped = prefixLength.coerceIn(0, 32)
        val maskInt = if (clamped == 0) 0 else (-1 shl (32 - clamped))
        val a = (maskInt ushr 24) and 0xFF
        val b = (maskInt ushr 16) and 0xFF
        val c = (maskInt ushr 8) and 0xFF
        val d = maskInt and 0xFF
        return "$a.$b.$c.$d"
    }

    fun suggestPs2StaticIp(phoneIp: String, prefixLength: Int): String {
        val parts = phoneIp.trim().split(".")
        if (parts.size != 4) return "192.168.1.50"
        val octets = parts.map { it.toIntOrNull() ?: return "192.168.1.50" }
        if (octets.any { it !in 0..255 }) return "192.168.1.50"
        val hostMask = if (prefixLength in 1..30) (1 shl (32 - prefixLength)) - 1 else 0xFF
        val ipInt = (octets[0] shl 24) or (octets[1] shl 16) or (octets[2] shl 8) or octets[3]
        val netInt = ipInt and hostMask.inv()
        val broadcastInt = netInt or hostMask
        val candidateA = netInt + 50
        val chosen = when {
            candidateA < broadcastInt && candidateA != ipInt -> candidateA
            netInt + 51 < broadcastInt && netInt + 51 != ipInt -> netInt + 51
            netInt + 2 < broadcastInt && netInt + 2 != ipInt -> netInt + 2
            else -> netInt + 1
        }
        return "${(chosen ushr 24) and 0xFF}.${(chosen ushr 16) and 0xFF}.${(chosen ushr 8) and 0xFF}.${chosen and 0xFF}"
    }

    fun detectTetherSubnets(): List<TetherSubnetInfo> {
        val out = ArrayList<TetherSubnetInfo>()
        val ifaces = try {
            NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()
        } catch (_: Exception) {
            emptyList()
        }
        for (iface in ifaces) {
            try {
                if (!iface.isUp || iface.isLoopback) continue
                val name = iface.name ?: continue
                if (NetworkAddressHelper.isExcludedInterface(name)) continue
                val kind = NetworkAddressHelper.classifyInterface(name, iface.displayName ?: name)
                for (ifAddr in iface.interfaceAddresses) {
                    val addr = ifAddr.address as? Inet4Address ?: continue
                    if (addr.isLoopbackAddress) continue
                    val host = addr.hostAddress ?: continue
                    val prefix = ifAddr.networkPrefixLength.toInt().let { if (it in 1..32) it else 24 }
                    out.add(
                        TetherSubnetInfo(
                            interfaceName = name,
                            kind = kind,
                            phoneIp = host,
                            prefixLength = prefix,
                            subnetMask = prefixLengthToMask(prefix),
                            suggestedPs2Ip = suggestPs2StaticIp(host, prefix)
                        )
                    )
                }
            } catch (_: Exception) {
                // Ignore restricted interfaces
            }
        }
        return out
            .distinctBy { "${it.interfaceName}:${it.phoneIp}" }
            .sortedWith(
                compareBy<TetherSubnetInfo>(
                    { if (NetworkAddressHelper.isPrivateOrLinkLocalLan(it.phoneIp)) 0 else 1 },
                    { it.kind.priority },
                    { it.interfaceName },
                    { it.phoneIp }
                )
            )
    }

    @Suppress("DEPRECATION")
    fun detectCellularUpstream(context: Context): CellularUpstreamInfo {
        try {
            val cm = context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            if (cm != null) {
                for (network in cm.allNetworks) {
                    val caps = cm.getNetworkCapabilities(network) ?: continue
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) {
                        val lp = cm.getLinkProperties(network)
                        val iface = lp?.interfaceName?.trim()?.takeIf { it.isNotEmpty() }
                        return CellularUpstreamInfo(connected = true, interfaceName = iface)
                    }
                }
            }
        } catch (_: Exception) {
        }
        // Fallback scan for cellular interface names if ConnectivityManager omits LinkProperties
        try {
            for (iface in NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()) {
                if (!iface.isUp || iface.isLoopback) continue
                val name = iface.name ?: continue
                val lower = name.lowercase()
                if (CELLULAR_IFACE_PREFIXES.any { lower.startsWith(it) } &&
                    iface.inetAddresses.toList().any { it is Inet4Address && !it.isLoopbackAddress }
                ) {
                    return CellularUpstreamInfo(connected = true, interfaceName = name)
                }
            }
        } catch (_: Exception) {
        }
        return CellularUpstreamInfo(connected = false, interfaceName = null)
    }

    fun tetherSettingsIntents(): List<Intent> = listOf(
        Intent().setClassName("com.android.settings", "com.android.settings.TetherSettings"),
        Intent("android.settings.TETHER_SETTINGS"),
        Intent(Settings.ACTION_WIRELESS_SETTINGS)
    )

    fun formatPs2NetcnfGuide(
        cellular: CellularUpstreamInfo,
        tether: TetherSubnetInfo?,
        customDns: String,
        natStatus: NatShareStatus?
    ): String {
        val sb = StringBuilder()
        val cellLabel = if (cellular.connected) {
            "Connected (${cellular.interfaceName ?: "cellular"})"
        } else {
            "Not detected (turn on Mobile Data)"
        }
        sb.append("Mobile Data: ").append(cellLabel)

        if (natStatus?.active == true) {
            sb.append("\nRoot NAT: Active (").append(natStatus.lanIface)
                .append(" -> ").append(natStatus.cellularIface).append(")")
            if (natStatus.dnsOverride.isNotBlank()) {
                sb.append(" · DNS DNAT -> ").append(natStatus.dnsOverride)
            }
        }

        if (tether != null) {
            val dnsLine = customDns.trim().takeIf { it.isNotEmpty() } ?: tether.phoneIp
            sb.append("\nBridge Interface: ").append(tether.summary())
            sb.append("\nPS2 NETCNF Auto: IP Auto (DHCP) · DNS ").append(
                if (customDns.trim().isNotEmpty()) "Manual ${customDns.trim()}" else "Auto (${tether.phoneIp})"
            )
            sb.append("\nPS2 NETCNF Static: IP ").append(tether.suggestedPs2Ip)
                .append(" · Mask ").append(tether.subnetMask)
                .append(" · Gateway ").append(tether.phoneIp)
                .append(" · Primary DNS ").append(dnsLine)
        } else {
            sb.append("\nNo USB-Ethernet or Hotspot interface active.")
            sb.append("\nConnect USB-Ethernet and enable Ethernet Tethering (Android 11+), ")
            sb.append("use Wi-Fi Hotspot + bridge, or enable Root NAT Bridge on rooted Android 5-10.")
        }
        return sb.toString()
    }
}

class MobileDataNatController(
    private val runner: ShellRunner = ProcessShellRunner
) {
    fun hasRootAccess(): Boolean {
        val res = runner.run(listOf("su", "-c", "id -u"))
        return res.exitCode == 0 && res.stdout.trim() == "0"
    }

    fun enableNat(lanIface: String, cellIface: String, dnsOverride: String = ""): NatShareStatus {
        val cleanLan = lanIface.trim()
        val cleanCell = cellIface.trim()
        val cleanDns = dnsOverride.trim()
        require(MobileInternetHelper.isValidInterfaceName(cleanLan)) {
            "Invalid LAN interface name: '$cleanLan'"
        }
        require(MobileInternetHelper.isValidInterfaceName(cleanCell)) {
            "Invalid cellular interface name: '$cleanCell'"
        }
        if (cleanDns.isNotEmpty()) {
            require(MobileInternetHelper.isValidIpv4(cleanDns)) {
                "Invalid DNS IPv4 address: '$cleanDns'"
            }
        }
        if (!hasRootAccess()) {
            return NatShareStatus(
                active = false,
                lanIface = cleanLan,
                cellularIface = cleanCell,
                dnsOverride = cleanDns,
                message = "Root NAT bridge requires root (su unavailable); use Android Ethernet Tethering instead"
            )
        }

        val forwardRes = runner.run(listOf("su", "-c", "sysctl -w net.ipv4.ip_forward=1"))
        if (forwardRes.exitCode != 0) {
            val detail = forwardRes.stderr.ifBlank { forwardRes.stdout }.ifBlank { "exit ${forwardRes.exitCode}" }
            return NatShareStatus(false, cleanLan, cleanCell, cleanDns, "Failed to enable ip_forward: $detail")
        }

        val natRules = mutableListOf(
            Pair(
                "iptables -t nat -C POSTROUTING -o $cleanCell -j MASQUERADE",
                "iptables -t nat -A POSTROUTING -o $cleanCell -j MASQUERADE"
            ),
            Pair(
                "iptables -C FORWARD -i $cleanLan -o $cleanCell -j ACCEPT",
                "iptables -I FORWARD 1 -i $cleanLan -o $cleanCell -j ACCEPT"
            ),
            Pair(
                "iptables -C FORWARD -i $cleanCell -o $cleanLan -m state --state RELATED,ESTABLISHED -j ACCEPT",
                "iptables -I FORWARD 1 -i $cleanCell -o $cleanLan -m state --state RELATED,ESTABLISHED -j ACCEPT"
            )
        )
        if (cleanDns.isNotEmpty()) {
            natRules.add(
                Pair(
                    "iptables -t nat -C PREROUTING -i $cleanLan -p udp --dport 53 -j DNAT --to-destination $cleanDns:53",
                    "iptables -t nat -A PREROUTING -i $cleanLan -p udp --dport 53 -j DNAT --to-destination $cleanDns:53"
                )
            )
            natRules.add(
                Pair(
                    "iptables -t nat -C PREROUTING -i $cleanLan -p tcp --dport 53 -j DNAT --to-destination $cleanDns:53",
                    "iptables -t nat -A PREROUTING -i $cleanLan -p tcp --dport 53 -j DNAT --to-destination $cleanDns:53"
                )
            )
        }

        for ((checkCmd, addCmd) in natRules) {
            val check = runner.run(listOf("su", "-c", checkCmd))
            if (check.exitCode != 0) {
                val add = runner.run(listOf("su", "-c", addCmd))
                if (add.exitCode != 0) {
                    val detail = add.stderr.ifBlank { add.stdout }.ifBlank { "exit ${add.exitCode}" }
                    return NatShareStatus(
                        active = false,
                        lanIface = cleanLan,
                        cellularIface = cleanCell,
                        dnsOverride = cleanDns,
                        message = "Failed to add NAT rule: $detail"
                    )
                }
            }
        }

        // Best-effort policy routing rule for Android netd secondary cellular tables.
        runner.run(
            listOf(
                "su",
                "-c",
                "ip rule del iif $cleanLan lookup $cleanCell pref 17500 2>/dev/null || true; " +
                    "ip rule add iif $cleanLan lookup $cleanCell pref 17500 2>/dev/null || true"
            )
        )

        val dnsNote = if (cleanDns.isNotEmpty()) " (DNS -> $cleanDns)" else ""
        return NatShareStatus(
            active = true,
            lanIface = cleanLan,
            cellularIface = cleanCell,
            dnsOverride = cleanDns,
            message = "Mobile Data NAT active: $cleanLan -> $cleanCell$dnsNote"
        )
    }

    fun disableNat(lanIface: String, cellIface: String, dnsOverride: String = ""): NatShareStatus {
        val cleanLan = lanIface.trim()
        val cleanCell = cellIface.trim()
        val cleanDns = dnsOverride.trim()
        if (!MobileInternetHelper.isValidInterfaceName(cleanLan) ||
            !MobileInternetHelper.isValidInterfaceName(cleanCell)
        ) {
            return NatShareStatus(false, message = "Mobile Data NAT idle")
        }
        if (!hasRootAccess()) {
            return NatShareStatus(false, message = "Root unavailable; skipping NAT cleanup")
        }
        val deletions = mutableListOf(
            "iptables -t nat -D POSTROUTING -o $cleanCell -j MASQUERADE",
            "iptables -D FORWARD -i $cleanLan -o $cleanCell -j ACCEPT",
            "iptables -D FORWARD -i $cleanCell -o $cleanLan -m state --state RELATED,ESTABLISHED -j ACCEPT"
        )
        if (cleanDns.isNotEmpty() && MobileInternetHelper.isValidIpv4(cleanDns)) {
            deletions.add("iptables -t nat -D PREROUTING -i $cleanLan -p udp --dport 53 -j DNAT --to-destination $cleanDns:53")
            deletions.add("iptables -t nat -D PREROUTING -i $cleanLan -p tcp --dport 53 -j DNAT --to-destination $cleanDns:53")
        }
        for (delCmd in deletions) {
            runner.run(listOf("su", "-c", delCmd))
        }
        runner.run(listOf("su", "-c", "ip rule del iif $cleanLan lookup $cleanCell pref 17500 2>/dev/null || true"))
        return NatShareStatus(false, message = "Mobile Data NAT stopped ($cleanLan -> $cleanCell)")
    }
}
