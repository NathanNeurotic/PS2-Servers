package com.nathanneurotic.ps2servers.config

import java.io.File
import java.io.FileOutputStream
import java.io.IOException

/**
 * Persisted launcher settings compatible with Desktop's launcher.json schema
 * and field defaults (launcher/servers.py + launcher/config.py).
 */
object LauncherConfig {
    const val FILENAME = "launcher.json"
    const val VERBOSE_MIGRATION_KEY = "verbose_default_on"

    val SERVER_KEYS = listOf("udpfs", "smbv1", "smbv2", "smbv3", "http", "nfs", "udpbd")

    val PROTOCOL_MODE_CHOICES = listOf(
        "Auto (recommended)" to "auto",
        "Standard only" to "standard",
        "Legacy / Modulo only" to "modulo"
    )

    val HTTP_CATALOG_PROFILE_CHOICES = listOf(
        "Legacy flat (OPL-HTTP)" to "legacy-flat",
        "RiptOPL path-aware" to "riptopl-path-aware"
    )

    private val PROTOCOL_MODE_BY_NAME: Map<String, String> = buildMap {
        for ((label, value) in PROTOCOL_MODE_CHOICES) {
            put(label.lowercase(), value)
            put(value.lowercase(), value)
        }
        put("proper", "standard")
    }

    private val HTTP_PROFILE_BY_NAME: Map<String, String> = buildMap {
        for ((label, value) in HTTP_CATALOG_PROFILE_CHOICES) {
            put(label.lowercase(), value)
            put(value.lowercase(), value)
        }
    }

    fun protocolModeWireValue(raw: Any?): String? {
        val key = raw?.toString()?.trim()?.lowercase() ?: return null
        return PROTOCOL_MODE_BY_NAME[key]
    }

    fun httpProfileWireValue(raw: Any?): String? {
        val key = raw?.toString()?.trim()?.lowercase() ?: return null
        return HTTP_PROFILE_BY_NAME[key]
    }

    fun defaultServerSettings(serverKey: String): LinkedHashMap<String, Any?> {
        return when (serverKey) {
            "udpfs" -> linkedMapOf(
                "root_dir" to "",
                "block_device" to "",
                "enable_compression" to true,
                "enforce_modulo" to false,
                "protocol_mode" to "Auto (recommended)",
                "read_only" to false,
                "port" to 0xF5F6,
                "data_port" to 0,
                "bind" to "",
                "tx_delay_ms" to "0",
                "peer_timeout" to "3600",
                "verbose" to true,
                "session_summary" to false
            )
            "smbv1" -> linkedMapOf(
                "games_folder" to "",
                "share_name" to "games",
                "username" to "guest",
                "password" to "",
                "port" to 1025,
                "read_only" to false,
                "enable_compression" to false,
                "bind" to "",
                "verbose" to true,
                "session_summary" to false,
                "port_445_redirect" to false
            )
            "smbv2", "smbv3" -> linkedMapOf(
                "games_folder" to "",
                "share_name" to "games",
                "port" to 1445,
                "username" to "ps2",
                "password" to "",
                "open_share" to false,
                "read_only" to false,
                "bind" to "",
                "verbose" to true,
                "session_summary" to false,
                "port_445_redirect" to false
            )
            "http" -> linkedMapOf(
                "root_dir" to "",
                "enable_compression" to true,
                "catalog_profile" to "Legacy flat (OPL-HTTP)",
                "recursive" to false,
                "port" to 1100,
                "bind" to "",
                "verbose" to true,
                "session_summary" to false
            )
            "nfs" -> linkedMapOf(
                "root_dir" to "",
                "port" to 2049,
                "allow_writes" to false,
                "bind" to "",
                "verbose" to true
            )
            "udpbd" -> linkedMapOf(
                "image_file" to "",
                "virtual_folder" to "",
                "read_only" to false,
                "verbose" to true,
                "session_summary" to false
            )
            else -> linkedMapOf()
        }
    }

    fun defaultConfig(): LinkedHashMap<String, Any?> {
        val servers = linkedMapOf<String, Any?>()
        for (key in SERVER_KEYS) {
            servers[key] = defaultServerSettings(key)
        }
        return linkedMapOf(
            "servers" to servers,
            "ip" to "",
            "ip_custom" to false,
            "autostart_last_config" to false,
            "last_active_servers" to emptyList<String>(),
            VERBOSE_MIGRATION_KEY to true,
            "android_saf_tree_uri" to "",
            "android_fallback_path" to "",
            "android_udpbd_image_uri" to "",
            "android_udpbd_mode" to "virtual_exfat",
            "android_ps2_dns" to "",
            "android_nat_lan_iface" to "",
            "android_nat_cell_iface" to ""
        )
    }

    fun load(file: File): LinkedHashMap<String, Any?> {
        if (!file.isFile) return defaultConfig()
        return try {
            val parsed = MinimalJson.parseObject(file.readText(Charsets.UTF_8))
            normalizeLoaded(parsed)
        } catch (_: Exception) {
            defaultConfig()
        }
    }

