package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.compression.CompressedIso
import com.nathanneurotic.ps2servers.vfs.PathGuard
import com.nathanneurotic.ps2servers.vfs.VfsEntry
import com.nathanneurotic.ps2servers.vfs.VfsPathException
import com.nathanneurotic.ps2servers.vfs.VfsRandomAccess
import com.nathanneurotic.ps2servers.vfs.VfsRoot
import java.io.BufferedInputStream
import java.io.EOFException
import java.io.IOException
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Pure-Kotlin SMBv1/CIFS server compatible with OPL, POPStarter, and Launchstation.
 *
 * Implements direct-TCP NetBIOS framing, NT LM 0.12 OEM/ASCII dialect negotiation,
 * guest or cleartext account auth, IPC$/RAP NetShareEnum, TRANS2 FIND_FIRST2/FIND_NEXT2
 * (8-byte aligned batches capped at 2048 bytes for the PS2 IOP driver), full file/dir
 * CRUD with open-file sharing checks, and optional CSO decompression to virtual `.iso`.
 */
class Smbv1ServerEngine(
    private val vfs: VfsRoot,
    private val shareName: String = "games",
    private val port: Int = DEFAULT_PORT,
    private val bindIp: String = "",
    private val username: String = "guest",
    private val password: String = "",
    private val readOnly: Boolean = false,
    private val enableCompression: Boolean = false,
    private val redirect445Enabled: Boolean = false,
    private val verbose: Boolean = false,
    private val log: (String) -> Unit = {}
) : ServerEngine {

    companion object {
        const val DEFAULT_PORT = 1025

        private val SMB_MAGIC = byteArrayOf(0xFF.toByte(), 'S'.code.toByte(), 'M'.code.toByte(), 'B'.code.toByte())

        const val SMB_COM_CREATE_DIRECTORY = 0x00
        const val SMB_COM_DELETE_DIRECTORY = 0x01
        const val SMB_COM_CLOSE = 0x04
        const val SMB_COM_DELETE = 0x06
        const val SMB_COM_RENAME = 0x07
        const val SMB_COM_CHECK_DIRECTORY = 0x10
        const val SMB_COM_QUERY_INFORMATION_DISK = 0x80
        const val SMB_COM_TRANSACTION = 0x25
        const val SMB_COM_ECHO = 0x2B
        const val SMB_COM_OPEN_ANDX = 0x2D
        const val SMB_COM_READ_ANDX = 0x2E
        const val SMB_COM_WRITE_ANDX = 0x2F
        const val SMB_COM_TRANS2 = 0x32
        const val SMB_COM_TREE_DISCONNECT = 0x71
        const val SMB_COM_NEGOTIATE = 0x72
        const val SMB_COM_SESSION_SETUP_ANDX = 0x73
        const val SMB_COM_LOGOFF_ANDX = 0x74
        const val SMB_COM_TREE_CONNECT_ANDX = 0x75
        const val SMB_COM_NT_CREATE_ANDX = 0xA2
        const val SMB_COM_NONE = 0xFF

        const val STATUS_SUCCESS = 0x00000000
        const val STATUS_NOT_IMPLEMENTED = 0xC0000002.toInt()
        const val STATUS_ACCESS_DENIED = 0xC0000022.toInt()
        const val STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034.toInt()
        const val STATUS_OBJECT_NAME_COLLISION = 0xC0000035.toInt()
        const val STATUS_SHARING_VIOLATION = 0xC0000043.toInt()
        const val STATUS_LOGON_FAILURE = 0xC000006D.toInt()

        const val CAP_LARGE_FILES = 0x00000008
        const val CAP_NT_SMBS = 0x00000010
        const val CAP_STATUS32 = 0x00000040
        const val CAP_LARGE_READX = 0x00004000
        const val SERVER_CAPS = CAP_NT_SMBS or CAP_STATUS32 or CAP_LARGE_FILES or CAP_LARGE_READX

        const val FLAGS2 = 0x4001
        const val FLAGS1_REPLY = 0x80

        const val ATTR_DIRECTORY = 0x10
        const val ATTR_NORMAL = 0x80

        const val FILE_SUPERSEDE = 0
        const val FILE_OPEN = 1
        const val FILE_CREATE = 2
        const val FILE_OPEN_IF = 3
        const val FILE_OVERWRITE = 4
        const val FILE_OVERWRITE_IF = 5

        const val FILE_SUPERSEDED = 0
        const val FILE_OPENED = 1
        const val FILE_CREATED = 2
        const val FILE_OVERWRITTEN = 3

        const val FILE_DIRECTORY_FILE = 0x00000001

        const val TRANS2_FIND_FIRST2 = 0x01
        const val TRANS2_FIND_NEXT2 = 0x02
        const val TRANS2_QUERY_PATH_INFORMATION = 0x05

        const val SMB_QUERY_FILE_BASIC_INFO = 0x0101
        const val SMB_QUERY_FILE_STANDARD_INFO = 0x0102
        const val FIND_BATCH_MAX = 2048

        private val PRE_LOGON_COMMANDS = setOf(
            SMB_COM_NEGOTIATE,
            SMB_COM_SESSION_SETUP_ANDX,
            SMB_COM_ECHO,
            SMB_COM_LOGOFF_ANDX
        )

        fun toFiletime(unixMs: Long): Long {
            if (unixMs <= 0L) return 0L
            return unixMs * 10_000L + 116444736000000000L
        }
    }

    override val key: String = "smbv1"
    override val displayName: String = "SMBv1"

    private val effectiveReadOnly = readOnly || vfs.isReadOnly
    private val normalizedShareName = shareName.trim().ifEmpty { "games" }
    private val requireAuth = password.isNotEmpty()
    private val compressionExts = listOf(".cso", ".ciso")

    private val running = AtomicBoolean(false)
    private var serverSocket: ServerSocket? = null
    private var acceptThread: Thread? = null
    private var workerPool: ExecutorService? = null
    private val mutateLock = Any()

    @Volatile
    var actualPort: Int = 0
        private set

    override val boundPort: Int
        get() = actualPort

    override val isRunning: Boolean
        get() = running.get()

    private class OpenFile(
        val relPath: String,
        val isDir: Boolean,
        val size: Long,
        val lastModMs: Long,
        val ra: VfsRandomAccess?,
        val compressed: Boolean,
        val writable: Boolean
    ) {
        fun close() {
            try {
                ra?.close()
            } catch (_: Exception) {
            }
        }
    }

    private class ListedItem(
        val name: String,
        val relPath: String,
        val isDir: Boolean,
        val size: Long,
        val mtimeMs: Long
    )

    private class SearchState(val entries: List<ListedItem>, var cursor: Int)

    private inner class ConnState(val sock: Socket) {
        var uid = 0
        var nextFid = 0x1000
        var nextTid = 0x0001
        var nextSid = 0x0001
        val trees = HashMap<Int, String>() // tid -> "SHARE" or "IPC"
        val files = ConcurrentHashMap<Int, OpenFile>()
        val searches = HashMap<Int, SearchState>()
        var loggedOn = false

        fun allocFid(): Int {
            nextFid = (nextFid + 1) and 0xFFFF
            if (nextFid == 0) nextFid = 0x1000
            return nextFid
        }

        fun allocTid(): Int {
            nextTid = (nextTid + 1) and 0xFFFF
            if (nextTid == 0) nextTid = 0x0001
            return nextTid
        }

        fun allocSid(): Int {
            nextSid = (nextSid + 1) and 0xFFFF
            if (nextSid == 0) nextSid = 0x0001
            return nextSid
        }

        fun closeAll() {
            for (f in files.values) f.close()
            files.clear()
        }
    }

    private class ParsedReq(val msg: ByteArray) {
        val cmd = msg[4].toInt() and 0xFF
        val tid = u16(msg, 24)
        val pid = u16(msg, 26)
        val uid = u16(msg, 28)
        val mid = u16(msg, 30)
        val wc = msg[32].toInt() and 0xFF
        val params: ByteArray
        val data: ByteArray

        init {
            val pEnd = minOf(msg.size, 33 + wc * 2)
            params = msg.copyOfRange(33, pEnd)
            val bcOff = 33 + wc * 2
            val bc = if (bcOff + 2 <= msg.size) u16(msg, bcOff) else 0
            val dStart = minOf(msg.size, bcOff + 2)
            val dEnd = minOf(msg.size, dStart + bc)
            data = msg.copyOfRange(dStart, dEnd)
        }

        private fun u16(b: ByteArray, off: Int): Int =
            (b[off].toInt() and 0xFF) or ((b[off + 1].toInt() and 0xFF) shl 8)
    }

    override fun start() {
        if (!running.compareAndSet(false, true)) return
        val requestedPort = port
        val requestedBindIp = bindIp.trim()
        ServerPortGuard.ensureBindable(requestedPort, redirect445Enabled)
        try {
            val addr = if (requestedBindIp.isEmpty()) {
                InetSocketAddress(requestedPort)
            } else {
                InetSocketAddress(InetAddress.getByName(requestedBindIp), requestedPort)
            }
            val ss = ServerSocket().apply {
                reuseAddress = true
                bind(addr, 16)
            }
            serverSocket = ss
            actualPort = ss.localPort
            workerPool = Executors.newCachedThreadPool { r ->
                Thread(r, "ps2-smbv1-conn").apply { isDaemon = true }
            }
            val authNote = if (requireAuth) "user=$username" else "guest"
            log("SMBv1 listening on ${requestedBindIp.ifEmpty { "0.0.0.0" }}:$actualPort share=$normalizedShareName ($authNote, ${if (effectiveReadOnly) "read-only" else "writable"})")
            acceptThread = Thread({ acceptLoop(ss) }, "ps2-smbv1-accept").apply {
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
                workerPool?.submit { serveClient(client) } ?: client.close()
            } catch (_: SocketException) {
                break
            } catch (e: IOException) {
                if (running.get()) log("SMBv1 accept error: ${e.message}")
            }
        }
    }

    private fun serveClient(sock: Socket) {
        val conn = ConnState(sock)
        try {
            sock.tcpNoDelay = true
            val inp = BufferedInputStream(sock.getInputStream(), 16384)
            val out = sock.getOutputStream()
            while (running.get()) {
                val msg = readNetbiosMessage(inp) ?: break
                if (msg.isEmpty()) continue
                if (msg.size < 33 || !msg.copyOfRange(0, 4).contentEquals(SMB_MAGIC)) continue
                val r = ParsedReq(msg)
                if (requireAuth && !conn.loggedOn && r.cmd !in PRE_LOGON_COMMANDS) {
                    sendSmbReply(out, r.cmd, STATUS_ACCESS_DENIED, r.tid, r.pid, r.uid, r.mid, ByteArray(0), ByteArray(0))
                    continue
                }
                dispatch(conn, r, out)
            }
        } catch (_: EOFException) {
        } catch (_: SocketException) {
        } catch (e: Exception) {
            if (running.get() && verbose) log("SMBv1 client error: ${e.message}")
        } finally {
            conn.closeAll()
            try {
                sock.close()
            } catch (_: Exception) {
            }
        }
    }

    private fun readNetbiosMessage(inp: BufferedInputStream): ByteArray? {
        val hdr = readExact(inp, 4) ?: return null
        val len = ((hdr[1].toInt() and 0xFF) shl 16) or
            ((hdr[2].toInt() and 0xFF) shl 8) or
            (hdr[3].toInt() and 0xFF)
        if (hdr[0].toInt() != 0x00) {
            if (len > 0) readExact(inp, len) ?: return null
            return ByteArray(0)
        }
        if (len == 0) return ByteArray(0)
        return readExact(inp, len)
    }

    private fun readExact(inp: BufferedInputStream, n: Int): ByteArray? {
        val out = ByteArray(n)
        var off = 0
        while (off < n) {
            val got = inp.read(out, off, n - off)
            if (got < 0) return null
            off += got
        }
        return out
    }

    private fun sendSmbReply(
        out: OutputStream,
        cmd: Int,
        status: Int,
        tid: Int,
        pid: Int,
        uid: Int,
        mid: Int,
        params: ByteArray,
        data: ByteArray
    ) {
        val bodyLen = 1 + params.size + 2 + data.size
        val smbLen = 32 + bodyLen
        val frame = ByteArray(4 + smbLen)
        frame[0] = 0
        frame[1] = ((smbLen ushr 16) and 0xFF).toByte()
        frame[2] = ((smbLen ushr 8) and 0xFF).toByte()
        frame[3] = (smbLen and 0xFF).toByte()

        System.arraycopy(SMB_MAGIC, 0, frame, 4, 4)
        frame[8] = cmd.toByte()
        frame[9] = (status and 0xFF).toByte()
        frame[10] = 0
        frame[11] = ((status ushr 16) and 0xFF).toByte()
        frame[12] = ((status ushr 24) and 0xFF).toByte()
        frame[13] = FLAGS1_REPLY.toByte()
        val hdrBb = ByteBuffer.wrap(frame, 14, 22).order(ByteOrder.LITTLE_ENDIAN)
        hdrBb.putShort(FLAGS2.toShort())
        // 12 reserved bytes at 16..27 remain 0
        val idsBb = ByteBuffer.wrap(frame, 28, 8).order(ByteOrder.LITTLE_ENDIAN)
        idsBb.putShort(tid.toShort())
        idsBb.putShort(pid.toShort())
        idsBb.putShort(uid.toShort())
        idsBb.putShort(mid.toShort())

        frame[36] = (params.size / 2).toByte()
        if (params.isNotEmpty()) System.arraycopy(params, 0, frame, 37, params.size)
        val bcOff = 37 + params.size
        frame[bcOff] = (data.size and 0xFF).toByte()
        frame[bcOff + 1] = ((data.size ushr 8) and 0xFF).toByte()
        if (data.isNotEmpty()) System.arraycopy(data, 0, frame, bcOff + 2, data.size)

        out.write(frame)
        out.flush()
    }

    private fun dispatch(conn: ConnState, r: ParsedReq, out: OutputStream) {
        var uidOut = r.uid
        var tidOut = r.tid
        val statusAndPayload: Triple<Int, ByteArray, ByteArray> = when (r.cmd) {
            SMB_COM_NEGOTIATE -> handleNegotiate(r)
            SMB_COM_SESSION_SETUP_ANDX -> {
                val (res, nextUid) = handleSessionSetup(conn, r)
                uidOut = nextUid
                res
            }
            SMB_COM_TREE_CONNECT_ANDX -> {
                val (res, nextTid) = handleTreeConnect(conn, r)
                tidOut = nextTid
                res
            }
            SMB_COM_TREE_DISCONNECT -> {
                conn.trees.remove(r.tid)
                Triple(STATUS_SUCCESS, ByteArray(0), ByteArray(0))
            }
            SMB_COM_LOGOFF_ANDX -> {
                conn.loggedOn = false
                val p = byteArrayOf(SMB_COM_NONE.toByte(), 0, 0, 0)
                Triple(STATUS_SUCCESS, p, ByteArray(0))
            }
            SMB_COM_OPEN_ANDX -> handleOpenAndx(conn, r)
            SMB_COM_NT_CREATE_ANDX -> handleNtCreateAndx(conn, r)
            SMB_COM_READ_ANDX -> handleReadAndx(conn, r)
            SMB_COM_WRITE_ANDX -> handleWriteAndx(conn, r)
            SMB_COM_CLOSE -> handleClose(conn, r)
            SMB_COM_ECHO -> {
                val p = byteArrayOf(1, 0)
                Triple(STATUS_SUCCESS, p, r.data)
            }
            SMB_COM_QUERY_INFORMATION_DISK -> {
                val p = ByteBuffer.allocate(10).order(ByteOrder.LITTLE_ENDIAN)
                    .putShort(0xFFFF.toShort())
                    .putShort(64.toShort())
                    .putShort(512.toShort())
                    .putShort(0xFFFF.toShort())
                    .putShort(0)
                    .array()
                Triple(STATUS_SUCCESS, p, ByteArray(0))
            }
            SMB_COM_CHECK_DIRECTORY -> handleCheckDirectory(conn, r)
            SMB_COM_CREATE_DIRECTORY -> handleCreateDirectory(conn, r)
            SMB_COM_DELETE -> handleDelete(conn, r)
            SMB_COM_DELETE_DIRECTORY -> handleDeleteDirectory(conn, r)
            SMB_COM_RENAME -> handleRename(conn, r)
            SMB_COM_TRANS2 -> handleTrans2(conn, r)
            SMB_COM_TRANSACTION -> handleTransaction(r)
            else -> Triple(STATUS_NOT_IMPLEMENTED, ByteArray(0), ByteArray(0))
        }
        val (status, params, data) = statusAndPayload
        if (status != STATUS_SUCCESS) {
            sendSmbReply(out, r.cmd, status, r.tid, r.pid, r.uid, r.mid, ByteArray(0), ByteArray(0))
        } else {
            sendSmbReply(out, r.cmd, status, tidOut, r.pid, uidOut, r.mid, params, data)
        }
    }

    private fun handleNegotiate(r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        val dialects = ArrayList<String>()
        var i = 0
        val d = r.data
        while (i < d.size) {
            if (d[i].toInt() == 0x02) {
                var end = i + 1
                while (end < d.size && d[end].toInt() != 0) end++
                dialects.add(String(d, i + 1, end - (i + 1), Charsets.US_ASCII))
                i = end + 1
            } else {
                i++
            }
        }
        val idx = dialects.indexOf("NT LM 0.12").let { if (it >= 0) it else 0 }
        val params = ByteBuffer.allocate(34).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(idx.toShort())
            .put(0.toByte()) // share-level + plaintext (guest)
            .putShort(1.toShort())
            .putShort(1.toShort())
            .putInt(65535)
            .putInt(65536)
            .putInt(0)
            .putInt(SERVER_CAPS)
            .putLong(0L)
            .putShort(0.toShort())
            .put(0.toByte())
            .array()
        val data = "WORKGROUP\u0000".toByteArray(Charsets.US_ASCII)
        return Triple(STATUS_SUCCESS, params, data)
    }

    private fun handleSessionSetup(conn: ConnState, r: ParsedReq): Pair<Triple<Int, ByteArray, ByteArray>, Int> {
        var action = 0x0001
        if (requireAuth) {
            val (acct, pw) = parseSessionSetup(r)
            val okUser = acct.equals(username, ignoreCase = true)
            val okPw = MessageDigest.isEqual(pw.toByteArray(Charsets.UTF_8), password.toByteArray(Charsets.UTF_8))
            if (!okUser || !okPw) {
                conn.loggedOn = false
                return Triple(STATUS_LOGON_FAILURE, ByteArray(0), ByteArray(0)) to r.uid
            }
            action = 0x0000
        }
        conn.loggedOn = true
        if (conn.uid == 0) conn.uid = 0x0800
        val params = ByteBuffer.allocate(6).order(ByteOrder.LITTLE_ENDIAN)
            .put(SMB_COM_NONE.toByte())
            .put(0.toByte())
            .putShort(0)
            .putShort(action.toShort())
            .array()
        val data = "OPLSMB\u0000OPLSMB\u0000".toByteArray(Charsets.US_ASCII)
        return Triple(STATUS_SUCCESS, params, data) to conn.uid
    }

    private fun parseSessionSetup(r: ParsedReq): Pair<String, String> {
        if (r.wc < 13 || r.params.size < 18) return "" to ""
        val bb = ByteBuffer.wrap(r.params).order(ByteOrder.LITTLE_ENDIAN)
        val ansiLen = bb.getShort(14).toInt() and 0xFFFF
        val uniLen = bb.getShort(16).toInt() and 0xFFFF
        val d = r.data
        val ansiEnd = minOf(d.size, ansiLen)
        val uniEnd = minOf(d.size, ansiEnd + uniLen)
        val ansiPw = String(d, 0, ansiEnd, Charsets.US_ASCII).trimEnd('\u0000')
        val uniPw = if (uniEnd > ansiEnd) String(d, ansiEnd, uniEnd - ansiEnd, Charsets.UTF_16LE).trimEnd('\u0000') else ""
        var end = uniEnd
        while (end < d.size && d[end].toInt() != 0) end++
        val account = if (end > uniEnd) String(d, uniEnd, end - uniEnd, Charsets.US_ASCII) else ""
        return account to (ansiPw.ifEmpty { uniPw })
    }

    private fun handleTreeConnect(conn: ConnState, r: ParsedReq): Pair<Triple<Int, ByteArray, ByteArray>, Int> {
        val pwLen = if (r.wc >= 4 && r.params.size >= 8) {
            ByteBuffer.wrap(r.params).order(ByteOrder.LITTLE_ENDIAN).getShort(6).toInt() and 0xFFFF
        } else 1
        val d = r.data
        val off = minOf(d.size, pwLen)
        var end = off
        while (end < d.size && d[end].toInt() != 0) end++
        val rawPath = String(d, off, end - off, Charsets.US_ASCII)
        val requested = rawPath.replace('/', '\\').trimEnd('\\').substringAfterLast('\\')
        val tid = conn.allocTid()
        val service: String
        if (requested.equals("IPC$", ignoreCase = true)) {
            conn.trees[tid] = "IPC"
            service = "IPC\u0000"
        } else {
            // Lenient single-share acceptance (OPL sometimes sends odd share casings/names).
            conn.trees[tid] = "SHARE"
            service = "A:\u0000"
        }
        val params = ByteBuffer.allocate(6).order(ByteOrder.LITTLE_ENDIAN)
            .put(SMB_COM_NONE.toByte())
            .put(0.toByte())
            .putShort(0)
            .putShort(0x0001.toShort())
            .array()
        val data = (service + "NTFS\u0000").toByteArray(Charsets.US_ASCII)
        return Triple(STATUS_SUCCESS, params, data) to tid
    }

    private fun isDiskTree(conn: ConnState, r: ParsedReq): Boolean = conn.trees[r.tid] == "SHARE"

    private fun resolveRel(raw: String): String? {
        return try {
            PathGuard.normalizeRelative(raw, strictWindowsNames = false)
        } catch (_: VfsPathException) {
            null
        }
    }

    private fun openCompressedIfEligible(relPath: String): Pair<VfsEntry, VfsRandomAccess>? {
        if (!enableCompression || !relPath.lowercase(Locale.US).endsWith(".iso")) return null
        val stem = relPath.substring(0, relPath.length - 4)
        val slash = stem.lastIndexOf('/')
        val parent = if (slash >= 0) stem.substring(0, slash) else ""
        val baseStem = if (slash >= 0) stem.substring(slash + 1) else stem
        val listing = runCatching { vfs.list(parent) }.getOrDefault(emptyList())
        for (ext in compressionExts) {
            val match = listing.firstOrNull { !it.isDirectory && it.name.equals("$baseStem$ext", ignoreCase = true) }
            if (match != null) {
                val raw = vfs.openRead(match.relativePath)
                return try {
                    match to CompressedIso.open(match.name, raw, cacheSize = 8)
                } catch (_: Exception) {
                    raw.close()
                    null
                }
            }
        }
        return null
    }

    private fun handleOpenAndx(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        if (!isDiskTree(conn, r)) return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val end = r.data.indexOf(0).let { if (it < 0) r.data.size else it }
        val name = String(r.data, 0, end, Charsets.US_ASCII)
        val rel = resolveRel(name) ?: return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        val entry = vfs.stat(rel)
        val openFile: OpenFile
        if (entry != null && !entry.isDirectory) {
            openFile = OpenFile(rel, false, entry.size, entry.lastModifiedMs, vfs.openRead(rel), compressed = false, writable = false)
        } else {
            val comp = openCompressedIfEligible(rel)
                ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
            openFile = OpenFile(comp.first.relativePath, false, comp.second.size(), comp.first.lastModifiedMs, comp.second, compressed = true, writable = false)
        }
        val fid = conn.allocFid()
        conn.files[fid] = openFile
        val params = ByteBuffer.allocate(30).order(ByteOrder.LITTLE_ENDIAN)
            .put(SMB_COM_NONE.toByte()).put(0).putShort(0)
            .putShort(fid.toShort())
            .putShort(0)
            .putInt(0)
            .putInt((openFile.size and 0xFFFFFFFFL).toInt())
            .putShort(0).putShort(0).putShort(0)
            .putShort(0x0001.toShort())
            .putInt(0).putShort(0)
            .array()
        return Triple(STATUS_SUCCESS, params, ByteArray(0))
    }

    private fun handleNtCreateAndx(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        if (!isDiskTree(conn, r)) return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        if (r.params.size < 39) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        val pb = ByteBuffer.wrap(r.params).order(ByteOrder.LITTLE_ENDIAN)
        val nameLen = pb.getShort(5).toInt() and 0xFFFF
        val disposition = pb.getInt(35)
        val createOptions = if (r.params.size >= 43) pb.getInt(39) else 0
        if (disposition > FILE_OVERWRITE_IF) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))

        val rawData = if (r.data.isNotEmpty() && r.data[0].toInt() == 0) r.data.copyOfRange(1, r.data.size) else r.data
        val sliceLen = minOf(nameLen, rawData.size)
        val nulIdx = (0 until sliceLen).firstOrNull { rawData[it].toInt() == 0 } ?: sliceLen
        val name = String(rawData, 0, nulIdx, Charsets.US_ASCII)
        val rel = resolveRel(name) ?: return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))

        val requestedDir = (createOptions and FILE_DIRECTORY_FILE) != 0
        val existing = vfs.stat(rel)
        var action = FILE_OPENED
        var compressedPair: Pair<VfsEntry, VfsRandomAccess>? = null
        if (existing == null) {
            compressedPair = openCompressedIfEligible(rel)
        }

        if (compressedPair != null) {
            if (disposition == FILE_CREATE) {
                compressedPair.second.close()
                return Triple(STATUS_OBJECT_NAME_COLLISION, ByteArray(0), ByteArray(0))
            }
            if (requestedDir || disposition in setOf(FILE_SUPERSEDE, FILE_OVERWRITE, FILE_OVERWRITE_IF)) {
                compressedPair.second.close()
                return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
            }
        } else if (existing != null) {
            if (disposition == FILE_CREATE) return Triple(STATUS_OBJECT_NAME_COLLISION, ByteArray(0), ByteArray(0))
            if (requestedDir && !existing.isDirectory) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
            if (disposition in setOf(FILE_SUPERSEDE, FILE_OVERWRITE, FILE_OVERWRITE_IF)) {
                if (effectiveReadOnly || existing.isDirectory) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
                vfs.openWrite(rel, create = true, truncate = true).close()
                action = if (disposition == FILE_SUPERSEDE) FILE_SUPERSEDED else FILE_OVERWRITTEN
            }
        } else {
            if (disposition == FILE_OPEN || disposition == FILE_OVERWRITE) {
                return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
            }
            if (effectiveReadOnly) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
            val ok = if (requestedDir) {
                vfs.mkdir(rel)
            } else {
                try {
                    vfs.openWrite(rel, create = true, truncate = true).close()
                    true
                } catch (_: Exception) {
                    false
                }
            }
            if (!ok) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
            action = FILE_CREATED
        }

        val openFile: OpenFile
        if (compressedPair != null) {
            val (ce, cra) = compressedPair
            openFile = OpenFile(ce.relativePath, false, cra.size(), ce.lastModifiedMs, cra, compressed = true, writable = false)
        } else {
            val latest = vfs.stat(rel) ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
            val ra = if (latest.isDirectory) null else {
                if (!effectiveReadOnly) runCatching { vfs.openWrite(rel, create = false, truncate = false) }.getOrNull()
                    ?: vfs.openRead(rel)
                else vfs.openRead(rel)
            }
            val writableHandle = !effectiveReadOnly && !latest.isDirectory
            openFile = OpenFile(rel, latest.isDirectory, latest.size, latest.lastModifiedMs, ra, compressed = false, writable = writableHandle)
        }

        val fid = conn.allocFid()
        conn.files[fid] = openFile
        val mtime = toFiletime(openFile.lastModMs)
        val params = ByteBuffer.allocate(68).order(ByteOrder.LITTLE_ENDIAN)
            .put(SMB_COM_NONE.toByte()).put(0).putShort(0)
            .put(0)
            .putShort(fid.toShort())
            .putInt(action)
            .putLong(mtime).putLong(mtime).putLong(mtime).putLong(mtime)
            .putInt(if (openFile.isDir) ATTR_DIRECTORY else ATTR_NORMAL)
            .putLong(openFile.size)
            .putLong(openFile.size)
            .putShort(0).putShort(0)
            .put(if (openFile.isDir) 1 else 0)
            .array()
        return Triple(STATUS_SUCCESS, params, ByteArray(0))
    }

    private fun handleReadAndx(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        val pb = ByteBuffer.wrap(r.params).order(ByteOrder.LITTLE_ENDIAN)
        val fid = pb.getShort(4).toInt() and 0xFFFF
        val offLow = pb.getInt(6).toLong() and 0xFFFFFFFFL
        val maxLow = pb.getShort(10).toInt() and 0xFFFF
        val maxHigh = pb.getShort(14).toInt() and 0xFFFF
        val offHigh = if (r.params.size >= 24) (pb.getInt(20).toLong() and 0xFFFFFFFFL) else 0L
        val offset = offLow or (offHigh shl 32)
        val maxCount = minOf(maxLow or (maxHigh shl 16), 0xFFFF)

        val of = conn.files[fid] ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val ra = of.ra ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val buf = ByteArray(maxCount)
        var got = 0
        while (got < maxCount) {
            val n = ra.readAt(offset + got, buf, got, maxCount - got)
            if (n <= 0) break
            got += n
        }
        val chunk = if (got == buf.size) buf else buf.copyOf(got)
        val params = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
            .put(SMB_COM_NONE.toByte()).put(0).putShort(0)
            .putShort(0).putShort(0).putShort(0)
            .putShort((got and 0xFFFF).toShort())
            .putShort(59.toShort()) // DATA_OFFSET
            .putInt(got ushr 16)
            .put(ByteArray(6))
            .array()
        return Triple(STATUS_SUCCESS, params, chunk)
    }

    private fun handleWriteAndx(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        if (effectiveReadOnly) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        val pb = ByteBuffer.wrap(r.params).order(ByteOrder.LITTLE_ENDIAN)
        val fid = pb.getShort(4).toInt() and 0xFFFF
        val offLow = pb.getInt(6).toLong() and 0xFFFFFFFFL
        val lenHigh = pb.getShort(18).toInt() and 0xFFFF
        val lenLow = pb.getShort(20).toInt() and 0xFFFF
        val dataOff = pb.getShort(22).toInt() and 0xFFFF
        val offHigh = if (r.params.size >= 28) (pb.getInt(24).toLong() and 0xFFFFFFFFL) else 0L
        val offset = offLow or (offHigh shl 32)
        val count = minOf(lenLow or (lenHigh shl 16), r.msg.size - dataOff).coerceAtLeast(0)

        val of = conn.files[fid] ?: return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        if (of.compressed || of.isDir || of.ra == null || !of.writable) {
            return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        }
        return try {
            var written = 0
            while (written < count) {
                val n = of.ra.writeAt(offset + written, r.msg, dataOff + written, count - written)
                if (n <= 0) break
                written += n
            }
            of.ra.flush()
            val params = ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN)
                .put(SMB_COM_NONE.toByte()).put(0).putShort(0)
                .putShort((written and 0xFFFF).toShort())
                .putShort(0).putShort(0).putShort(0)
                .array()
            Triple(STATUS_SUCCESS, params, ByteArray(0))
        } catch (_: Exception) {
            Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        }
    }

    private fun handleClose(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        val fid = if (r.params.size >= 2) ByteBuffer.wrap(r.params).order(ByteOrder.LITTLE_ENDIAN).getShort(0).toInt() and 0xFFFF else 0
        conn.files.remove(fid)?.close()
        return Triple(STATUS_SUCCESS, ByteArray(0), ByteArray(0))
    }

    private fun handleCheckDirectory(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        if (!isDiskTree(conn, r)) return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val end = r.data.indexOf(0).let { if (it < 0) r.data.size else it }
        val rel = resolveRel(String(r.data, 0, end, Charsets.US_ASCII)) ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val entry = vfs.stat(rel)
        return if (entry != null && entry.isDirectory) {
            Triple(STATUS_SUCCESS, ByteArray(0), ByteArray(0))
        } else {
            Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        }
    }

    private fun handleCreateDirectory(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        if (effectiveReadOnly || !isDiskTree(conn, r)) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        val raw = if (r.data.isNotEmpty() && r.data[0].toInt() == 0x04) r.data.copyOfRange(1, r.data.size) else r.data
        val end = raw.indexOf(0).let { if (it < 0) raw.size else it }
        val rel = resolveRel(String(raw, 0, end, Charsets.US_ASCII)) ?: return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        if (vfs.stat(rel) != null) return Triple(STATUS_OBJECT_NAME_COLLISION, ByteArray(0), ByteArray(0))
        return if (vfs.mkdir(rel)) Triple(STATUS_SUCCESS, ByteArray(0), ByteArray(0)) else Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
    }

    private fun parseBufferNames(data: ByteArray): List<String> {
        val out = ArrayList<String>()
        var i = 0
        while (i < data.size && data[i].toInt() == 0x04) {
            i++
            var end = i
            while (end < data.size && data[end].toInt() != 0) end++
            out.add(String(data, i, end - i, Charsets.US_ASCII))
            i = if (end < data.size) end + 1 else end
        }
        return out
    }

    private fun isInUse(conn: ConnState, rel: String): Boolean {
        val norm = rel.lowercase(Locale.US)
        for (of in conn.files.values) {
            val p = of.relPath.lowercase(Locale.US)
            if (p == norm || (norm.isNotEmpty() && p.startsWith("$norm/"))) return true
        }
        return false
    }

    private fun handleDelete(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        if (effectiveReadOnly || !isDiskTree(conn, r)) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        val names = parseBufferNames(r.data)
        if (names.isEmpty()) return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val rel = resolveRel(names[0]) ?: return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        if (rel.isEmpty()) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        synchronized(mutateLock) {
            val entry = vfs.stat(rel) ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
            if (entry.isDirectory) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
            if (isInUse(conn, rel)) return Triple(STATUS_SHARING_VIOLATION, ByteArray(0), ByteArray(0))
            return if (vfs.deleteFile(rel)) Triple(STATUS_SUCCESS, ByteArray(0), ByteArray(0)) else Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        }
    }

    private fun handleDeleteDirectory(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        if (effectiveReadOnly || !isDiskTree(conn, r)) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        val names = parseBufferNames(r.data)
        if (names.isEmpty()) return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val rel = resolveRel(names[0]) ?: return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        if (rel.isEmpty()) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        synchronized(mutateLock) {
            val entry = vfs.stat(rel) ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
            if (!entry.isDirectory) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
            if (isInUse(conn, rel)) return Triple(STATUS_SHARING_VIOLATION, ByteArray(0), ByteArray(0))
            return if (vfs.deleteDirectory(rel)) Triple(STATUS_SUCCESS, ByteArray(0), ByteArray(0)) else Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        }
    }

    private fun handleRename(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        if (effectiveReadOnly || !isDiskTree(conn, r)) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        val names = parseBufferNames(r.data)
        if (names.size < 2) return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val oldRel = resolveRel(names[0]) ?: return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        val newRel = resolveRel(names[1]) ?: return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        if (oldRel.isEmpty() || newRel.isEmpty()) return Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
        synchronized(mutateLock) {
            vfs.stat(oldRel) ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
            if (!oldRel.equals(newRel, ignoreCase = true) && vfs.stat(newRel) != null) {
                return Triple(STATUS_OBJECT_NAME_COLLISION, ByteArray(0), ByteArray(0))
            }
            if (isInUse(conn, oldRel)) return Triple(STATUS_SHARING_VIOLATION, ByteArray(0), ByteArray(0))
            return if (vfs.rename(oldRel, newRel, replaceIfExists = false)) {
                Triple(STATUS_SUCCESS, ByteArray(0), ByteArray(0))
            } else {
                Triple(STATUS_ACCESS_DENIED, ByteArray(0), ByteArray(0))
            }
        }
    }

    private fun handleTrans2(conn: ConnState, r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        val pb = ByteBuffer.wrap(r.params).order(ByteOrder.LITTLE_ENDIAN)
        val paramCount = pb.getShort(18).toInt() and 0xFFFF
        val paramOff = pb.getShort(20).toInt() and 0xFFFF
        val setupCount = r.params[26].toInt() and 0xFF
        val setup = if (setupCount >= 1 && r.params.size >= 30) pb.getShort(28).toInt() and 0xFFFF else 0
        val tParams = r.msg.copyOfRange(paramOff, minOf(r.msg.size, paramOff + paramCount))

        return when (setup) {
            TRANS2_FIND_FIRST2 -> handleFindFirst2(conn, r, tParams)
            TRANS2_FIND_NEXT2 -> handleFindNext2(conn, r, tParams)
            TRANS2_QUERY_PATH_INFORMATION -> handleQueryPath(conn, tParams)
            else -> Triple(STATUS_NOT_IMPLEMENTED, ByteArray(0), ByteArray(0))
        }
    }

    private fun buildTrans2Reply(tParams: ByteArray, tData: ByteArray): Triple<Int, ByteArray, ByteArray> {
        val base = 32 + 1 + 20 + 2 // 55
        val paramOff = (base + 3) and 3.inv()
        val pad1 = paramOff - base
        val dataStart = paramOff + tParams.size
        val dataOff = (dataStart + 3) and 3.inv()
        val pad2 = dataOff - dataStart
        val params = ByteBuffer.allocate(20).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(tParams.size.toShort())
            .putShort(tData.size.toShort())
            .putShort(0)
            .putShort(tParams.size.toShort())
            .putShort(paramOff.toShort())
            .putShort(0)
            .putShort(tData.size.toShort())
            .putShort(dataOff.toShort())
            .putShort(0)
            .put(0).put(0)
            .array()
        val body = ByteArray(pad1 + tParams.size + pad2 + tData.size)
        System.arraycopy(tParams, 0, body, pad1, tParams.size)
        System.arraycopy(tData, 0, body, pad1 + tParams.size + pad2, tData.size)
        return Triple(STATUS_SUCCESS, params, body)
    }

    private fun listDirEntries(relDir: String): List<ListedItem> {
        val dirEntry = vfs.stat(relDir) ?: return emptyList()
        val raw = vfs.list(relDir)
        val out = ArrayList<ListedItem>(raw.size + 2)
        out.add(ListedItem(".", relDir, true, 0L, dirEntry.lastModifiedMs))
        out.add(ListedItem("..", relDir, true, 0L, dirEntry.lastModifiedMs))

        val takenIso = raw.filter { !it.isDirectory && it.name.lowercase(Locale.US).endsWith(".iso") }
            .map { it.name.lowercase(Locale.US) }.toSet()
        val body = ArrayList<ListedItem>(raw.size)
        for (item in raw) {
            val dot = item.name.lastIndexOf('.')
            if (enableCompression && !item.isDirectory && dot > 0) {
                val stem = item.name.substring(0, dot)
                val ext = item.name.substring(dot).lowercase(Locale.US)
                if (ext in compressionExts && "$stem.iso".lowercase(Locale.US) !in takenIso) {
                    val info = runCatching { vfs.openRead(item.relativePath).use { CompressedIso.probeInfo(it) } }.getOrNull()
                    if (info != null) {
                        body.add(ListedItem("$stem.iso", item.relativePath, false, info, item.lastModifiedMs))
                        continue
                    }
                }
            }
            body.add(ListedItem(item.name, item.relativePath, item.isDirectory, item.size, item.lastModifiedMs))
        }
        body.sortBy { it.name }
        out.addAll(body)
        return out
    }

    private fun packDirEntry104(item: ListedItem): ByteArray {
        val nb = item.name.toByteArray(Charsets.US_ASCII)
        val ft = toFiletime(item.mtimeMs)
        val sz = if (item.isDir) 0L else item.size
        val rec = ByteArray(94 + nb.size)
        val bb = ByteBuffer.wrap(rec).order(ByteOrder.LITTLE_ENDIAN)
        bb.putInt(0) // NextEntryOffset
        bb.putInt(0) // FileIndex
        bb.putLong(ft); bb.putLong(ft); bb.putLong(ft); bb.putLong(ft)
        bb.putLong(sz); bb.putLong(sz)
        bb.putInt(if (item.isDir) ATTR_DIRECTORY else ATTR_NORMAL)
        bb.putInt(nb.size)
        bb.putInt(0)
        rec[68] = 0
        rec[69] = 0
        System.arraycopy(nb, 0, rec, 94, nb.size)
        return rec
    }

    private fun findBatch(r: ParsedReq, entries: List<ListedItem>, cursor: Int, want: Int): Pair<ByteArray, Int> {
        val reqMaxData = if (r.params.size >= 8) {
            ByteBuffer.wrap(r.params).order(ByteOrder.LITTLE_ENDIAN).getShort(6).toInt() and 0xFFFF
        } else FIND_BATCH_MAX
        val maxData = minOf(reqMaxData, FIND_BATCH_MAX)
        val chunks = ArrayList<ByteArray>()
        var curLen = 0
        var lastStart = 0
        var count = 0
        val endIdx = minOf(entries.size, cursor + maxOf(want, 1))
        for (idx in cursor until endIdx) {
            val rec = packDirEntry104(entries[idx])
            val start = (curLen + 7) and 7.inv()
            if (count > 0 && start + rec.size > maxData) break
            if (count > 0 && start > curLen) {
                chunks.add(ByteArray(start - curLen))
            }
            if (count > 0) {
                // Patch previous record's NextEntryOffset directly later
            }
            chunks.add(rec)
            lastStart = start
            curLen = start + rec.size
            count++
        }
        val joined = ByteArray(curLen)
        var pos = 0
        val starts = ArrayList<Int>()
        for (c in chunks) {
            if (c.size >= 94) starts.add(pos)
            System.arraycopy(c, 0, joined, pos, c.size)
            pos += c.size
        }
        for (k in 0 until starts.size - 1) {
            val s = starts[k]
            val delta = starts[k + 1] - s
            ByteBuffer.wrap(joined, s, 4).order(ByteOrder.LITTLE_ENDIAN).putInt(delta)
        }
        return joined to count
    }

    private fun handleFindFirst2(conn: ConnState, r: ParsedReq, tParams: ByteArray): Triple<Int, ByteArray, ByteArray> {
        if (!isDiskTree(conn, r)) return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val end = tParams.indexOf(0, startIndex = 12).let { if (it < 0) tParams.size else it }
        var pattern = String(tParams, 12, (end - 12).coerceAtLeast(0), Charsets.US_ASCII).replace('/', '\\')
        if (pattern.endsWith("\\*")) pattern = pattern.substring(0, pattern.length - 2)
        else if (pattern.endsWith("*")) pattern = pattern.substring(0, pattern.length - 1)
        val rel = resolveRel(pattern) ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val dir = vfs.stat(rel)
        if (dir == null || !dir.isDirectory) return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        val entries = listDirEntries(rel)
        val sid = conn.allocSid()
        val want = if (tParams.size >= 4) {
            ByteBuffer.wrap(tParams).order(ByteOrder.LITTLE_ENDIAN).getShort(2).toInt() and 0xFFFF
        } else 1
        val (rd, count) = findBatch(r, entries, 0, want)
        val eos = if (count == entries.size) 1 else 0
        conn.searches[sid] = SearchState(entries, count)
        val rp = ByteBuffer.allocate(10).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(sid.toShort()).putShort(count.toShort()).putShort(eos.toShort()).putShort(0).putShort(0)
            .array()
        return buildTrans2Reply(rp, rd)
    }

    private fun ByteArray.indexOf(b: Byte, startIndex: Int): Int {
        for (i in startIndex until size) if (this[i] == b) return i
        return -1
    }

    private fun handleFindNext2(conn: ConnState, r: ParsedReq, tParams: ByteArray): Triple<Int, ByteArray, ByteArray> {
        val sid = ByteBuffer.wrap(tParams).order(ByteOrder.LITTLE_ENDIAN).getShort(0).toInt() and 0xFFFF
        val st = conn.searches[sid]
        if (st == null || st.cursor >= st.entries.size) {
            conn.searches.remove(sid)
            val rp = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putShort(0).putShort(1).putShort(0).putShort(0).array()
            return buildTrans2Reply(rp, ByteArray(0))
        }
        val want = if (tParams.size >= 4) {
            ByteBuffer.wrap(tParams).order(ByteOrder.LITTLE_ENDIAN).getShort(2).toInt() and 0xFFFF
        } else 1
        val (rd, count) = findBatch(r, st.entries, st.cursor, want)
        st.cursor += count
        val eos = if (st.cursor >= st.entries.size) 1 else 0
        val rp = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(count.toShort()).putShort(eos.toShort()).putShort(0).putShort(0)
            .array()
        return buildTrans2Reply(rp, rd)
    }

    private fun handleQueryPath(conn: ConnState, tParams: ByteArray): Triple<Int, ByteArray, ByteArray> {
        val level = ByteBuffer.wrap(tParams).order(ByteOrder.LITTLE_ENDIAN).getShort(0).toInt() and 0xFFFF
        val end = tParams.indexOf(0, startIndex = 6).let { if (it < 0) tParams.size else it }
        val name = String(tParams, 6, (end - 6).coerceAtLeast(0), Charsets.US_ASCII)
        val rel = resolveRel(name) ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
        var entry = vfs.stat(rel)
        var size = entry?.size ?: 0L
        if (entry == null) {
            val comp = openCompressedIfEligible(rel) ?: return Triple(STATUS_OBJECT_NAME_NOT_FOUND, ByteArray(0), ByteArray(0))
            entry = comp.first
            size = comp.second.use { it.size() }
        } else if (entry.isDirectory) {
            size = 0L
        }
        val ft = toFiletime(entry.lastModifiedMs)
        val rd = if (level == SMB_QUERY_FILE_STANDARD_INFO) {
            ByteBuffer.allocate(22).order(ByteOrder.LITTLE_ENDIAN)
                .putLong(size).putLong(size).putInt(0).put(0).put(if (entry.isDirectory) 1 else 0)
                .array()
        } else {
            ByteBuffer.allocate(40).order(ByteOrder.LITTLE_ENDIAN)
                .putLong(ft).putLong(ft).putLong(ft).putLong(ft)
                .putInt(if (entry.isDirectory) ATTR_DIRECTORY else ATTR_NORMAL).putInt(0)
                .array()
        }
        return buildTrans2Reply(ByteArray(0), rd)
    }

    private fun handleTransaction(r: ParsedReq): Triple<Int, ByteArray, ByteArray> {
        val name13 = normalizedShareName.toByteArray(Charsets.US_ASCII).let {
            ByteArray(13).apply { System.arraycopy(it, 0, this, 0, minOf(13, it.size)) }
        }
        val data = ByteArray(21)
        System.arraycopy(name13, 0, data, 0, 13)
        data[13] = 0
        ByteBuffer.wrap(data, 14, 6).order(ByteOrder.LITTLE_ENDIAN).putShort(0).putInt(20)
        data[20] = 0
        val rparams = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(0).putShort(0).putShort(1).putShort(1).array()
        val base = 55
        val paramOff = (base + 3) and 3.inv()
        val pad1 = paramOff - base
        val dataStart = paramOff + rparams.size
        val dataOff = (dataStart + 3) and 3.inv()
        val pad2 = dataOff - dataStart
        val params = ByteBuffer.allocate(20).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(rparams.size.toShort()).putShort(data.size.toShort()).putShort(0)
            .putShort(rparams.size.toShort()).putShort(paramOff.toShort()).putShort(0)
            .putShort(data.size.toShort()).putShort(dataOff.toShort()).putShort(0)
            .put(0).put(0)
            .array()
        val body = ByteArray(pad1 + rparams.size + pad2 + data.size)
        System.arraycopy(rparams, 0, body, pad1, rparams.size)
        System.arraycopy(data, 0, body, pad1 + rparams.size + pad2, data.size)
        return Triple(STATUS_SUCCESS, params, body)
    }
}
