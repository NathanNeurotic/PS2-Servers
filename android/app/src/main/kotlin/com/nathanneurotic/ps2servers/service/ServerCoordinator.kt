package com.nathanneurotic.ps2servers.service

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.documentfile.provider.DocumentFile
import com.nathanneurotic.ps2servers.config.LauncherConfig
import com.nathanneurotic.ps2servers.net.MobileDataNatController
import com.nathanneurotic.ps2servers.net.NatShareStatus
import com.nathanneurotic.ps2servers.net.Port445RedirectHelper
import com.nathanneurotic.ps2servers.net.PortBindCheck
import com.nathanneurotic.ps2servers.net.RedirectStatus
import com.nathanneurotic.ps2servers.servers.HttpServerEngine
import com.nathanneurotic.ps2servers.servers.Nfs4ServerEngine
import com.nathanneurotic.ps2servers.servers.ServerEngine
import com.nathanneurotic.ps2servers.servers.Smb2ServerEngine
import com.nathanneurotic.ps2servers.servers.Smbv1ServerEngine
import com.nathanneurotic.ps2servers.servers.UdpbdServerEngine
import com.nathanneurotic.ps2servers.servers.UdpfsServerEngine
import com.nathanneurotic.ps2servers.vfs.FileRandomAccess
import com.nathanneurotic.ps2servers.vfs.FileVfsRoot
import com.nathanneurotic.ps2servers.vfs.SafRandomAccess
import com.nathanneurotic.ps2servers.vfs.SafVfsRoot
import com.nathanneurotic.ps2servers.vfs.VfsRandomAccess
import com.nathanneurotic.ps2servers.vfs.VfsRoot
import java.io.File
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

interface SafBridge {
    fun openTreeVfs(treeUri: String, readOnly: Boolean, strictWindowsNames: Boolean): VfsRoot
    fun openSingleDocument(uri: String, writable: Boolean): VfsRandomAccess
    fun describeTree(treeUri: String): String
    fun hasPersistedReadPermission(treeUri: String): Boolean
    fun hasPersistedWritePermission(treeUri: String): Boolean
}

class AndroidSafBridge(context: Context) : SafBridge {
    private val appContext = context.applicationContext

    override fun openTreeVfs(treeUri: String, readOnly: Boolean, strictWindowsNames: Boolean): VfsRoot {
        val parsed = Uri.parse(treeUri)
        return SafVfsRoot(
            context = appContext,
            treeUri = parsed,
            isReadOnly = readOnly,
            strictWindowsNames = strictWindowsNames
        )
    }

    override fun openSingleDocument(uri: String, writable: Boolean): VfsRandomAccess {
        return SafRandomAccess(
            resolver = appContext.contentResolver,
            uri = Uri.parse(uri),
            writable = writable
        )
    }

    override fun describeTree(treeUri: String): String {
        if (treeUri.isBlank()) return "No SAF folder selected"
        val parsed = Uri.parse(treeUri)
        val hasRead = hasPersistedReadPermission(treeUri)
        val hasWrite = hasPersistedWritePermission(treeUri)
        val name = try {
            DocumentFile.fromTreeUri(appContext, parsed)?.name
                ?: DocumentsContract.getTreeDocumentId(parsed)
        } catch (_: Exception) {
            parsed.lastPathSegment
        } ?: treeUri
        val mode = when {
            !hasRead -> "permission missing"
            hasWrite -> "read/write"
            else -> "read-only"
        }
        return "$name ($mode)"
    }

    override fun hasPersistedReadPermission(treeUri: String): Boolean {
        if (treeUri.isBlank()) return false
        return appContext.contentResolver.persistedUriPermissions.any {
            it.uri.toString() == treeUri && it.isReadPermission
        }
    }

    override fun hasPersistedWritePermission(treeUri: String): Boolean {
        if (treeUri.isBlank()) return false
        return appContext.contentResolver.persistedUriPermissions.any {
            it.uri.toString() == treeUri && it.isWritePermission
        }
    }

    companion object {
        fun persistTreePermission(context: Context, uri: Uri, wantWrite: Boolean = true): Boolean {
            return try {
                val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or
                    if (wantWrite) Intent.FLAG_GRANT_WRITE_URI_PERMISSION else 0
                context.contentResolver.takePersistableUriPermission(uri, flags)
                true
            } catch (_: SecurityException) {
                try {
                    context.contentResolver.takePersistableUriPermission(
                        uri,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION
                    )
                    true
                } catch (_: Exception) {
                    false
                }
            }
        }

        fun persistDocumentPermission(context: Context, uri: Uri, wantWrite: Boolean = true): Boolean {
            return persistTreePermission(context, uri, wantWrite)
        }
    }
}

