package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.vfs.PathGuard
import com.nathanneurotic.ps2servers.vfs.VfsEntry
import com.nathanneurotic.ps2servers.vfs.VfsPathException
import com.nathanneurotic.ps2servers.vfs.VfsRandomAccess
import com.nathanneurotic.ps2servers.vfs.VfsRoot
import java.io.BufferedInputStream
import java.io.EOFException
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Pure-Kotlin SMB2/SMB3 server compatible with Windows 11 Explorer, Neutrino usmb2,
 * libsmb2 (RiptOPL/Launchstation), and smbclient.
 */
class Smb2ServerEngine(
    private val vfs: VfsRoot,
    override val key: String = "smbv2",
    private val ceiling: Int = DIALECT_210,
    private val shareName: String = "games",
    private val port: Int = DEFAULT_PORT,
    private val bindIp: String = "",
    private val username: String = "ps2",
    private val password: String = "",
    private val openShare: Boolean = false,
    private val readOnly: Boolean = false,
    private val redirect445Enabled: Boolean = false,
    private val verbose: Boolean = false,
    private val log: (String) -> Unit = {}
) : ServerEngine {

    companion object {
        const val DEFAULT_PORT = 1445
        const val DIALECT_202 = 0x0202
        const val DIALECT_210 = 0x0210
        const val DIALECT_300 = 0x0300
        const val DIALECT_302 = 0x0302

        private val SMB2_MAGIC = byteArrayOf(0xFE.toByte(), 'S'.code.toByte(), 'M'.code.toByte(), 'B'.code.toByte())
        private val SMB1_MAGIC = byteArrayOf(0xFF.toByte(), 'S'.code.toByte(), 'M'.code.toByte(), 'B'.code.toByte())
        private val COMPOUND_FILE_ID = ByteArray(16) { 0xFF.toByte() }

        const val CMD_NEGOTIATE = 0x0000
        const val CMD_SESSION_SETUP = 0x0001
        const val CMD_LOGOFF = 0x0002
        const val CMD_TREE_CONNECT = 0x0003
        const val CMD_TREE_DISCONNECT = 0x0004
        const val CMD_CREATE = 0x0005
        const val CMD_CLOSE = 0x0006
        const val CMD_FLUSH = 0x0007
        const val CMD_READ = 0x0008
        const val CMD_WRITE = 0x0009
        const val CMD_IOCTL = 0x000B
        const val CMD_CANCEL = 0x000C
        const val CMD_ECHO = 0x000D
        const val CMD_QUERY_DIRECTORY = 0x000E
        const val CMD_QUERY_INFO = 0x0010
        const val CMD_SET_INFO = 0x0011

        const val STATUS_SUCCESS = 0x00000000
        const val STATUS_BUFFER_OVERFLOW = 0x80000005.toInt()
        const val STATUS_NO_MORE_FILES = 0x80000006.toInt()
        const val STATUS_INFO_LENGTH_MISMATCH = 0xC0000004.toInt()
        const val STATUS_INVALID_PARAMETER = 0xC000000D.toInt()
        const val STATUS_INVALID_DEVICE_REQUEST = 0xC0000010.toInt()
        const val STATUS_END_OF_FILE = 0xC0000011.toInt()
        const val STATUS_MORE_PROCESSING_REQUIRED = 0xC0000016.toInt()
        const val STATUS_ACCESS_DENIED = 0xC0000022.toInt()
        const val STATUS_OBJECT_NAME_INVALID = 0xC0000033.toInt()
        const val STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034.toInt()
        const val STATUS_OBJECT_NAME_COLLISION = 0xC0000035.toInt()
        const val STATUS_OBJECT_PATH_SYNTAX_BAD = 0xC000003B.toInt()
        const val STATUS_NOT_SUPPORTED = 0xC00000BB.toInt()
        const val STATUS_FILE_IS_A_DIRECTORY = 0xC00000BA.toInt()
        const val STATUS_BAD_NETWORK_NAME = 0xC00000CC.toInt()
        const val STATUS_NETWORK_NAME_DELETED = 0xC00000C9.toInt()
        const val STATUS_PIPE_EMPTY = 0xC00000D9.toInt()
        const val STATUS_NOT_A_DIRECTORY = 0xC0000103.toInt()
        const val STATUS_DIRECTORY_NOT_EMPTY = 0xC0000101.toInt()
        const val STATUS_FILE_CLOSED = 0xC0000128.toInt()
        const val STATUS_USER_SESSION_DELETED = 0xC0000203.toInt()
        const val STATUS_MEDIA_WRITE_PROTECTED = 0xC00000A2.toInt()

        const val CREDIT_LIMIT = 512
        const val MAX_TRANSACT = 1 shl 20
        const val MAX_READ = 1 shl 20
        const val MAX_WRITE = 1 shl 20
    }

    override val displayName: String = if (ceiling >= DIALECT_300) "SMBv3" else "SMBv2"
    private val effectiveReadOnly = readOnly || vfs.isReadOnly
    private val normalizedShare = shareName.trim().ifEmpty { "games" }
    private val guid = Smb2Crypto.randomBytes(16)
    private val startTimeFt = Smbv1ServerEngine.toFiletime(System.currentTimeMillis())

    private val running = AtomicBoolean(false)
    private var serverSocket: ServerSocket? = null
    private var acceptThread: Thread? = null
    private var workerPool: ExecutorService? = null

    @Volatile
    var actualPort: Int = 0
        private set

    override val boundPort: Int
        get() = actualPort

    override val isRunning: Boolean
        get() = running.get()

    private class HandleState(
        val fileId: Long,
        val isIpc: Boolean,
        val relPath: String,
        val isDir: Boolean
    ) {
        var ra: VfsRandomAccess? = null
        var canWrite: Boolean = false
        var deleteOnClose: Boolean = false
        var pipe: SrvsvcPipe? = null
        var listing: List<String>? = null
        var cursor: Int = 0

        fun close() {
            try {
                ra?.close()
            } catch (_: Exception) {
            }
            ra = null
        }
    }

    private class HeaderParsed(val raw: ByteArray) {
        val bb = ByteBuffer.wrap(raw, 0, 64).order(ByteOrder.LITTLE_ENDIAN)
        val creditCharge = bb.getShort(6).toInt() and 0xFFFF
        val command = bb.getShort(12).toInt() and 0xFFFF
        val credits = bb.getShort(14).toInt() and 0xFFFF
        val flags = bb.getInt(16)
        val nextCommand = bb.getInt(20)
        val messageId = bb.getLong(24)
        var treeId = bb.getInt(36)
        var sessionId = bb.getLong(40)
    }

    private class NegClaims(val caps: Int, val guid: ByteArray, val mode: Int, val dialects: IntArray)

    private inner class ConnState(val sock: Socket) {
        var dialect: Int = 0
        var sessionId: Long = 0L
        var authenticated = false
        var challenge: ByteArray? = null
        var ntlmNeg: ByteArray? = null
        var ntlmChal: ByteArray? = null
        var signingKey: ByteArray? = null
        var signingRequired = false
        var negClaims: NegClaims? = null
        var terminate = false
        val trees = HashMap<Int, Boolean>() // tid -> isIpc
        val handles = ConcurrentHashMap<Long, HandleState>()
        var nextTree = 1
        var nextHandle = 1L
        var credits = 0
        var compoundFileId: ByteArray? = null

        fun addHandle(isIpc: Boolean, relPath: String, isDir: Boolean): HandleState {
            val hid = nextHandle++
            val h = HandleState(hid, isIpc, relPath, isDir)
            handles[hid] = h
            return h
        }

        fun resolveHandle(rawFid: ByteArray): HandleState {
            val fid = if (rawFid.contentEquals(COMPOUND_FILE_ID) && compoundFileId != null) compoundFileId!! else rawFid
            val bb = ByteBuffer.wrap(fid).order(ByteOrder.LITTLE_ENDIAN)
            val p = bb.getLong(0)
            val v = bb.getLong(8)
            val h = handles[v]
            if (h == null || p != v) throw Smb2Err(STATUS_FILE_CLOSED)
            return h
        }

        fun closeAll() {
            for (h in handles.values) h.close()
            handles.clear()
        }
    }

    private class Smb2Err(val status: Int, msg: String = "") : Exception(msg)

    override fun start() {
        if (!openShare && password.isEmpty()) {
            throw IOException("$displayName requires a password unless Open Share is enabled")
        }
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
                Thread(r, "ps2-smb2-conn").apply { isDaemon = true }
            }
            val authNote = if (openShare) "open/guest" else "user=$username"
            log("$displayName listening on ${requestedBindIp.ifEmpty { "0.0.0.0" }}:$actualPort share=$normalizedShare ($authNote, ${if (effectiveReadOnly) "read-only" else "writable"})")
            acceptThread = Thread({ acceptLoop(ss) }, "ps2-smb2-accept").apply {
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
                if (running.get()) log("$displayName accept error: ${e.message}")
            }
        }
    }

    private fun serveClient(sock: Socket) {
        val conn = ConnState(sock)
        try {
            sock.tcpNoDelay = true
            val inp = BufferedInputStream(sock.getInputStream(), 32768)
            while (running.get()) {
                val msg = readNetbios(inp) ?: break
                if (msg.isEmpty()) continue
                dispatchAll(conn, msg)
                if (conn.terminate) break
            }
        } catch (_: EOFException) {
        } catch (_: SocketException) {
        } catch (e: Exception) {
            if (running.get() && verbose) log("$displayName client error: ${e.message}")
        } finally {
            conn.closeAll()
            try {
                sock.close()
            } catch (_: Exception) {
            }
        }
    }

    private fun readNetbios(inp: BufferedInputStream): ByteArray? {
        val hdr = readExact(inp, 4) ?: return null
        val len = ((hdr[1].toInt() and 0xFF) shl 16) or ((hdr[2].toInt() and 0xFF) shl 8) or (hdr[3].toInt() and 0xFF)
        if (hdr[0].toInt() != 0x00) {
            if (len > 0) readExact(inp, len) ?: return null
            return ByteArray(0)
        }
        if (len == 0) return ByteArray(0)
        return readExact(inp, len)
    }

    private fun readExact(inp: BufferedInputStream, n: Int): ByteArray? {
        val buf = ByteArray(n)
        var off = 0
        while (off < n) {
            val k = inp.read(buf, off, n - off)
            if (k < 0) return null
            off += k
        }
        return buf
    }

    private fun sendNetbios(sock: Socket, msg: ByteArray) {
        val frame = ByteArray(4 + msg.size)
        frame[0] = 0
        frame[1] = ((msg.size ushr 16) and 0xFF).toByte()
        frame[2] = ((msg.size ushr 8) and 0xFF).toByte()
        frame[3] = (msg.size and 0xFF).toByte()
        System.arraycopy(msg, 0, frame, 4, msg.size)
        val out = sock.getOutputStream()
        out.write(frame)
        out.flush()
    }

    private data class ReplyPart(val hdr: HeaderParsed, val status: Int, val body: ByteArray, val treeIdOverride: Int?, val grant: Int)

    private fun dispatchAll(conn: ConnState, msg: ByteArray) {
        val replies = ArrayList<ReplyPart>()
        var offset = 0
        conn.compoundFileId = null
        var prevHdr: HeaderParsed? = null
        var prevStatus: Int = STATUS_SUCCESS

        while (offset < msg.size) {
            if (offset + 64 > msg.size || !msg.copyOfRange(offset, offset + 4).contentEquals(SMB2_MAGIC)) {
                if (offset == 0) handleSmb1Upgrade(conn, msg)
                return
            }
            val hdr = HeaderParsed(msg.copyOfRange(offset, offset + 64))
            val end = if (hdr.nextCommand > 0) offset + hdr.nextCommand else msg.size
            val body = msg.copyOfRange(offset + 64, minOf(msg.size, end))
            val related = (hdr.flags and 0x00000004) != 0 && prevHdr != null
            if (related) {
                if (hdr.sessionId == -1L) hdr.sessionId = prevHdr!!.sessionId
                if (hdr.treeId == -1) hdr.treeId = prevHdr!!.treeId
            }
            val sigRefusal = signatureCheck(conn, hdr, msg.copyOfRange(offset, minOf(msg.size, end)))
            val result: Triple<Int?, ByteArray, Int?> = when {
                related && prevStatus != STATUS_SUCCESS -> Triple(prevStatus, errorBody(), null)
                sigRefusal != null -> Triple(sigRefusal, errorBody(), null)
                else -> handleOne(conn, hdr, body)
            }
            val (status, replyBody, tidOverride) = result
            if (hdr.command == CMD_CREATE && status == STATUS_SUCCESS && replyBody.size >= 80) {
                conn.compoundFileId = replyBody.copyOfRange(64, 80)
            }
            if (tidOverride != null) hdr.treeId = tidOverride
            prevHdr = hdr
            prevStatus = status ?: STATUS_SUCCESS
            val grant = grantCredits(conn, hdr)
            if (status != null) {
                replies.add(ReplyPart(hdr, status, replyBody, tidOverride, grant))
            }
            if (hdr.nextCommand == 0) break
            offset = end
        }
        conn.compoundFileId = null
        if (replies.isEmpty()) return

        var out = ByteArray(0)
        for (i in replies.indices) {
            val part = replies[i]
            val last = i == replies.size - 1
            var bodyBytes = part.body
            var nextCmd = 0
            if (!last) {
                val total = 64 + bodyBytes.size
                val aligned = (total + 7) and 7.inv()
                if (aligned > total) bodyBytes = bodyBytes + ByteArray(aligned - total)
                nextCmd = aligned
            }
            var flags = if (i > 0 && (part.hdr.flags and 0x04) != 0) 0x04 else 0
            val shouldSign = conn.signingKey != null &&
                part.status != STATUS_MORE_PROCESSING_REQUIRED &&
                (conn.signingRequired || (part.hdr.flags and 0x08) != 0)
            if (shouldSign) flags = flags or 0x08
            val sid = if (conn.sessionId != 0L) conn.sessionId else part.hdr.sessionId
            val tid = part.treeIdOverride ?: part.hdr.treeId
            var piece = packHeader(part.hdr.command, part.status, part.hdr.messageId, tid, sid, part.grant, flags, nextCmd) + bodyBytes
            if (shouldSign) {
                piece = Smb2Crypto.applySignature(conn.signingKey!!, conn.dialect, piece)
            }
            out += piece
        }
        if (out.isNotEmpty()) sendNetbios(conn.sock, out)
    }

    private fun handleSmb1Upgrade(conn: ConnState, msg: ByteArray) {
        if (msg.size < 35 || !msg.copyOfRange(0, 4).contentEquals(SMB1_MAGIC) || (msg[4].toInt() and 0xFF) != 0x72) return
        val wc = msg[32].toInt() and 0xFF
        val at = 33 + wc * 2
        if (at + 2 > msg.size) return
        val bc = (msg[at].toInt() and 0xFF) or ((msg[at + 1].toInt() and 0xFF) shl 8)
        val blob = msg.copyOfRange(at + 2, minOf(msg.size, at + 2 + bc))
        val dialects = ArrayList<String>()
        var i = 0
        while (i < blob.size) {
            if (blob[i].toInt() == 0x02) {
                var end = i + 1
                while (end < blob.size && blob[end].toInt() != 0) end++
                dialects.add(String(blob, i + 1, end - (i + 1), Charsets.US_ASCII))
                i = end + 1
            } else i++
        }
        val dialect = when {
            "SMB 2.???" in dialects -> 0x02FF
            "SMB 2.002" in dialects && DIALECT_202 <= ceiling -> {
                conn.dialect = DIALECT_202
                DIALECT_202
            }
            else -> return
        }
        conn.credits++
        val pkt = packHeader(CMD_NEGOTIATE, STATUS_SUCCESS, 0L, 0, 0L, 1, 0, 0) + negotiateResponse(dialect)
        sendNetbios(conn.sock, pkt)
    }

    private fun signatureCheck(conn: ConnState, hdr: HeaderParsed, message: ByteArray): Int? {
        val key = conn.signingKey ?: return null
        val signed = (hdr.flags and 0x08) != 0
        if (signed) {
            return if (Smb2Crypto.verifySignature(key, conn.dialect, message)) null else STATUS_ACCESS_DENIED
        }
        val unsignedAllowed = hdr.command == CMD_NEGOTIATE || hdr.command == CMD_SESSION_SETUP || hdr.command == CMD_CANCEL
        return if (conn.signingRequired && !unsignedAllowed) STATUS_ACCESS_DENIED else null
    }

    private fun grantCredits(conn: ConnState, hdr: HeaderParsed): Int {
        if (hdr.command != CMD_NEGOTIATE) {
            conn.credits = maxOf(0, conn.credits - maxOf(1, hdr.creditCharge))
        }
        val want = maxOf(1, hdr.credits)
        val grant = maxOf(1, minOf(want, CREDIT_LIMIT - conn.credits))
        conn.credits += grant
        return grant
    }

    private fun handleOne(conn: ConnState, hdr: HeaderParsed, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        return try {
            when (hdr.command) {
                CMD_NEGOTIATE -> hNegotiate(conn, body)
                CMD_SESSION_SETUP -> hSessionSetup(conn, body)
                CMD_LOGOFF -> {
                    conn.closeAll(); conn.authenticated = false; conn.trees.clear()
                    Triple(STATUS_SUCCESS, byteArrayOf(4, 0, 0, 0), null)
                }
                CMD_TREE_CONNECT -> hTreeConnect(conn, body)
                CMD_TREE_DISCONNECT -> {
                    conn.trees.remove(hdr.treeId)
                    Triple(STATUS_SUCCESS, byteArrayOf(4, 0, 0, 0), null)
                }
                CMD_CREATE -> hCreate(conn, hdr, body)
                CMD_CLOSE -> hClose(conn, hdr, body)
                CMD_FLUSH -> {
                    requireTree(conn, hdr, ipcOk = false)
                    val h = conn.resolveHandle(body.copyOfRange(8, 24))
                    h.ra?.flush()
                    Triple(STATUS_SUCCESS, byteArrayOf(4, 0, 0, 0), null)
                }
                CMD_READ -> hRead(conn, hdr, body)
                CMD_WRITE -> hWrite(conn, hdr, body)
                CMD_QUERY_DIRECTORY -> hQueryDirectory(conn, hdr, body)
                CMD_QUERY_INFO -> hQueryInfo(conn, hdr, body)
                CMD_SET_INFO -> hSetInfo(conn, hdr, body)
                CMD_ECHO -> Triple(STATUS_SUCCESS, byteArrayOf(4, 0, 0, 0), null)
                CMD_IOCTL -> hIoctl(conn, hdr, body)
                CMD_CANCEL -> Triple(null, ByteArray(0), null)
                else -> Triple(STATUS_NOT_SUPPORTED, errorBody(), null)
            }
        } catch (e: Smb2Err) {
            Triple(e.status, errorBody(), null)
        } catch (e: VfsPathException) {
            val st = when (e.code) {
                "STATUS_OBJECT_NAME_INVALID" -> STATUS_OBJECT_NAME_INVALID
                "STATUS_OBJECT_PATH_SYNTAX_BAD" -> STATUS_OBJECT_PATH_SYNTAX_BAD
                else -> STATUS_ACCESS_DENIED
            }
            Triple(st, errorBody(), null)
        } catch (e: Smb2Auth.AuthFailure) {
            Triple(e.status, errorBody(), null)
        } catch (e: Exception) {
            if (verbose) log("$displayName handler error: ${e.message}")
            Triple(STATUS_INVALID_PARAMETER, errorBody(), null)
        }
    }

    private fun hNegotiate(conn: ConnState, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        if (body.size < 36) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val count = bb.getShort(2).toInt() and 0xFFFF
        val mode = bb.getShort(4).toInt() and 0xFFFF
        val caps = bb.getInt(8)
        val offered = ArrayList<Int>(count)
        for (i in 0 until count) {
            val at = 36 + i * 2
            if (at + 2 > body.size) break
            offered.add(bb.getShort(at).toInt() and 0xFFFF)
        }
        conn.negClaims = NegClaims(caps, body.copyOfRange(12, 28), mode, offered.toIntArray())
        val supported = setOf(DIALECT_202, DIALECT_210, DIALECT_300, DIALECT_302)
        val chosen = offered.filter { it <= ceiling && it in supported }.maxOrNull()
            ?: throw Smb2Err(STATUS_NOT_SUPPORTED)
        conn.dialect = chosen
        return Triple(STATUS_SUCCESS, negotiateResponse(chosen), null)
    }

    private fun negotiateResponse(dialect: Int): ByteArray {
        val token = Smb2Auth.negTokenInit()
        val nowFt = Smbv1ServerEngine.toFiletime(System.currentTimeMillis())
        val fixed = ByteBuffer.allocate(64).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(65).putShort(0x0001).putShort(dialect.toShort()).putShort(0)
            .put(guid)
            .putInt(0x00000004) // GLOBAL_CAP_LARGE_MTU
            .putInt(MAX_TRANSACT).putInt(MAX_READ).putInt(MAX_WRITE)
            .putLong(nowFt).putLong(startTimeFt)
            .putShort((64 + 64).toShort()).putShort(token.size.toShort()).putInt(0)
            .array()
        return fixed + token
    }

    private fun hSessionSetup(conn: ConnState, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        if (body.size < 24) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val secMode = body[3].toInt() and 0xFF
        val bufOff = bb.getShort(12).toInt() and 0xFFFF
        val bufLen = bb.getShort(14).toInt() and 0xFFFF
        val blob = reqBuf(body, bufOff, bufLen, 64 + 24)
        val token = Smb2Auth.extractNtlm(blob) ?: throw Smb2Err(0xC000006D.toInt())
        val raw = Smb2Auth.isRawNtlmssp(blob)
        val kind = Smb2Auth.messageType(token)

        if (kind == Smb2Auth.NTLM_NEGOTIATE) {
            val chal = Smb2Crypto.randomBytes(8)
            conn.challenge = chal
            val clientFlags = if (token.size >= 16) ByteBuffer.wrap(token, 12, 4).order(ByteOrder.LITTLE_ENDIAN).int else 0
            val chalMsg = Smb2Auth.buildChallenge(chal, "WORKGROUP", "PS2SERVERS", compact = raw, clientFlags = clientFlags)
            conn.ntlmNeg = Smb2Auth.trimNtlm(token)
            conn.ntlmChal = chalMsg
            conn.signingKey = null
            conn.signingRequired = false
            val outTok = if (raw) chalMsg else Smb2Auth.negTokenResp(1, chalMsg, withMech = true)
            if (conn.sessionId == 0L) conn.sessionId = 0x100000001L
            val bOut = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
                .putShort(9).putShort(0).putShort((64 + 8).toShort()).putShort(outTok.size.toShort()).array()
            return Triple(STATUS_MORE_PROCESSING_REQUIRED, bOut + outTok, null)
        }

        if (kind != Smb2Auth.NTLM_AUTHENTICATE || conn.challenge == null) {
            throw Smb2Err(0xC000006D.toInt())
        }
        val creds = Smb2Auth.parseAuthenticate(token)
        val sessionKey = Smb2Auth.verifyCredentials(username, password, openShare, creds, conn.challenge!!)
        val sessionFlags = when {
            openShare && creds.isAnonymous -> 0x0002
            openShare -> 0x0001
            else -> 0
        }
        if (sessionKey != null && !Smb2Auth.verifyMic(sessionKey, conn.ntlmNeg, conn.ntlmChal, creds)) {
            throw Smb2Err(0xC000006D.toInt())
        }
        conn.authenticated = true
        conn.challenge = null
        conn.ntlmNeg = null
        conn.ntlmChal = null
        if (sessionKey != null) {
            conn.signingKey = Smb2Crypto.signingKey(sessionKey, conn.dialect)
            conn.signingRequired = (secMode and 0x02) != 0
        }
        if (conn.sessionId == 0L) conn.sessionId = 0x100000001L
        val outTok = if (raw) ByteArray(0) else Smb2Auth.negTokenResp(0, null, withMech = false)
        val bOut = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(9).putShort(sessionFlags.toShort())
            .putShort(if (outTok.isEmpty()) 0 else (64 + 8).toShort())
            .putShort(outTok.size.toShort())
            .array()
        return Triple(STATUS_SUCCESS, bOut + outTok, null)
    }

    private fun hTreeConnect(conn: ConnState, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        if (!conn.authenticated) throw Smb2Err(STATUS_USER_SESSION_DELETED)
        if (body.size < 8) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val pOff = bb.getShort(4).toInt() and 0xFFFF
        val pLen = bb.getShort(6).toInt() and 0xFFFF
        val path = String(reqBuf(body, pOff, pLen, 64 + 8), Charsets.UTF_16LE)
        val name = path.trimEnd('\\').substringAfterLast('\\')
        if (name.equals("IPC$", ignoreCase = true)) {
            val tid = conn.nextTree++
            conn.trees[tid] = true
            val out = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
                .putShort(16).put(0x02).put(0).putInt(0x30).putInt(0).putInt(0x001F01FF).array()
            return Triple(STATUS_SUCCESS, out, tid)
        }
        if (!name.equals(normalizedShare, ignoreCase = true)) {
            throw Smb2Err(STATUS_BAD_NETWORK_NAME)
        }
        val tid = conn.nextTree++
        conn.trees[tid] = false
        val access = if (effectiveReadOnly) 0x001200A9 else 0x001F01FF
        val out = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(16).put(0x01).put(0).putInt(0).putInt(0).putInt(access).array()
        return Triple(STATUS_SUCCESS, out, tid)
    }

    private fun requireTree(conn: ConnState, hdr: HeaderParsed, ipcOk: Boolean): Boolean {
        if (!conn.authenticated) throw Smb2Err(STATUS_USER_SESSION_DELETED)
        val isIpc = conn.trees[hdr.treeId] ?: throw Smb2Err(STATUS_NETWORK_NAME_DELETED)
        if (isIpc && !ipcOk) throw Smb2Err(STATUS_INVALID_DEVICE_REQUEST)
        return isIpc
    }

    private fun hCreate(conn: ConnState, hdr: HeaderParsed, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        val isIpc = requireTree(conn, hdr, ipcOk = true)
        if (body.size < 56) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        if (isIpc) {
            val nOff = bb.getShort(44).toInt() and 0xFFFF
            val nLen = bb.getShort(46).toInt() and 0xFFFF
            val pipeName = String(reqBuf(body, nOff, nLen, 64 + 56), Charsets.UTF_16LE).trim('\\').lowercase(Locale.US)
            if (pipeName != "srvsvc") throw Smb2Err(STATUS_OBJECT_NAME_NOT_FOUND)
            val h = conn.addHandle(isIpc = true, pipeName, false)
            val remark = if (effectiveReadOnly) "read-only" else ""
            h.pipe = SrvsvcPipe(
                listOf(
                    SrvsvcPipe.ShareDesc(normalizedShare, 0, remark),
                    SrvsvcPipe.ShareDesc("IPC$", 0x80000003.toInt(), "Remote IPC")
                )
            )
            val out = ByteBuffer.allocate(88).order(ByteOrder.LITTLE_ENDIAN)
                .putShort(89).put(0).put(0).putInt(1)
                .putLong(0).putLong(0).putLong(0).putLong(0).putLong(4096).putLong(0)
                .putInt(0x80).putInt(0).putLong(h.fileId).putLong(h.fileId).putInt(0).putInt(0).array()
            return Triple(STATUS_SUCCESS, out, null)
        }

        val access = bb.getInt(24)
        val disposition = bb.getInt(36)
        val options = bb.getInt(40)
        val nOff = bb.getShort(44).toInt() and 0xFFFF
        val nLen = bb.getShort(46).toInt() and 0xFFFF
        val rawName = String(reqBuf(body, nOff, nLen, 64 + 56), Charsets.UTF_16LE)
        val rel = PathGuard.normalizeRelative(rawName, strictWindowsNames = true)

        val existing = vfs.stat(rel)
        val wantDir = (options and 0x00000001) != 0
        val wantFile = (options and 0x00000040) != 0
        if (disposition == 1 && existing == null) throw Smb2Err(STATUS_OBJECT_NAME_NOT_FOUND)
        if (disposition == 2 && existing != null) throw Smb2Err(STATUS_OBJECT_NAME_COLLISION)

        var action = 1
        if (existing == null) {
            if (effectiveReadOnly) throw Smb2Err(STATUS_MEDIA_WRITE_PROTECTED)
            if (wantDir) {
                if (!vfs.mkdir(rel)) throw Smb2Err(STATUS_ACCESS_DENIED)
                action = 2
            } else if (disposition in intArrayOf(0, 2, 3, 5)) {
                vfs.openWrite(rel, create = true, truncate = true).close()
                action = 2
            } else {
                throw Smb2Err(STATUS_OBJECT_NAME_NOT_FOUND)
            }
        } else if (disposition in intArrayOf(0, 4, 5) && !existing.isDirectory) {
            if (effectiveReadOnly) throw Smb2Err(STATUS_MEDIA_WRITE_PROTECTED)
            vfs.openWrite(rel, create = true, truncate = true).close()
            action = 3
        }

        val entry = vfs.stat(rel) ?: throw Smb2Err(STATUS_OBJECT_NAME_NOT_FOUND)
        if (entry.isDirectory && wantFile) throw Smb2Err(STATUS_FILE_IS_A_DIRECTORY)
        if (!entry.isDirectory && wantDir) throw Smb2Err(STATUS_NOT_A_DIRECTORY)

        val h = conn.addHandle(false, rel, entry.isDirectory)
        h.canWrite = (access and (0x2 or 0x4 or 0x02000000 or 0x10000000 or 0x40000000)) != 0
        if ((options and 0x00001000) != 0) {
            if (effectiveReadOnly) throw Smb2Err(STATUS_MEDIA_WRITE_PROTECTED)
            h.deleteOnClose = true
        }
        val ft = Smbv1ServerEngine.toFiletime(entry.lastModifiedMs)
        val alloc = (entry.size + 4095L) and 4095L.inv()
        val attrs = attrsFor(entry)
        val out = ByteBuffer.allocate(88).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(89).put(0).put(0).putInt(action)
            .putLong(ft).putLong(ft).putLong(ft).putLong(ft)
            .putLong(alloc).putLong(entry.size)
            .putInt(attrs).putInt(0)
            .putLong(h.fileId).putLong(h.fileId)
            .putInt(0).putInt(0).array()
        return Triple(STATUS_SUCCESS, out, null)
    }

    private fun hClose(conn: ConnState, hdr: HeaderParsed, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        requireTree(conn, hdr, ipcOk = true)
        if (body.size < 24) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val flags = bb.getShort(2).toInt() and 0xFFFF
        val h = conn.resolveHandle(body.copyOfRange(8, 24))
        val entry = if (!h.isIpc) vfs.stat(h.relPath) else null
        h.close()
        conn.handles.remove(h.fileId)
        if (h.deleteOnClose && !h.isIpc) {
            if (h.isDir) vfs.deleteDirectory(h.relPath) else vfs.deleteFile(h.relPath)
        }
        val out = ByteBuffer.allocate(60).order(ByteOrder.LITTLE_ENDIAN)
        if ((flags and 0x0001) != 0 && entry != null) {
            val ft = Smbv1ServerEngine.toFiletime(entry.lastModifiedMs)
            val alloc = (entry.size + 4095L) and 4095L.inv()
            out.putShort(60).putShort(flags.toShort()).putInt(0)
                .putLong(ft).putLong(ft).putLong(ft).putLong(ft)
                .putLong(alloc).putLong(entry.size).putInt(attrsFor(entry))
        } else {
            out.putShort(60)
        }
        return Triple(STATUS_SUCCESS, out.array(), null)
    }

    private fun ensureOpenFile(h: HandleState, writable: Boolean): VfsRandomAccess {
        val existing = h.ra
        if (existing != null && (!writable || !effectiveReadOnly)) return existing
        existing?.close()
        val ra = if (writable) vfs.openWrite(h.relPath, create = false, truncate = false) else vfs.openRead(h.relPath)
        h.ra = ra
        return ra
    }

    private fun hRead(conn: ConnState, hdr: HeaderParsed, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        requireTree(conn, hdr, ipcOk = true)
        if (body.size < 48) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val reqLen = minOf(bb.getInt(4), MAX_READ)
        val offset = bb.getLong(8)
        val h = conn.resolveHandle(body.copyOfRange(16, 32))
        val pipe = h.pipe
        if (pipe != null) {
            val (data, more) = pipe.read(reqLen)
            if (data.isEmpty()) throw Smb2Err(STATUS_PIPE_EMPTY)
            val bHdr = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
                .putShort(17).put((64 + 16).toByte()).put(0).putInt(data.size).putInt(0).putInt(0).array()
            return Triple(if (more) STATUS_BUFFER_OVERFLOW else STATUS_SUCCESS, bHdr + data, null)
        }
        if (h.isDir) throw Smb2Err(STATUS_INVALID_DEVICE_REQUEST)
        val ra = ensureOpenFile(h, writable = false)
        val buf = ByteArray(reqLen)
        var got = 0
        while (got < reqLen) {
            val n = ra.readAt(offset + got, buf, got, reqLen - got)
            if (n <= 0) break
            got += n
        }
        if (got == 0) throw Smb2Err(STATUS_END_OF_FILE)
        val data = if (got == buf.size) buf else buf.copyOf(got)
        val bHdr = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(17).put((64 + 16).toByte()).put(0).putInt(data.size).putInt(0).putInt(0).array()
        return Triple(STATUS_SUCCESS, bHdr + data, null)
    }

    private fun hWrite(conn: ConnState, hdr: HeaderParsed, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        requireTree(conn, hdr, ipcOk = true)
        if (body.size < 48) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val dataOff = bb.getShort(2).toInt() and 0xFFFF
        val length = bb.getInt(4)
        val offset = bb.getLong(8)
        val h = conn.resolveHandle(body.copyOfRange(16, 32))
        val pipe = h.pipe
        if (pipe != null) {
            val payload = reqBuf(body, dataOff, length, 64 + 48)
            val written = pipe.write(payload)
            val out = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
                .putShort(17).putShort(0).putInt(written).putInt(0).putShort(0).putShort(0).array()
            return Triple(STATUS_SUCCESS, out, null)
        }
        if (effectiveReadOnly) throw Smb2Err(STATUS_MEDIA_WRITE_PROTECTED)
        if (h.isDir) throw Smb2Err(STATUS_INVALID_DEVICE_REQUEST)
        if (!h.canWrite) throw Smb2Err(STATUS_ACCESS_DENIED)
        if (length > MAX_WRITE) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val data = reqBuf(body, dataOff, length, 64 + 48)
        val ra = ensureOpenFile(h, writable = true)
        var written = 0
        while (written < data.size) {
            val n = ra.writeAt(offset + written, data, written, data.size - written)
            if (n <= 0) break
            written += n
        }
        ra.flush()
        val out = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(17).putShort(0).putInt(written).putInt(0).putShort(0).putShort(0).array()
        return Triple(STATUS_SUCCESS, out, null)
    }

    private fun hQueryDirectory(conn: ConnState, hdr: HeaderParsed, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        requireTree(conn, hdr, ipcOk = false)
        if (body.size < 32) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val infoClass = body[2].toInt() and 0xFF
        val flags = body[3].toInt() and 0xFF
        val h = conn.resolveHandle(body.copyOfRange(8, 24))
        if (!h.isDir) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val nOff = bb.getShort(24).toInt() and 0xFFFF
        val nLen = bb.getShort(26).toInt() and 0xFFFF
        val outLen = minOf(bb.getInt(28).let { if (it <= 0) MAX_TRANSACT else it }, MAX_TRANSACT)
        val pattern = String(reqBuf(body, nOff, nLen, 64 + 32), Charsets.UTF_16LE).ifEmpty { "*" }

        if (h.listing == null || (flags and 0x01) != 0) {
            val listed = ArrayList<String>().apply { add("."); add("..") }
            val children = vfs.list(h.relPath).map { it.name }.sorted()
            if (pattern == "*" || pattern == "*.*") {
                listed.addAll(children)
            } else {
                val regex = wildcardToRegex(pattern)
                listed.addAll(children.filter { regex.matches(it.lowercase(Locale.US)) })
            }
            h.listing = listed
            h.cursor = 0
        }

        val listing = h.listing!!
        val records = ArrayList<ByteArray>()
        var total = 0
        while (h.cursor < listing.size) {
            val name = listing[h.cursor]
            val targetEntry = when (name) {
                "." -> vfs.stat(h.relPath)
                ".." -> vfs.stat(h.relPath.substringBeforeLast('/', ""))
                else -> vfs.stat(if (h.relPath.isEmpty()) name else "${h.relPath}/$name")
            }
            if (targetEntry == null) {
                h.cursor++
                continue
            }
            val rec = encodeDirEntry(name, targetEntry, infoClass) ?: throw Smb2Err(STATUS_INVALID_PARAMETER)
            val padded = (rec.size + 7) and 7.inv()
            if (total + padded > outLen && records.isNotEmpty()) break
            records.add(if (padded == rec.size) rec else rec + ByteArray(padded - rec.size))
            total += padded
            h.cursor++
            if ((flags and 0x02) != 0) break
        }
        if (records.isEmpty()) throw Smb2Err(STATUS_NO_MORE_FILES)

        val blob = ByteArray(total)
        var pos = 0
        for (i in records.indices) {
            val r = records[i]
            System.arraycopy(r, 0, blob, pos, r.size)
            if (i < records.size - 1) {
                ByteBuffer.wrap(blob, pos, 4).order(ByteOrder.LITTLE_ENDIAN).putInt(r.size)
            }
            pos += r.size
        }
        val bHdr = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(9).putShort((64 + 8).toShort()).putInt(blob.size).array()
        return Triple(STATUS_SUCCESS, bHdr + blob, null)
    }

    private fun wildcardToRegex(pattern: String): Regex {
        val sb = StringBuilder("^")
        for (ch in pattern.lowercase(Locale.US)) {
            when (ch) {
                '*' -> sb.append(".*")
                '?' -> sb.append('.')
                '.', '(', ')', '+', '|', '^', '$', '[', ']', '{', '}', '\\' -> sb.append('\\').append(ch)
                else -> sb.append(ch)
            }
        }
        sb.append('$')
        return Regex(sb.toString())
    }

    private fun encodeDirEntry(name: String, entry: VfsEntry, infoClass: Int): ByteArray? {
        val ft = Smbv1ServerEngine.toFiletime(entry.lastModifiedMs)
        val sz = if (entry.isDirectory) 0L else entry.size
        val alloc = (sz + 4095L) and 4095L.inv()
        val attrs = attrsFor(entry)
        val nUtf16 = name.toByteArray(Charsets.UTF_16LE)
        val fileId = (entry.relativePath.hashCode().toLong() and 0xFFFFFFFFL) or (sz shl 32)
        return when (infoClass) {
            0x01 -> ByteBuffer.allocate(64 + nUtf16.size).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(0).putInt(0).putLong(ft).putLong(ft).putLong(ft).putLong(ft)
                .putLong(sz).putLong(alloc).putInt(attrs).putInt(nUtf16.size).put(nUtf16).array()
            0x02 -> ByteBuffer.allocate(68 + nUtf16.size).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(0).putInt(0).putLong(ft).putLong(ft).putLong(ft).putLong(ft)
                .putLong(sz).putLong(alloc).putInt(attrs).putInt(nUtf16.size).putInt(0).put(nUtf16).array()
            0x26 -> ByteBuffer.allocate(80 + nUtf16.size).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(0).putInt(0).putLong(ft).putLong(ft).putLong(ft).putLong(ft)
                .putLong(sz).putLong(alloc).putInt(attrs).putInt(nUtf16.size).putInt(0).putInt(0).putLong(fileId).put(nUtf16).array()
            0x03 -> ByteBuffer.allocate(94 + nUtf16.size).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(0).putInt(0).putLong(ft).putLong(ft).putLong(ft).putLong(ft)
                .putLong(sz).putLong(alloc).putInt(attrs).putInt(nUtf16.size).putInt(0).put(0).put(0).put(ByteArray(24)).put(nUtf16).array()
            0x25 -> ByteBuffer.allocate(104 + nUtf16.size).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(0).putInt(0).putLong(ft).putLong(ft).putLong(ft).putLong(ft)
                .putLong(sz).putLong(alloc).putInt(attrs).putInt(nUtf16.size).putInt(0).put(0).put(0).put(ByteArray(24))
                .putShort(0).putLong(fileId).put(nUtf16).array()
            0x0C -> ByteBuffer.allocate(12 + nUtf16.size).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(0).putInt(0).putInt(nUtf16.size).put(nUtf16).array()
            else -> null
        }
    }

    private fun hQueryInfo(conn: ConnState, hdr: HeaderParsed, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        requireTree(conn, hdr, ipcOk = false)
        if (body.size < 40) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val infoType = body[2].toInt() and 0xFF
        val infoClass = body[3].toInt() and 0xFF
        val outLen = bb.getInt(4)
        val h = conn.resolveHandle(body.copyOfRange(24, 40))

        val blob: ByteArray = when (infoType) {
            0x01 -> {
                val entry = vfs.stat(h.relPath) ?: throw Smb2Err(STATUS_OBJECT_NAME_NOT_FOUND)
                val name = if (h.relPath.isEmpty()) "\\" else "\\${h.relPath.replace('/', '\\')}"
                encodeFileInfo(infoClass, entry, name) ?: throw Smb2Err(STATUS_NOT_SUPPORTED)
            }
            0x02 -> encodeFsInfo(infoClass) ?: throw Smb2Err(STATUS_NOT_SUPPORTED)
            else -> throw Smb2Err(STATUS_NOT_SUPPORTED)
        }
        if (outLen > 0 && blob.size > outLen) throw Smb2Err(STATUS_INFO_LENGTH_MISMATCH)
        val bHdr = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(9).putShort((64 + 8).toShort()).putInt(blob.size).array()
        return Triple(STATUS_SUCCESS, bHdr + blob, null)
    }

    private fun encodeFileInfo(infoClass: Int, entry: VfsEntry, name: String): ByteArray? {
        val ft = Smbv1ServerEngine.toFiletime(entry.lastModifiedMs)
        val sz = if (entry.isDirectory) 0L else entry.size
        val alloc = (sz + 4095L) and 4095L.inv()
        val attrs = attrsFor(entry)
        val fileId = (entry.relativePath.hashCode().toLong() and 0xFFFFFFFFL) or (sz shl 32)
        val nameUtf16 = name.toByteArray(Charsets.UTF_16LE)
        return when (infoClass) {
            0x04 -> ByteBuffer.allocate(40).order(ByteOrder.LITTLE_ENDIAN)
                .putLong(ft).putLong(ft).putLong(ft).putLong(ft).putInt(attrs).putInt(0).array()
            0x05 -> ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
                .putLong(alloc).putLong(sz).putInt(1).put(0).put(if (entry.isDirectory) 1 else 0).putShort(0).array()
            0x06 -> ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(fileId).array()
            0x07, 0x10, 0x11 -> ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(0).array()
            0x08 -> ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(0x001F01FF).array()
            0x0E -> ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(0L).array()
            0x09 -> ByteBuffer.allocate(4 + nameUtf16.size).order(ByteOrder.LITTLE_ENDIAN).putInt(nameUtf16.size).put(nameUtf16).array()
            0x23 -> ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putInt(attrs).putInt(0).array()
            0x22 -> ByteBuffer.allocate(56).order(ByteOrder.LITTLE_ENDIAN)
                .putLong(ft).putLong(ft).putLong(ft).putLong(ft).putLong(alloc).putLong(sz).putInt(attrs).putInt(0).array()
            0x12 -> {
                encodeFileInfo(0x04, entry, "")!! + encodeFileInfo(0x05, entry, "")!! +
                    encodeFileInfo(0x06, entry, "")!! + encodeFileInfo(0x07, entry, "")!! +
                    encodeFileInfo(0x08, entry, "")!! + ByteArray(16) +
                    encodeFileInfo(0x09, entry, name)!!
            }
            else -> null
        }
    }

    private fun encodeFsInfo(infoClass: Int): ByteArray? {
        return when (infoClass) {
            0x01 -> {
                val lbl = "PS2SERVERS".toByteArray(Charsets.UTF_16LE)
                ByteBuffer.allocate(18 + lbl.size).order(ByteOrder.LITTLE_ENDIAN)
                    .putLong(0L).putInt(0x53324253).putInt(lbl.size).put(0).put(0).put(lbl).array()
            }
            0x03, 0x07 -> {
                val fs = runCatching { vfs.statFs() }.getOrNull()
                val totalUnits = (fs?.totalBytes ?: 0L) / 4096L
                val freeUnits = (fs?.freeBytes ?: 0L) / 4096L
                if (infoClass == 0x03) {
                    ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
                        .putLong(totalUnits).putLong(freeUnits).putInt(8).putInt(512).array()
                } else {
                    ByteBuffer.allocate(32).order(ByteOrder.LITTLE_ENDIAN)
                        .putLong(totalUnits).putLong(freeUnits).putLong(freeUnits).putInt(8).putInt(512).array()
                }
            }
            0x04 -> ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putInt(7).putInt(0x20).array()
            0x05 -> {
                val ntfs = "NTFS".toByteArray(Charsets.UTF_16LE)
                ByteBuffer.allocate(12 + ntfs.size).order(ByteOrder.LITTLE_ENDIAN)
                    .putInt(0x06).putInt(255).putInt(ntfs.size).put(ntfs).array()
            }
            else -> null
        }
    }

    private fun hSetInfo(conn: ConnState, hdr: HeaderParsed, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        requireTree(conn, hdr, ipcOk = false)
        if (effectiveReadOnly) throw Smb2Err(STATUS_MEDIA_WRITE_PROTECTED)
        if (body.size < 32) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val infoType = body[2].toInt() and 0xFF
        val infoClass = body[3].toInt() and 0xFF
        val bufLen = bb.getInt(4)
        val bufOff = bb.getShort(8).toInt() and 0xFFFF
        val h = conn.resolveHandle(body.copyOfRange(16, 32))
        val payload = reqBuf(body, bufOff, bufLen, 64 + 32)
        if (infoType != 0x01) throw Smb2Err(STATUS_NOT_SUPPORTED)

        when (infoClass) {
            0x14 -> {
                if (!h.canWrite) throw Smb2Err(STATUS_ACCESS_DENIED)
                val size = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN).getLong(0)
                val ra = ensureOpenFile(h, writable = true)
                ra.setLength(size)
                return Triple(STATUS_SUCCESS, byteArrayOf(2, 0), null)
            }
            0x0D -> {
                h.deleteOnClose = payload.isNotEmpty() && payload[0].toInt() != 0
                return Triple(STATUS_SUCCESS, byteArrayOf(2, 0), null)
            }
            0x04 -> return Triple(STATUS_SUCCESS, byteArrayOf(2, 0), null)
            0x0A -> {
                if (payload.size < 20) throw Smb2Err(STATUS_INVALID_PARAMETER)
                val replace = payload[0].toInt() != 0
                val nLen = ByteBuffer.wrap(payload, 16, 4).order(ByteOrder.LITTLE_ENDIAN).int
                if (20 + nLen > payload.size) throw Smb2Err(STATUS_INVALID_PARAMETER)
                val rawTarget = String(payload, 20, nLen, Charsets.UTF_16LE)
                val destRel = PathGuard.normalizeRelative(rawTarget, strictWindowsNames = true)
                val existsDest = vfs.stat(destRel) != null
                if (existsDest && !destRel.equals(h.relPath, ignoreCase = true) && !replace) {
                    throw Smb2Err(STATUS_OBJECT_NAME_COLLISION)
                }
                h.close()
                if (!vfs.rename(h.relPath, destRel, replaceIfExists = replace)) {
                    throw Smb2Err(STATUS_ACCESS_DENIED)
                }
                return Triple(STATUS_SUCCESS, byteArrayOf(2, 0), null)
            }
            else -> throw Smb2Err(STATUS_NOT_SUPPORTED)
        }
    }

    private fun hIoctl(conn: ConnState, hdr: HeaderParsed, body: ByteArray): Triple<Int?, ByteArray, Int?> {
        requireTree(conn, hdr, ipcOk = true)
        if (body.size < 56) throw Smb2Err(STATUS_INVALID_PARAMETER)
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val ctlCode = bb.getInt(4)
        val inOff = bb.getInt(24)
        val inLen = bb.getInt(28)
        val maxOut = bb.getInt(44)
        val data = reqBuf(body, inOff, inLen, 64 + 56)
        var status = STATUS_SUCCESS
        val outData: ByteArray
        when (ctlCode) {
            0x00140204 -> {
                if (data.size < 24) throw Smb2Err(STATUS_INVALID_PARAMETER)
                val db = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
                val caps = db.getInt(0)
                val cliGuid = data.copyOfRange(4, 20)
                val mode = db.getShort(20).toInt() and 0xFFFF
                val cnt = db.getShort(22).toInt() and 0xFFFF
                val claims = conn.negClaims
                val dialsMatch = claims != null && 24 + cnt * 2 <= data.size &&
                    claims.dialects.size == cnt &&
                    (0 until cnt).all { (db.getShort(24 + it * 2).toInt() and 0xFFFF) == claims.dialects[it] }
                if (claims == null || caps != claims.caps || mode != claims.mode || !cliGuid.contentEquals(claims.guid) || !dialsMatch) {
                    conn.terminate = true
                    throw Smb2Err(STATUS_ACCESS_DENIED)
                }
                outData = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
                    .putInt(0x00000004).put(guid).putShort(0x0001).putShort(conn.dialect.toShort()).array()
            }
            0x0011C017 -> {
                val h = conn.resolveHandle(body.copyOfRange(8, 24))
                val pipe = h.pipe ?: throw Smb2Err(STATUS_INVALID_DEVICE_REQUEST)
                val full = pipe.transact(data)
                if (maxOut > 0 && full.size > maxOut) {
                    pipe.outbox = full.copyOfRange(maxOut, full.size) + pipe.outbox
                    outData = full.copyOf(maxOut)
                    status = STATUS_BUFFER_OVERFLOW
                } else {
                    outData = full
                }
            }
            else -> throw Smb2Err(STATUS_NOT_SUPPORTED)
        }
        val outOff = 64 + 48
        val bHdr = ByteBuffer.allocate(48).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(49).putShort(0).putInt(ctlCode)
            .put(body.copyOfRange(8, 24))
            .putInt(outOff).putInt(0).putInt(outOff).putInt(outData.size).putInt(0).putInt(0).array()
        return Triple(status, bHdr + outData, null)
    }

    private fun attrsFor(entry: VfsEntry): Int {
        var a = if (entry.isDirectory) 0x10 else 0x20
        if (effectiveReadOnly) a = a or 0x01
        return a
    }

    private fun reqBuf(body: ByteArray, offset: Int, length: Int, least: Int): ByteArray {
        if (length == 0) return ByteArray(0)
        val start = offset - 64
        if (offset < least || start < 0 || length < 0 || start + length > body.size) {
            throw Smb2Err(STATUS_INVALID_PARAMETER)
        }
        return body.copyOfRange(start, start + length)
    }

    private fun errorBody(): ByteArray = byteArrayOf(9, 0, 0, 0, 0, 0, 0, 0, 0)

    private fun packHeader(
        command: Int,
        status: Int,
        messageId: Long,
        treeId: Int,
        sessionId: Long,
        credits: Int,
        flags: Int,
        nextCommand: Int
    ): ByteArray {
        val out = ByteArray(64)
        System.arraycopy(SMB2_MAGIC, 0, out, 0, 4)
        val bb = ByteBuffer.wrap(out, 4, 60).order(ByteOrder.LITTLE_ENDIAN)
        bb.putShort(64).putShort(0).putInt(status).putShort(command.toShort())
            .putShort(credits.toShort()).putInt(flags or 0x00000001).putInt(nextCommand)
            .putLong(messageId).putInt(0).putInt(treeId).putLong(sessionId)
        return out
    }
}
