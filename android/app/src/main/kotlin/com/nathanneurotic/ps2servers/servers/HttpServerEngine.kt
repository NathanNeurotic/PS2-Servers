package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.compression.CompressedIso
import com.nathanneurotic.ps2servers.vfs.VfsEntry
import com.nathanneurotic.ps2servers.vfs.VfsRandomAccess
import com.nathanneurotic.ps2servers.vfs.VfsRoot
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.EOFException
import java.io.IOException
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.net.URLDecoder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Pure-Kotlin HTTP/1.1 keep-alive server for OPL-HTTP (`legacy-flat`) and
 * RiptOPL (`riptopl-path-aware`).
 *
 * Enforces the PS2 IOP http_cdvdman.irx wire contract:
 *  - Range reads always answer 206 Partial Content with exact requested byte count.
 *  - Response headers stay strictly under 512 bytes (no Server banner).
 *  - games.csv always includes Content-Length (never chunked) and respects client caps.
 */
class HttpServerEngine(
    private val vfs: VfsRoot,
    private val port: Int = DEFAULT_PORT,
    private val bindIp: String = "",
    private val enableCompression: Boolean = true,
    private val catalogProfile: String = PROFILE_LEGACY_FLAT,
    private val recursive: Boolean = false,
    private val verbose: Boolean = false,
    private val log: (String) -> Unit = {}
) : ServerEngine {
    override val key: String = "http"
    override val displayName: String = "HTTP"
    override val boundPort: Int
        get() = actualPort
    companion object {
        const val DEFAULT_PORT = 1100
        const val PROFILE_LEGACY_FLAT = "legacy-flat"
        const val PROFILE_RIPTOPL_PATH_AWARE = "riptopl-path-aware"

        const val STARTUP_MAX = 12
        const val FILENAME_MAX = 159
        const val GAMES_CSV_MAX = 8192
        const val CD_SIZE_LIMIT = 800L * 1024L * 1024L
        const val RIPTOPL_STARTUP_MAX = 12
        const val RIPTOPL_TITLE_MAX = 160
        const val RIPTOPL_PATH_MAX = 255
        const val RIPTOPL_CATALOG_BYTES_MAX = 262144
        const val RIPTOPL_CATALOG_ROWS_MAX = 2048
        const val RIPTOPL_URI_MAX = 768
        const val RIPTOPL_BASE_PATH_MAX = 64
        const val RECURSIVE_MAX_DEPTH = 4
        const val RECURSIVE_MAX_DIRS = 2000
        const val RESCAN_INTERVAL_MS = 5000L

        private val RAW_EXTENSIONS = setOf(".iso", ".zso", ".ziso")
        private val STARTUP_RE = Regex("""^([A-Za-z]{4}_\d{3}\.\d{2})\.(.+)$""")
        private val RANGE_RE = Regex("""^bytes=(\d*)-(\d*)$""")
        private const val RIPTOPL_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~/"
    }

    data class GameEntry(
        val startup: String,
        val advertised: String,
        val relSourcePath: String,
        val media: String,
        val size: Long,
        val compressed: Boolean,
        val conventional: Boolean,
        val relPath: String,
        val title: String
    ) {
        fun legacyCsvLine(): String = "$startup,$advertised,$media"
        fun riptoplCsvLine(): String = "${csvQuote(startup)},${csvQuote(title)},$media,${csvQuote(relPath)}"

        private fun csvQuote(v: String): String = "\"${v.replace("\"", "\"\"")}\""
    }

    private class RangeException(message: String) : Exception(message)

    private val pathAware = catalogProfile == PROFILE_RIPTOPL_PATH_AWARE
    private val running = AtomicBoolean(false)
    private var serverSocket: ServerSocket? = null
    private var acceptThread: Thread? = null
    private var workerPool: ExecutorService? = null

    private val indexLock = Any()
    private var entries = HashMap<String, GameEntry>()
    private var exactPaths = HashMap<String, GameEntry>()
    private var pathEntries = HashMap<String, GameEntry>()
    private var ambiguousNames = HashSet<String>()
    private var ambiguousPaths = HashSet<String>()
    private var csvBytes = ByteArray(0)
    private var lastSignature: String? = null
    private var lastScanMs: Long = 0L

    @Volatile
    var actualPort: Int = 0
        private set

    override val isRunning: Boolean
        get() = running.get()

    override fun start() {
        if (recursive && !pathAware) {
            throw IllegalArgumentException("Recursive scanning requires the RiptOPL path-aware profile")
        }
        if (!running.compareAndSet(false, true)) return
        ServerPortGuard.ensureBindable(port, redirect445Enabled = false)
        try {
            refresh(force = true)
            val ss = ServerSocket().apply {
                reuseAddress = true
                val addr = if (bindIp.isBlank()) InetSocketAddress(port) else InetSocketAddress(InetAddress.getByName(bindIp.trim()), port)
                bind(addr, 64)
            }
            serverSocket = ss
            actualPort = ss.localPort
            workerPool = Executors.newCachedThreadPool { r ->
                Thread(r, "ps2-http-conn").apply { isDaemon = true }
            }
            log("HTTP ($catalogProfile) listening on ${bindIp.ifBlank { "0.0.0.0" }}:$actualPort (${vfs.displayPath})")
            acceptThread = Thread({ acceptLoop(ss) }, "ps2-http-accept").apply {
                isDaemon = true
                start()
            }
        } catch (e: Exception) {
            running.set(false)
            cleanup()
            throw e
        }
    }

    override fun stop() {
        running.set(false)
        try {
            serverSocket?.close()
        } catch (_: Exception) {
        }
        workerPool?.shutdownNow()
        try {
            acceptThread?.join(1500L)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        cleanup()
    }

    private fun cleanup() {
        serverSocket = null
        acceptThread = null
        workerPool = null
    }

    private fun acceptLoop(ss: ServerSocket) {
        while (running.get()) {
            try {
                val client = ss.accept()
                workerPool?.submit { handleClient(client) } ?: client.close()
            } catch (_: SocketException) {
                break
            } catch (e: IOException) {
                if (running.get()) log("HTTP accept warning: ${e.message}")
            }
        }
    }

    private fun handleClient(socket: Socket) {
        var cachedSourcePath: String? = null
        var cachedCompressed = false
        var cachedHandle: VfsRandomAccess? = null
        try {
            socket.tcpNoDelay = true
            socket.soTimeout = 120_000
            val input = BufferedInputStream(socket.getInputStream(), 8192)
            val out = socket.getOutputStream()

            while (running.get()) {
                val requestLine = readAsciiLine(input) ?: break
                if (requestLine.isBlank()) continue
                val parts = requestLine.split(' ')
                if (parts.size < 3) {
                    sendShort(out, 400, "Bad Request", sendBody = true, close = true)
                    break
                }
                val method = parts[0].uppercase(Locale.US)
                val target = parts[1]
                val version = parts[2].uppercase(Locale.US)

                val headers = HashMap<String, String>()
                while (true) {
                    val line = readAsciiLine(input) ?: break
                    if (line.isEmpty()) break
                    val colon = line.indexOf(':')
                    if (colon > 0) {
                        val name = line.substring(0, colon).trim().lowercase(Locale.US)
                        val value = line.substring(colon + 1).trim()
                        headers[name] = value
                    }
                }

                val connHdr = headers["connection"]?.lowercase(Locale.US)
                val closeConn = if (version == "HTTP/1.0") connHdr != "keep-alive" else connHdr == "close"

                if (method != "GET" && method != "HEAD") {
                    sendShort(out, 405, "Method Not Allowed", sendBody = true, close = closeConn)
                    if (closeConn) break else continue
                }

                val sendBody = method != "HEAD"
                val normPath = normalizeRequestPath(target)
                if (normPath == null) {
                    sendShort(out, 400, "Bad Request", sendBody, close = closeConn)
                    if (closeConn) break else continue
                }

                if (isGamesCsv(normPath)) {
                    val payload = getGamesCsv()
                    serveBytes(out, 200, "OK", "text/csv", payload, sendBody, closeConn)
                    if (closeConn) break else continue
                }

                val entry = lookup(normPath)
                if (entry == null) {
                    if (verbose) log("HTTP 404 $normPath")
                    sendShort(out, 404, "Not Found", sendBody, close = closeConn)
                    if (closeConn) break else continue
                }

                val total = entry.size
                val span = try {
                    parseRange(headers["range"], total)
                } catch (_: RangeException) {
                    sendShort(out, 416, "Requested Range Not Satisfiable", sendBody, close = closeConn)
                    if (closeConn) break else continue
                }

                val handle = try {
                    if (cachedSourcePath == entry.relSourcePath && cachedCompressed == entry.compressed && cachedHandle != null) {
                        cachedHandle
                    } else {
                        try {
                            cachedHandle?.close()
                        } catch (_: Exception) {
                        }
                        val raw = vfs.openRead(entry.relSourcePath)
                        val opened = if (entry.compressed) {
                            try {
                                CompressedIso.open(entry.relSourcePath, raw)
                            } catch (e: Exception) {
                                raw.close()
                                throw e
                            }
                        } else {
                            raw
                        }
                        cachedSourcePath = entry.relSourcePath
                        cachedCompressed = entry.compressed
                        cachedHandle = opened
                        opened
                    }
                } catch (e: Exception) {
                    log("HTTP 500 opening ${entry.relSourcePath}: ${e.message}")
                    sendShort(out, 500, "Internal Server Error", sendBody, close = true)
                    break
                }

                if (verbose) log("HTTP $method ${headers["range"] ?: normPath}")

                if (span == null) {
                    val hdr = buildHeaderBlock(
                        status = "200 OK",
                        contentType = "application/octet-stream",
                        contentLength = total,
                        contentRange = null,
                        acceptRanges = true,
                        close = closeConn
                    )
                    out.write(hdr)
                    if (sendBody) {
                        streamRange(handle, 0L, total, out)
                    }
                    out.flush()
                } else {
                    val (start, end) = span
                    val length = end - start + 1L
                    val hdr = buildHeaderBlock(
                        status = "206 Partial Content",
                        contentType = "application/octet-stream",
                        contentLength = length,
                        contentRange = "bytes $start-$end/$total",
                        acceptRanges = true,
                        close = closeConn
                    )
                    out.write(hdr)
                    if (sendBody) {
                        streamRange(handle, start, length, out)
                    }
                    out.flush()
                }
                if (closeConn) break
            }
        } catch (_: EOFException) {
        } catch (_: SocketException) {
        } catch (e: Exception) {
            if (running.get() && verbose) log("HTTP client error: ${e.message}")
        } finally {
            try {
                cachedHandle?.close()
            } catch (_: Exception) {
            }
            try {
                socket.close()
            } catch (_: Exception) {
            }
        }
    }

    private fun readAsciiLine(input: BufferedInputStream): String? {
        val baos = ByteArrayOutputStream(128)
        while (true) {
            val b = input.read()
            if (b < 0) {
                return if (baos.size() == 0) null else baos.toString("ISO-8859-1").trimEnd('\r')
            }
            if (b == '\n'.code) {
                return baos.toString("ISO-8859-1").trimEnd('\r')
            }
            baos.write(b)
            if (baos.size() > 8192) throw IOException("Header line too long")
        }
    }

    private fun buildHeaderBlock(
        status: String,
        contentType: String,
        contentLength: Long,
        contentRange: String?,
        acceptRanges: Boolean,
        close: Boolean
    ): ByteArray {
        val fmt = SimpleDateFormat("EEE, dd MMM yyyy HH:mm:ss 'GMT'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("GMT")
        }
        val sb = StringBuilder(256)
        sb.append("HTTP/1.1 ").append(status).append("\r\n")
        sb.append("Date: ").append(fmt.format(Date())).append("\r\n")
        sb.append("Content-Type: ").append(contentType).append("\r\n")
        if (contentRange != null) {
            sb.append("Content-Range: ").append(contentRange).append("\r\n")
        }
        sb.append("Content-Length: ").append(contentLength).append("\r\n")
        if (acceptRanges) {
            sb.append("Accept-Ranges: bytes\r\n")
        }
        if (close) {
            sb.append("Connection: close\r\n")
        }
        sb.append("\r\n")
        return sb.toString().toByteArray(Charsets.ISO_8859_1)
    }

    private fun sendShort(out: OutputStream, code: Int, reason: String, sendBody: Boolean, close: Boolean) {
        val body = "$reason\n".toByteArray(Charsets.US_ASCII)
        serveBytes(out, code, reason, "text/plain", body, sendBody, close)
    }

    private fun serveBytes(
        out: OutputStream,
        code: Int,
        reason: String,
        contentType: String,
        body: ByteArray,
        sendBody: Boolean,
        close: Boolean
    ) {
        val hdr = buildHeaderBlock(
            status = "$code $reason",
            contentType = contentType,
            contentLength = body.size.toLong(),
            contentRange = null,
            acceptRanges = false,
            close = close
        )
        out.write(hdr)
        if (sendBody && body.isNotEmpty()) {
            out.write(body)
        }
        out.flush()
    }

    private fun streamRange(handle: VfsRandomAccess, offset: Long, length: Long, out: OutputStream) {
        var pos = offset
        var remaining = length
        val buf = ByteArray(64 * 1024)
        while (remaining > 0L) {
            val want = minOf(remaining, buf.size.toLong()).toInt()
            val n = handle.readAt(pos, buf, 0, want)
            if (n <= 0) {
                throw IOException("Short read at offset=$pos")
            }
            out.write(buf, 0, n)
            pos += n
            remaining -= n
        }
    }

    private fun parseRange(header: String?, total: Long): Pair<Long, Long>? {
        if (header.isNullOrBlank()) return null
        val match = RANGE_RE.matchEntire(header.trim()) ?: throw RangeException(header)
        val sStr = match.groupValues[1]
        val eStr = match.groupValues[2]
        if (sStr.isEmpty()) throw RangeException(header)
        val start = sStr.toLongOrNull() ?: throw RangeException(header)
        val end = if (eStr.isEmpty()) total - 1L else (eStr.toLongOrNull() ?: throw RangeException(header))
        if (start > end || start >= total || end >= total) {
            throw RangeException(header)
        }
        return start to end
    }

    private fun normalizeRequestPath(target: String): String? {
        val raw = target.substringBefore('?').substringBefore('#')
        val decoded = try {
            URLDecoder.decode(raw, "UTF-8")
        } catch (_: Exception) {
            return null
        }
        if (decoded.indexOf('\u0000') >= 0) return null
        val stack = ArrayList<String>()
        for (seg in decoded.split('/')) {
            when (seg) {
                "", "." -> {}
                ".." -> if (stack.isNotEmpty()) stack.removeAt(stack.size - 1)
                else -> stack.add(seg)
            }
        }
        return stack.joinToString("/")
    }

    private fun isGamesCsv(normPath: String): Boolean {
        val base = normPath.substringAfterLast('/')
        return base.equals("games.csv", ignoreCase = true)
    }

    fun getGamesCsv(): ByteArray {
        val now = System.currentTimeMillis()
        synchronized(indexLock) {
            if (now - lastScanMs >= RESCAN_INTERVAL_MS) {
                lastScanMs = now
                refresh(force = false)
            }
            return csvBytes
        }
    }

    fun lookup(normPath: String): GameEntry? {
        if (pathAware) {
            val parts = normPath.trimStart('/').split('/').filter { it.isNotEmpty() }
            synchronized(indexLock) {
                for (start in parts.indices) {
                    val candidate = parts.subList(start, parts.size).joinToString("/")
                    exactPaths[candidate]?.let { return it }
                    val folded = candidate.lowercase(Locale.US)
                    if (folded !in ambiguousPaths) {
                        pathEntries[folded]?.let { return it }
                    }
                }
            }
            return null
        }
        val clean = normPath.trimStart('/').lowercase(Locale.US)
        synchronized(indexLock) {
            pathEntries[clean]?.let { return it }
            val base = clean.substringAfterLast('/')
            if (base in ambiguousNames) return null
            return entries[base]
        }
    }

    fun refresh(force: Boolean) {
        val scanDirs = scanDirs()
        val sig = buildString {
            for ((dirRel, _) in scanDirs) {
                append(dirRel).append(':')
                for (e in runCatching { vfs.list(dirRel) }.getOrDefault(emptyList())) {
                    append(e.name).append('|').append(e.size).append('|').append(e.lastModifiedMs).append(';')
                }
            }
            val diskCsv = vfs.stat("games.csv")?.takeIf { !it.isDirectory }
            if (diskCsv != null) {
                append("__disk_games_csv__:").append(diskCsv.size).append('|').append(diskCsv.lastModifiedMs)
            }
        }

        synchronized(indexLock) {
            if (!force && sig == lastSignature) return
            val decomps = decompressibleExtensions()
            if (pathAware) {
                rebuildPathAware(scanDirs, decomps, sig)
            } else {
                rebuildFlat(scanDirs, decomps, sig)
            }
        }
    }

    private fun decompressibleExtensions(): List<String> {
        if (!enableCompression) return emptyList()
        return if (pathAware) {
            CompressedIso.SUPPORTED_EXTENSIONS
        } else {
            CompressedIso.SUPPORTED_EXTENSIONS.filter { it != ".zso" && it != ".ziso" }
        }
    }

    private fun scanDirs(): List<Pair<String, String?>> {
        if (recursive) {
            val result = ArrayList<Pair<String, String?>>()
            val queue = ArrayDeque<Triple<String, String?, Int>>()
            result.add("" to null)
            queue.addLast(Triple("", null, 0))
            while (queue.isNotEmpty() && result.size < RECURSIVE_MAX_DIRS) {
                val (dir, media, depth) = queue.removeFirst()
                if (depth >= RECURSIVE_MAX_DEPTH) continue
                val children = runCatching { vfs.list(dir) }.getOrDefault(emptyList())
                    .filter { it.isDirectory }
                    .sortedBy { it.name }
                for (child in children) {
                    if (result.size >= RECURSIVE_MAX_DIRS) break
                    val implied = if (depth == 0 && child.name.uppercase(Locale.US) in setOf("CD", "DVD")) {
                        child.name.uppercase(Locale.US)
                    } else {
                        media
                    }
                    result.add(child.relativePath to implied)
                    queue.addLast(Triple(child.relativePath, implied, depth + 1))
                }
            }
            return result
        }
        val out = ArrayList<Pair<String, String?>>()
        out.add("" to null)
        val rootChildren = runCatching { vfs.list("") }.getOrDefault(emptyList()).filter { it.isDirectory }
        for (target in listOf("DVD", "CD")) {
            val found = rootChildren.firstOrNull { it.name.equals(target, ignoreCase = true) }
            if (found != null) {
                out.add(found.relativePath to target)
            }
        }
        return out
    }

    private fun rebuildFlat(
        scanDirs: List<Pair<String, String?>>,
        decomps: List<String>,
        sig: String
    ) {
        val nextEntries = HashMap<String, GameEntry>()
        val nextPathEntries = HashMap<String, GameEntry>()
        val nextAmbiguous = HashSet<String>()
        for ((dirRel, impliedMedia) in scanDirs) {
            val items = runCatching { vfs.list(dirRel) }.getOrDefault(emptyList()).filter { !it.isDirectory }
            for (item in items) {
                val entry = buildGameEntry(dirRel, item, impliedMedia, decomps) ?: continue
                if (dirRel.isNotEmpty()) {
                    nextPathEntries["$dirRel/${entry.advertised}".lowercase(Locale.US)] = entry
                    nextPathEntries["$dirRel/${item.name}".lowercase(Locale.US)] = entry
                }
                val key = entry.advertised.lowercase(Locale.US)
                if (nextEntries.containsKey(key)) {
                    nextAmbiguous.add(key)
                } else {
                    nextEntries[key] = entry
                }
            }
        }
        entries = nextEntries
        pathEntries = nextPathEntries
        ambiguousNames = nextAmbiguous

        val diskCsvEntry = vfs.stat("games.csv")?.takeIf { !it.isDirectory }
        if (diskCsvEntry != null && diskCsvEntry.size in 1L..GAMES_CSV_MAX.toLong()) {
            try {
                vfs.openRead("games.csv").use { ra ->
                    val bytes = ByteArray(diskCsvEntry.size.toInt())
                    var got = 0
                    while (got < bytes.size) {
                        val n = ra.readAt(got.toLong(), bytes, got, bytes.size - got)
                        if (n <= 0) break
                        got += n
                    }
                    csvBytes = if (got == bytes.size) bytes else bytes.copyOf(got)
                    lastSignature = sig
                    log("HTTP indexed ${entries.size} game(s) + disk games.csv")
                    return
                }
            } catch (_: Exception) {
            }
        }

        val baos = ByteArrayOutputStream()
        for (entry in entries.values.sortedBy { it.advertised.lowercase(Locale.US) }) {
            val line = (entry.legacyCsvLine() + "\n").toByteArray(Charsets.UTF_8)
            if (baos.size() + line.size > GAMES_CSV_MAX) continue
            baos.write(line)
        }
        csvBytes = baos.toByteArray()
        lastSignature = sig
        log("HTTP indexed ${entries.size} game(s) from ${vfs.displayPath}")
    }

    private fun rebuildPathAware(
        scanDirs: List<Pair<String, String?>>,
        decomps: List<String>,
        sig: String
    ) {
        val candidates = LinkedHashMap<String, GameEntry>()
        for ((dirRel, impliedMedia) in scanDirs) {
            val items = runCatching { vfs.list(dirRel) }.getOrDefault(emptyList()).filter { !it.isDirectory }
            for (item in items) {
                val entry = buildGameEntry(dirRel, item, impliedMedia, decomps) ?: continue
                if (!candidates.containsKey(entry.relPath)) {
                    candidates[entry.relPath] = entry
                }
            }
        }

        val body = ByteArrayOutputStream()
        val startups = HashSet<String>()
        val retained = ArrayList<GameEntry>()
        var rows = 0
        for (entry in candidates.values.sortedWith(compareBy({ it.relPath.lowercase(Locale.US) }, { it.relPath }))) {
            if (riptoplRowProblem(entry.startup, entry.title, entry.relPath) != null) continue
            if (!startups.add(entry.startup)) continue
            val line = (entry.riptoplCsvLine() + "\n").toByteArray(Charsets.UTF_8)
            if (rows >= RIPTOPL_CATALOG_ROWS_MAX || body.size() + line.size > RIPTOPL_CATALOG_BYTES_MAX) continue
            body.write(line)
            rows++
            retained.add(entry)
        }

        val nextExact = HashMap<String, GameEntry>()
        val nextFolded = HashMap<String, GameEntry>()
        val nextAmb = HashSet<String>()
        for (entry in retained) {
            nextExact[entry.relPath] = entry
            val key = entry.relPath.lowercase(Locale.US)
            if (nextFolded.containsKey(key)) {
                nextAmb.add(key)
            } else {
                nextFolded[key] = entry
            }
        }
        for (key in nextAmb) nextFolded.remove(key)

        entries = HashMap()
        exactPaths = nextExact
        pathEntries = nextFolded
        ambiguousPaths = nextAmb
        ambiguousNames = HashSet()
        csvBytes = body.toByteArray()
        lastSignature = sig
        log("HTTP (RiptOPL) indexed ${retained.size} game(s) from ${vfs.displayPath}")
    }

    private fun buildGameEntry(
        dirRel: String,
        item: VfsEntry,
        impliedMedia: String?,
        decomps: List<String>
    ): GameEntry? {
        val dot = item.name.lastIndexOf('.')
        if (dot <= 0) return null
        val stem = item.name.substring(0, dot)
        val lowerExt = item.name.substring(dot).lowercase(Locale.US)

        val advertised: String
        val size: Long
        val compressed: Boolean
        if (lowerExt in decomps) {
            advertised = "$stem.iso"
            val probeSize = try {
                vfs.openRead(item.relativePath).use { CompressedIso.probeInfo(it) }
            } catch (_: Exception) {
                null
            } ?: return null
            size = probeSize
            compressed = true
        } else if (lowerExt in RAW_EXTENSIONS) {
            val outExt = if (lowerExt == ".ziso") ".zso" else lowerExt
            advertised = "$stem$outExt"
            size = item.size
            compressed = false
        } else {
            return null
        }

        val media = impliedMedia ?: if (size <= CD_SIZE_LIMIT) "CD" else "DVD"
        val match = STARTUP_RE.matchEntire(stem)
        val conventional = match != null
        val rawStartup = match?.groupValues?.get(1)?.uppercase(Locale.US) ?: stem.take(STARTUP_MAX)
        val rawTitle = match?.groupValues?.get(2) ?: stem

        if (pathAware) {
            val relPath = if (dirRel.isEmpty()) advertised else "$dirRel/$advertised"
            val startup = if (conventional) rawStartup else utf8Prefix(stem.trim(' ', '\t'), RIPTOPL_STARTUP_MAX).trim(' ', '\t')
            val title = utf8Prefix(rawTitle.trim(' ', '\t'), RIPTOPL_TITLE_MAX).trim(' ', '\t')
            return GameEntry(startup, advertised, item.relativePath, media, size, compressed, conventional, relPath, title)
        }

        if (advertised.length > FILENAME_MAX) return null
        if (advertised.any { it == ',' || it == '\n' || it == '\r' }) return null
        return GameEntry(rawStartup, advertised, item.relativePath, media, size, compressed, conventional, advertised, advertised)
    }

    private fun utf8Prefix(text: String, limit: Int): String {
        val bytes = text.toByteArray(Charsets.UTF_8)
        if (bytes.size <= limit) return text
        var end = limit
        while (end > 0 && (bytes[end].toInt() and 0xC0) == 0x80) {
            end--
        }
        return String(bytes, 0, end, Charsets.UTF_8)
    }

    private fun riptoplRowProblem(startup: String, title: String, relPath: String): String? {
        val checks = listOf(
            Triple("startup", startup, RIPTOPL_STARTUP_MAX),
            Triple("title", title, RIPTOPL_TITLE_MAX),
            Triple("path", relPath, RIPTOPL_PATH_MAX)
        )
        for ((label, value, limit) in checks) {
            if (value != value.trim(' ', '\t')) return "$label leading/trailing blank"
            if (value.isEmpty()) return "empty $label"
            if (value.any { it.code < 0x20 || it.code == 0x7F }) return "control char in $label"
            if (value.toByteArray(Charsets.UTF_8).size > limit) return "$label too long"
        }
        if ("://" in relPath || relPath.startsWith('/') || '\\' in relPath) return "bad path"
        if (relPath.split('/').any { it.isEmpty() || it == "." || it == ".." }) return "bad segment"
        val lower = relPath.lowercase(Locale.US)
        if ((!lower.endsWith(".iso") && !lower.endsWith(".zso")) || relPath.length <= 4) return "bad ext"
        val encodedLen = buildString {
            for (b in relPath.toByteArray(Charsets.UTF_8)) {
                val ch = (b.toInt() and 0xFF).toChar()
                if (ch in RIPTOPL_UNRESERVED) append(ch)
                else append(String.format(Locale.US, "%%%02X", b.toInt() and 0xFF))
            }
        }.length
        if (encodedLen + RIPTOPL_BASE_PATH_MAX >= RIPTOPL_URI_MAX) return "URI too long"
        return null
    }
}