data class ServerStatusSnapshot(
    val key: String,
    val displayName: String,
    val running: Boolean,
    val boundPort: Int?,
    val redirect445Active: Boolean = false,
    val redirectMessage: String? = null
)

class ServerCoordinator(
    private val configFile: File,
    private val safBridge: SafBridge? = null,
    private val redirectHelper: Port445RedirectHelper = Port445RedirectHelper(),
    private val natController: MobileDataNatController = MobileDataNatController()
) {
    interface Listener {
        fun onStateChanged()
        fun onLogAppended(line: String)
    }

    private val lock = Any()
    private val config: LinkedHashMap<String, Any?> = LauncherConfig.load(configFile)
    private val runningEngines = LinkedHashMap<String, ServerEngine>()
    private val activeRedirects = LinkedHashMap<String, RedirectStatus>()
    private var activeNatStatus: NatShareStatus = NatShareStatus(active = false)
    private val logs = ArrayDeque<String>()
    private val listeners = LinkedHashSet<Listener>()

    companion object {
        private const val MAX_LOG_LINES = 400

        fun defaultConfigFile(context: Context): File = File(context.filesDir, "launcher.json")

        fun displayNameFor(key: String): String = when (key) {
            "udpfs" -> "UDPFS"
            "smbv1" -> "SMBv1"
            "smbv2" -> "SMBv2"
            "smbv3" -> "SMBv3"
            "http" -> "HTTP"
            "nfs" -> "NFSv4"
            "udpbd" -> "UDPBD"
            else -> key.uppercase(Locale.US)
        }

        fun folderKeyFor(serverKey: String): String = when (serverKey) {
            "smbv1", "smbv2", "smbv3" -> "games_folder"
            "udpbd" -> "virtual_folder"
            else -> "root_dir"
        }
    }

    fun addListener(listener: Listener) {
        synchronized(lock) {
            listeners.add(listener)
        }
    }

    fun removeListener(listener: Listener) {
        synchronized(lock) {
            listeners.remove(listener)
        }
    }

    @Suppress("UNCHECKED_CAST")
    private fun copyConfigMap(src: Map<String, Any?>): LinkedHashMap<String, Any?> {
        val out = LinkedHashMap<String, Any?>(src.size)
        for ((k, v) in src) {
            out[k] = when (v) {
                is Map<*, *> -> copyConfigMap(v as Map<String, Any?>)
                is List<*> -> ArrayList<Any?>(v)
                else -> v
            }
        }
        return out
    }

    fun snapshotConfig(): LinkedHashMap<String, Any?> = synchronized(lock) {
        copyConfigMap(config)
    }

    fun saveConfig() {
        val copy = synchronized(lock) { copyConfigMap(config) }
        LauncherConfig.save(configFile, copy)
    }

    fun resetConfig(): LinkedHashMap<String, Any?> {
        stopAll()
        val fresh = synchronized(lock) {
            LauncherConfig.reset(configFile)
            val reset = LauncherConfig.load(configFile)
            config.clear()
            config.putAll(reset)
            copyConfigMap(config)
        }
        appendLog("Reset settings to desktop-aligned defaults")
        notifyStateChanged()
        return fresh
    }

    fun updateTopLevel(key: String, value: Any?) {
        synchronized(lock) {
            config[key] = value
            LauncherConfig.save(configFile, config)
        }
        notifyStateChanged()
    }

    fun updateServerSetting(serverKey: String, settingKey: String, value: Any?) {
        synchronized(lock) {
            val map = LauncherConfig.serverMap(config, serverKey)
            map[settingKey] = value
            LauncherConfig.save(configFile, config)
        }
        notifyStateChanged()
    }

    fun logsSnapshot(): List<String> = synchronized(lock) { logs.toList() }

    fun clearLogs() {
        synchronized(lock) { logs.clear() }
        notifyStateChanged()
    }

    fun appendLog(message: String) {
        val ts = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
        val line = "[$ts] $message"
        val currentListeners: List<Listener>
        synchronized(lock) {
            if (logs.size >= MAX_LOG_LINES) {
                logs.removeFirst()
            }
            logs.addLast(line)
            currentListeners = listeners.toList()
        }
        for (l in currentListeners) {
            l.onLogAppended(line)
        }
    }

    private fun notifyStateChanged() {
        val currentListeners = synchronized(lock) { listeners.toList() }
        for (l in currentListeners) {
            l.onStateChanged()
        }
    }

    fun activeKeys(): Set<String> = synchronized(lock) { runningEngines.keys.toSet() }

    fun isAnyRunning(): Boolean = synchronized(lock) { runningEngines.isNotEmpty() }

    fun natStatus(): NatShareStatus = synchronized(lock) { activeNatStatus }

    fun isMobileNatActive(): Boolean = synchronized(lock) { activeNatStatus.active }

    fun isAnyBackgroundWorkActive(): Boolean = synchronized(lock) {
        runningEngines.isNotEmpty() || activeNatStatus.active
    }

    fun startMobileNat(lanIface: String, cellIface: String, dnsOverride: String = ""): NatShareStatus {
        val prev = synchronized(lock) { activeNatStatus }
        if (prev.active) {
            natController.disableNat(prev.lanIface, prev.cellularIface, prev.dnsOverride)
        }
        val status = natController.enableNat(lanIface, cellIface, dnsOverride)
        synchronized(lock) {
            activeNatStatus = status
        }
        appendLog("[Mobile Data] ${status.message}")
        notifyStateChanged()
        return status
    }

    fun stopMobileNat() {
        val current = synchronized(lock) {
            val prev = activeNatStatus
            activeNatStatus = NatShareStatus(active = false)
            prev
        }
        if (current.active) {
            val res = natController.disableNat(current.lanIface, current.cellularIface, current.dnsOverride)
            appendLog("[Mobile Data] ${res.message}")
            notifyStateChanged()
        }
    }

    fun needsMulticastLock(): Boolean = synchronized(lock) {
        runningEngines.containsKey("udpfs") || runningEngines.containsKey("udpbd")
    }

    fun statuses(): List<ServerStatusSnapshot> = synchronized(lock) {
        LauncherConfig.SERVER_KEYS.map { key ->
            val engine = runningEngines[key]
            val redirect = activeRedirects[key]
            ServerStatusSnapshot(
                key = key,
                displayName = displayNameFor(key),
                running = engine?.isRunning == true,
                boundPort = engine?.boundPort,
                redirect445Active = redirect?.active == true,
                redirectMessage = redirect?.message
            )
        }
    }

    fun describeActiveRoot(serverKey: String? = null): String = synchronized(lock) {
        if (serverKey != null) {
            val settings = LauncherConfig.serverMap(config, serverKey)
            val overrideDir = LauncherConfig.asString(settings[folderKeyFor(serverKey)]).trim()
            if (overrideDir.isNotEmpty()) {
                return "Path override: $overrideDir"
            }
        }
        val safUri = LauncherConfig.asString(config["android_saf_tree_uri"]).trim()
        if (safUri.isNotEmpty()) {
            val desc = safBridge?.describeTree(safUri) ?: safUri
            return "SAF: $desc"
        }
        val fallback = LauncherConfig.asString(config["android_fallback_path"]).trim()
        if (fallback.isNotEmpty()) {
            val dir = File(fallback)
            val state = when {
                !dir.exists() -> "missing"
                !dir.isDirectory -> "not a directory"
                dir.canWrite() -> "read/write"
                dir.canRead() -> "read-only"
                else -> "no read permission"
            }
            return "Folder: $fallback ($state)"
        }
        return "No shared folder selected"
    }

    fun resolveVfsRoot(serverKey: String?, readOnly: Boolean, strictWindowsNames: Boolean): VfsRoot {
        val (overrideDir, safUri, fallbackPath) = synchronized(lock) {
            val perServer = if (serverKey != null) {
                val map = LauncherConfig.serverMap(config, serverKey)
                LauncherConfig.asString(map[folderKeyFor(serverKey)]).trim()
            } else {
                ""
            }
            Triple(
                perServer,
                LauncherConfig.asString(config["android_saf_tree_uri"]).trim(),
                LauncherConfig.asString(config["android_fallback_path"]).trim()
            )
        }

        if (overrideDir.isNotEmpty()) {
            return FileVfsRoot(File(overrideDir), isReadOnly = readOnly, strictWindowsNames = strictWindowsNames)
        }
        if (safUri.isNotEmpty()) {
            val bridge = safBridge
                ?: throw IOException("SAF bridge is unavailable on this host; set a fallback filesystem path.")
            return bridge.openTreeVfs(safUri, readOnly = readOnly, strictWindowsNames = strictWindowsNames)
        }
        if (fallbackPath.isNotEmpty()) {
            return FileVfsRoot(File(fallbackPath), isReadOnly = readOnly, strictWindowsNames = strictWindowsNames)
        }
        throw IOException("Select a Games Folder (SAF) or fallback directory first.")
    }

    fun startServer(serverKey: String): ServerStatusSnapshot {
        require(serverKey in LauncherConfig.SERVER_KEYS) { "Unknown server key: $serverKey" }
        stopServer(serverKey)

        val pair = synchronized(lock) {
            copyConfigMap(LauncherConfig.serverMap(config, serverKey)) to
                copyConfigMap(config)
        }
        val settings = pair.first
        val topCfg = pair.second

        val engine = buildEngine(serverKey, settings, topCfg)
        var redirectStatus: RedirectStatus? = null
        try {
            engine.start()
            if (serverKey in setOf("smbv1", "smbv2", "smbv3") &&
                LauncherConfig.asBool(settings["port_445_redirect"], false)
            ) {
                redirectStatus = redirectHelper.applyRedirect(engine.boundPort)
                appendLog("[${displayNameFor(serverKey)}] ${redirectStatus.message}")
            }
        } catch (t: Throwable) {
            try {
                engine.stop()
            } catch (_: Exception) {
            }
            throw t
        }

        synchronized(lock) {
            runningEngines[serverKey] = engine
            if (redirectStatus != null) {
                activeRedirects[serverKey] = redirectStatus
            } else {
                activeRedirects.remove(serverKey)
            }
            config["last_active_servers"] = runningEngines.keys.toList()
            LauncherConfig.save(configFile, config)
        }
        appendLog("[${displayNameFor(serverKey)}] Started on port ${engine.boundPort}")
        notifyStateChanged()
        return ServerStatusSnapshot(
            key = serverKey,
            displayName = displayNameFor(serverKey),
            running = engine.isRunning,
            boundPort = engine.boundPort,
            redirect445Active = redirectStatus?.active == true,
            redirectMessage = redirectStatus?.message
        )
    }

    fun stopServer(serverKey: String) {
        val (engine, redirect) = synchronized(lock) {
            val e = runningEngines.remove(serverKey)
            val r = activeRedirects.remove(serverKey)
            if (e != null) {
                config["last_active_servers"] = runningEngines.keys.toList()
                LauncherConfig.save(configFile, config)
            }
            e to r
        }
        if (redirect != null && redirect.active) {
            val cleanup = redirectHelper.removeRedirect(redirect.targetPort)
            appendLog("[${displayNameFor(serverKey)}] ${cleanup.message}")
        }
        if (engine != null) {
            try {
                engine.stop()
            } catch (e: Exception) {
                appendLog("[${displayNameFor(serverKey)}] Stop warning: ${e.message}")
            }
            appendLog("[${displayNameFor(serverKey)}] Stopped")
            notifyStateChanged()
        }
    }

    fun stopAll() {
        val keys = synchronized(lock) { runningEngines.keys.toList() }
        for (key in keys) {
            stopServer(key)
        }
        stopMobileNat()
        notifyStateChanged()
    }

    fun restoreLastActive(): List<String> {
        val (autostart, lastKeys) = synchronized(lock) {
            val auto = LauncherConfig.asBool(config["autostart_last_config"], false)
            val raw = config["last_active_servers"] as? List<*> ?: emptyList<Any?>()
            auto to raw.mapNotNull { it?.toString() }.filter { it in LauncherConfig.SERVER_KEYS }
        }
        if (!autostart || lastKeys.isEmpty()) return emptyList()
        val errors = mutableListOf<String>()
        for (key in lastKeys) {
            try {
                startServer(key)
            } catch (e: Exception) {
                val msg = "${displayNameFor(key)}: ${e.message ?: e::class.java.simpleName}"
                errors.add(msg)
                appendLog("Autostart failed for $msg")
            }
        }
        return errors
    }

    private fun asDouble(value: Any?, default: Double): Double {
        return when (value) {
            is Number -> value.toDouble()
            is String -> value.trim().toDoubleOrNull() ?: default
            else -> default
        }
    }

    private fun requireBindablePort(serverKey: String, port: Int, redirect445Enabled: Boolean) {
        if (port == 0) return
        when (redirectHelper.checkPortBindability(port, redirect445Enabled)) {
            is PortBindCheck.Allowed -> Unit
            is PortBindCheck.PrivilegedPortRefused -> {
                throw IllegalStateException(
                    "${displayNameFor(serverKey)} cannot bind privileged port $port (<1024) on unrooted Android. " +
                        "Use port >= 1024 (e.g. 1025/1445) or enable root TCP 445 redirect."
                )
            }
            else -> {
                throw IllegalStateException(
                    "${displayNameFor(serverKey)} cannot bind port $port (<1024) without root privilege."
                )
            }
        }
    }

    private fun buildEngine(
        serverKey: String,
        settings: Map<String, Any?>,
        topCfg: Map<String, Any?>
    ): ServerEngine {
        val verbose = LauncherConfig.asBool(settings["verbose"], false)
        val bindIp = LauncherConfig.asString(settings["bind"]).trim()
        val logger: (String) -> Unit = { line -> appendLog(line) }

        return when (serverKey) {
            "udpfs" -> {
                val readOnly = LauncherConfig.asBool(settings["read_only"], false)
                val port = LauncherConfig.asInt(settings["port"], UdpfsServerEngine.DEFAULT_PORT)
                requireBindablePort(serverKey, port, false)
                val vfs = resolveVfsRoot("udpfs", readOnly = readOnly, strictWindowsNames = false)
                val blockPath = LauncherConfig.asString(settings["block_device"]).trim()
                val blockFactory: (() -> VfsRandomAccess)? = if (blockPath.isNotEmpty()) {
                    val file = File(blockPath)
                    if (!file.isFile) {
                        throw IOException("UDPFS block device does not exist: $blockPath")
                    }
                    { FileRandomAccess(file, writable = !readOnly) }
                } else {
                    null
                }
                val fallbackSec = asDouble(settings["fallback_interval"], 0.25)
                val txDelayMs = asDouble(settings["tx_delay_ms"] ?: settings["tx_delay"], 0.0)
                val peerTimeoutSec = asDouble(settings["peer_timeout"], 3600.0)
                UdpfsServerEngine(
                    vfs = vfs,
                    blockDeviceFactory = blockFactory,
                    port = port,
                    dataPort = LauncherConfig.asInt(settings["data_port"], 0),
                    bindIp = bindIp,
                    protocolModeSetting = LauncherConfig.asString(settings["protocol_mode"], "Auto (recommended)"),
                    enableCompression = LauncherConfig.asBool(settings["enable_compression"], true),
                    readOnly = readOnly,
                    sectorSize = LauncherConfig.asInt(settings["sector_size"], 512),
                    txDelayMs = txDelayMs,
                    peerTimeoutSec = peerTimeoutSec,
                    maxTransferBytes = LauncherConfig.asInt(settings["max_transfer_bytes"], 8 * 1024 * 1024),
                    maxSessions = LauncherConfig.asInt(settings["max_sessions"], 256),
                    fallbackIntervalMs = (fallbackSec * 1000.0).toLong().coerceAtLeast(10L),
                    verbose = verbose,
                    log = logger
                )
            }
            "smbv1" -> {
                val readOnly = LauncherConfig.asBool(settings["read_only"], false)
                val port = LauncherConfig.asInt(settings["port"], Smbv1ServerEngine.DEFAULT_PORT)
                val redirect445 = LauncherConfig.asBool(settings["port_445_redirect"], false)
                requireBindablePort(serverKey, port, redirect445)
                val vfs = resolveVfsRoot("smbv1", readOnly = readOnly, strictWindowsNames = true)
                Smbv1ServerEngine(
                    vfs = vfs,
                    shareName = LauncherConfig.asString(settings["share_name"], "games").trim().ifEmpty { "games" },
                    port = port,
                    bindIp = bindIp,
                    username = LauncherConfig.asString(settings["username"], "guest"),
                    password = LauncherConfig.asString(settings["password"]),
                    readOnly = readOnly,
                    enableCompression = LauncherConfig.asBool(settings["enable_compression"], false),
                    redirect445Enabled = redirect445,
                    verbose = verbose,
                    log = logger
                )
            }
            "smbv2", "smbv3" -> {
                val readOnly = LauncherConfig.asBool(settings["read_only"], false)
                val port = LauncherConfig.asInt(settings["port"], Smb2ServerEngine.DEFAULT_PORT)
                val redirect445 = LauncherConfig.asBool(settings["port_445_redirect"], false)
                requireBindablePort(serverKey, port, redirect445)
                val vfs = resolveVfsRoot(serverKey, readOnly = readOnly, strictWindowsNames = true)
                val ceiling = if (serverKey == "smbv3") {
                    Smb2ServerEngine.DIALECT_302
                } else {
                    Smb2ServerEngine.DIALECT_210
                }
                Smb2ServerEngine(
                    vfs = vfs,
                    key = serverKey,
                    ceiling = ceiling,
                    shareName = LauncherConfig.asString(settings["share_name"], "games").trim().ifEmpty { "games" },
                    port = port,
                    bindIp = bindIp,
                    username = LauncherConfig.asString(settings["username"], "ps2"),
                    password = LauncherConfig.asString(settings["password"]),
                    openShare = LauncherConfig.asBool(settings["open_share"], false),
                    readOnly = readOnly,
                    redirect445Enabled = redirect445,
                    verbose = verbose,
                    log = logger
                )
            }
            "http" -> {
                val port = LauncherConfig.asInt(settings["port"], HttpServerEngine.DEFAULT_PORT)
                requireBindablePort(serverKey, port, false)
                val vfs = resolveVfsRoot("http", readOnly = true, strictWindowsNames = false)
                val normalizedProfile = LauncherConfig.httpProfileWireValue(settings["catalog_profile"])
                    ?: HttpServerEngine.PROFILE_LEGACY_FLAT
                HttpServerEngine(
                    vfs = vfs,
                    port = port,
                    bindIp = bindIp,
                    enableCompression = LauncherConfig.asBool(settings["enable_compression"], true),
                    catalogProfile = normalizedProfile,
                    recursive = LauncherConfig.asBool(settings["recursive"], false),
                    verbose = verbose,
                    log = logger
                )
            }
            "nfs" -> {
                val allowWrites = LauncherConfig.asBool(settings["allow_writes"], false)
                val port = LauncherConfig.asInt(settings["port"], Nfs4ServerEngine.DEFAULT_PORT)
                requireBindablePort(serverKey, port, false)
                val vfs = resolveVfsRoot("nfs", readOnly = !allowWrites, strictWindowsNames = false)
                Nfs4ServerEngine(
                    vfs = vfs,
                    port = port,
                    bindIp = bindIp,
                    allowWrites = allowWrites,
                    verbose = verbose,
                    log = logger
                )
            }
            "udpbd" -> {
                val readOnly = LauncherConfig.asBool(settings["read_only"], false)
                val mode = LauncherConfig.asString(topCfg["android_udpbd_mode"], "virtual_exfat")
                val safDocUri = LauncherConfig.asString(topCfg["android_udpbd_image_uri"]).trim()
                val imagePath = LauncherConfig.asString(settings["image_file"]).trim()
                if (mode == "image_file") {
                    val factory: () -> VfsRandomAccess
                    if (safDocUri.isNotEmpty()) {
                        val bridge = safBridge
                            ?: throw IOException("SAF bridge is unavailable for UDPBD image URI")
                        factory = { bridge.openSingleDocument(safDocUri, writable = !readOnly) }
                    } else if (imagePath.isNotEmpty()) {
                        val imgFile = File(imagePath)
                        if (!imgFile.isFile) {
                            throw IOException("UDPBD image file does not exist: $imagePath")
                        }
                        factory = { FileRandomAccess(imgFile, writable = !readOnly) }
                    } else {
                        throw IOException("Select a UDPBD disk image (.iso/.img) or switch to Virtual exFAT folder mode.")
                    }
                    UdpbdServerEngine(
                        vfsRoot = null,
                        rawDeviceFactory = factory,
                        port = LauncherConfig.asInt(settings["port"], UdpbdServerEngine.DEFAULT_PORT),
                        bindIp = bindIp,
                        readOnly = readOnly,
                        verbose = verbose,
                        log = logger
                    )
                } else {
                    val vfs = resolveVfsRoot("udpbd", readOnly = true, strictWindowsNames = false)
                    UdpbdServerEngine(
                        vfsRoot = vfs,
                        rawDeviceFactory = null,
                        port = LauncherConfig.asInt(settings["port"], UdpbdServerEngine.DEFAULT_PORT),
                        bindIp = bindIp,
                        readOnly = true,
                        verbose = verbose,
                        log = logger
                    )
                }
            }
            else -> throw IllegalArgumentException("Unsupported server key: $serverKey")
        }
    }
}