    fun normalizeLoaded(raw: Map<String, Any?>): LinkedHashMap<String, Any?> {
        val migratedVerbose = migrateVerboseDefault(raw)
        val out = LinkedHashMap<String, Any?>(migratedVerbose)
        val defaults = defaultConfig()
        for ((k, v) in defaults) {
            if (k != "servers" && !out.containsKey(k)) {
                out[k] = v
            }
        }
        val rawServers = (out["servers"] as? Map<*, *>) ?: emptyMap<Any?, Any?>()
        val mergedServers = linkedMapOf<String, Any?>()
        for (key in SERVER_KEYS) {
            val base = defaultServerSettings(key)
            val savedEntry = rawServers[key] as? Map<*, *>
            val typed = linkedMapOf<String, Any?>()
            if (savedEntry != null) {
                for ((sk, sv) in savedEntry) {
                    if (sk != null) typed[sk.toString()] = sv
                }
            }
            val migrated = migrateSaved(key, typed)
            for ((mk, mv) in migrated) {
                base[mk] = mv
            }
            mergedServers[key] = base
        }
        for ((k, v) in rawServers) {
            val ks = k?.toString() ?: continue
            if (!mergedServers.containsKey(ks)) {
                mergedServers[ks] = v
            }
        }
        out["servers"] = mergedServers
        return LinkedHashMap(out)
    }

    fun migrateSaved(serverKey: String, saved: Map<String, Any?>): LinkedHashMap<String, Any?> {
        if (saved.isEmpty() || serverKey != "udpfs") {
            return LinkedHashMap(saved)
        }
        if (!asBool(saved["modulo_mode"], false)) {
            return LinkedHashMap(saved)
        }
        val migrated = LinkedHashMap(saved)
        migrated["enforce_modulo"] = true
        if (protocolModeWireValue(migrated["protocol_mode"]) == null) {
            migrated["protocol_mode"] = "Modulo"
        }
        migrated.remove("modulo_mode")
        return migrated
    }

    fun migrateVerboseDefault(saved: Map<String, Any?>): LinkedHashMap<String, Any?> {
        if (asBool(saved[VERBOSE_MIGRATION_KEY], false)) {
            return LinkedHashMap(saved)
        }
        val migrated = LinkedHashMap(saved)
        migrated[VERBOSE_MIGRATION_KEY] = true
        val cards = saved["servers"] as? Map<*, *>
        if (cards != null) {
            val out = linkedMapOf<String, Any?>()
            for ((k, v) in cards) {
                val key = k?.toString() ?: continue
                if (key in SERVER_KEYS && v is Map<*, *>) {
                    val copy = linkedMapOf<String, Any?>()
                    for ((fk, fv) in v) {
                        if (fk != null) copy[fk.toString()] = fv
                    }
                    copy["verbose"] = true
                    out[key] = copy
                } else {
                    out[key] = v
                }
            }
            migrated["servers"] = out
        }
        return migrated
    }

    fun save(file: File, data: Map<String, Any?>) {
        val parent = file.parentFile
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw IOException("Cannot create config directory: ${parent.absolutePath}")
        }
        val tmp = File(file.parentFile, file.name + ".tmp")
        val content = MinimalJson.stringify(data, indent = 2)
        try {
            FileOutputStream(tmp).use { fos ->
                fos.write(content.toByteArray(Charsets.UTF_8))
                fos.fd.sync()
            }
            if (!tmp.renameTo(file)) {
                if (file.exists() && !file.delete()) {
                    throw IOException("Failed to replace ${file.absolutePath}")
                }
                if (!tmp.renameTo(file)) {
                    throw IOException("Failed to move ${tmp.absolutePath} to ${file.absolutePath}")
                }
            }
        } catch (e: Exception) {
            tmp.delete()
            throw e
        }
    }

    fun reset(file: File) {
        if (file.exists()) {
            file.delete()
        }
    }

    @Suppress("UNCHECKED_CAST")
    fun serverMap(config: MutableMap<String, Any?>, serverKey: String): MutableMap<String, Any?> {
        val servers = (config["servers"] as? MutableMap<String, Any?>)
            ?: linkedMapOf<String, Any?>().also { config["servers"] = it }
        val existing = servers[serverKey] as? MutableMap<String, Any?>
        if (existing != null) return existing
        return defaultServerSettings(serverKey).also { servers[serverKey] = it }
    }

    fun asBool(value: Any?, default: Boolean = false): Boolean = when (value) {
        is Boolean -> value
        is Number -> value.toInt() != 0
        is String -> when (value.trim().lowercase()) {
            "1", "true", "yes", "on" -> true
            "0", "false", "no", "off" -> false
            else -> default
        }
        else -> default
    }

    fun asInt(value: Any?, default: Int): Int = when (value) {
        is Number -> value.toInt()
        is String -> {
            val trimmed = value.trim()
            if (trimmed.isEmpty()) default
            else if (trimmed.startsWith("0x", ignoreCase = true)) {
                trimmed.substring(2).toIntOrNull(16) ?: default
            } else {
                trimmed.toIntOrNull() ?: default
            }
        }
        else -> default
    }

    fun asString(value: Any?, default: String = ""): String = value?.toString() ?: default
}
