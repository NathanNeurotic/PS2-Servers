package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.vfs.PathGuard
import com.nathanneurotic.ps2servers.vfs.VfsEntry
import com.nathanneurotic.ps2servers.vfs.VfsPathException
import com.nathanneurotic.ps2servers.vfs.VfsRandomAccess
import com.nathanneurotic.ps2servers.vfs.VfsRoot
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.EOFException
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Pure-Kotlin ONC RPC + NFSv4.0 server tailored to `unfs4` (Neutrino / nfsman).
 *
 * Enforces the unfs4 wire contract: single RPC fragment replies with AUTH_NONE verifier,
 * echoed COMPOUND tag, attributes packed strictly as TYPE/SIZE/TIME_MODIFY, bounded READ
 * and READDIR replies, and persistent idle connections without server-side timeouts.
 */
class Nfs4ServerEngine(
    private val vfs: VfsRoot,
    private val port: Int = DEFAULT_PORT,
    private val bindIp: String = "",
    private val allowWrites: Boolean = false,
    private val verbose: Boolean = false,
    private val log: (String) -> Unit = {}
) : ServerEngine {

    companion object {
        const val DEFAULT_PORT = 2049
        const val MAX_IO = 1024 * 1024
        const val MAX_RECORD = MAX_IO + 8192
        const val MAX_OPS = 128
        const val MAX_READDIR = 64 * 1024
        const val MAX_CONNECTIONS = 32

        const val NFS_PROGRAM = 100003
        const val NFS_V4 = 4

        const val NFS4_OK = 0
        const val NFS4ERR_NOENT = 2
        const val NFS4ERR_IO = 5
        const val NFS4ERR_ACCESS = 13
        const val NFS4ERR_EXIST = 17
        const val NFS4ERR_NOTDIR = 20
        const val NFS4ERR_ISDIR = 21
        const val NFS4ERR_INVAL = 22
        const val NFS4ERR_FBIG = 27
        const val NFS4ERR_ROFS = 30
        const val NFS4ERR_NAMETOOLONG = 63
        const val NFS4ERR_NOTEMPTY = 66
        const val NFS4ERR_STALE = 70
        const val NFS4ERR_BADHANDLE = 10001
        const val NFS4ERR_BAD_COOKIE = 10003
        const val NFS4ERR_NOTSUPP = 10004
        const val NFS4ERR_TOOSMALL = 10005
        const val NFS4ERR_SERVERFAULT = 10006
        const val NFS4ERR_BADTYPE = 10007
        const val NFS4ERR_RESOURCE = 10018
        const val NFS4ERR_NOFILEHANDLE = 10020
        const val NFS4ERR_MINOR_VERS_MISMATCH = 10021
        const val NFS4ERR_STALE_CLIENTID = 10022
        const val NFS4ERR_ATTRNOTSUPP = 10032
        const val NFS4ERR_BADXDR = 10036
        const val NFS4ERR_BADNAME = 10041
        const val NFS4ERR_OP_ILLEGAL = 10044

        const val OP_CLOSE = 4
        const val OP_CREATE = 6
        const val OP_GETATTR = 9
        const val OP_GETFH = 10
        const val OP_LOOKUP = 15
        const val OP_OPEN = 18
        const val OP_OPEN_CONFIRM = 20
        const val OP_PUTFH = 22
        const val OP_PUTROOTFH = 24
        const val OP_READ = 25
        const val OP_READDIR = 26
        const val OP_REMOVE = 28
        const val OP_RENAME = 29
        const val OP_SAVEFH = 32
        const val OP_SETATTR = 34
        const val OP_SETCLIENTID = 35
        const val OP_SETCLIENTID_CONFIRM = 36
        const val OP_WRITE = 38
        const val OP_ILLEGAL = 10044

        private const val FATTR4_TYPE = 1
        private const val FATTR4_SIZE = 4
        private const val FATTR4_MODE = 33
        private const val FATTR4_TIME_MODIFY = 53
        private val GETTABLE_ATTRS = intArrayOf(FATTR4_TYPE, FATTR4_SIZE, FATTR4_TIME_MODIFY)
    }

    override val key: String = "nfs"
    override val displayName: String = "NFSv4"

    private val effectiveAllowWrites = allowWrites && !vfs.isReadOnly
    private val prefix = Smb2Crypto.randomBytes(8)
    private val writeVerifier = Smb2Crypto.randomBytes(8)
    private val stateidOther = Smb2Crypto.randomBytes(4)

    private val handleLock = Any()
    private val pathByNum = HashMap<Long, List<String>>().apply { put(0L, emptyList()) }
    private val numByPath = HashMap<String, Long>().apply { put("", 0L) }
    private var nextHandleNum = 1L

    private val fsLock = Any()
    private val stateLock = Any()
    private var counter = 0L
    private var generation = 0L
    private val clients = LinkedHashMap<Long, ByteArray>()

    private val connLock = Any()
    private val activeConns = HashSet<ConnState>()

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

    private class Nfs4Err(val status: Int) : Exception("NFS4 error $status")
    private class XdrErr : Exception("XDR decode error")

    private class CachedFile(val generation: Long, val ra: VfsRandomAccess)

    private class ConnState(val sock: Socket) {
        @Volatile
        var lastActiveMs = System.currentTimeMillis()
        @Volatile
        var closing = false
        val files = LinkedHashMap<Long, CachedFile>()

        fun closeFiles() {
            for (cf in files.values) {
                try {
                    cf.ra.close()
                } catch (_: Exception) {
                }
            }
            files.clear()
        }
    }

    private class CompoundState(val conn: ConnState?) {
        var cfh: List<String>? = null
        var sfh: List<String>? = null
    }

    private class XdrReader(private val data: ByteArray) {
        var pos = 0

        fun take(n: Int): ByteArray {
            if (n < 0 || pos + n > data.size) throw XdrErr()
            val out = data.copyOfRange(pos, pos + n)
            pos += n
            return out
        }

        fun u32(): Int {
            val b = take(4)
            return ((b[0].toInt() and 0xFF) shl 24) or
                ((b[1].toInt() and 0xFF) shl 16) or
                ((b[2].toInt() and 0xFF) shl 8) or
                (b[3].toInt() and 0xFF)
        }

        fun u64(): Long {
            val b = take(8)
            return ByteBuffer.wrap(b).order(ByteOrder.BIG_ENDIAN).long
        }

        fun fixed(n: Int): ByteArray {
            val chunk = take(n)
            val pad = (4 - (n and 3)) and 3
            if (pad > 0) take(pad)
            return chunk
        }

        fun opaque(): ByteArray = fixed(u32())

        fun bitmap(): Set<Int> {
            val n = u32()
            if (n < 0 || n > 8) throw XdrErr()
            val out = HashSet<Int>()
            for (w in 0 until n) {
                val word = u32()
                for (b in 0 until 32) {
                    if (((word ushr b) and 1) != 0) out.add(w * 32 + b)
                }
            }
            return out
        }

        fun remaining(): Int = data.size - pos
    }

    override fun start() {
        if (!running.compareAndSet(false, true)) return
        val requestedPort = port
        val requestedBindIp = bindIp.trim()
        ServerPortGuard.ensureBindable(requestedPort, redirect445Enabled = false)
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
                Thread(r, "ps2-nfs4-conn").apply { isDaemon = true }
            }
            log("NFSv4 listening on ${requestedBindIp.ifEmpty { "0.0.0.0" }}:$actualPort (${vfs.displayPath}, ${if (effectiveAllowWrites) "writes ON" else "read-only"})")
            acceptThread = Thread({ acceptLoop(ss) }, "ps2-nfs4-accept").apply {
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
        synchronized(connLock) {
            for (c in activeConns) {
                try {
                    c.sock.close()
                } catch (_: Exception) {
                }
            }
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
                client.tcpNoDelay = true
                val conn = ConnState(client)
                admitConnection(conn)
                workerPool?.submit { serveConnection(conn) } ?: client.close()
            } catch (_: SocketException) {
                break
            } catch (e: IOException) {
                if (running.get()) log("NFSv4 accept error: ${e.message}")
            }
        }
    }

    private fun admitConnection(conn: ConnState) {
        var evict: ConnState? = null
        synchronized(connLock) {
            activeConns.add(conn)
            val live = activeConns.filter { !it.closing }
            if (live.size > MAX_CONNECTIONS) {
                val counts = HashMap<InetAddress?, Int>()
                for (c in live) {
                    val addr = c.sock.inetAddress
                    counts[addr] = (counts[addr] ?: 0) + 1
                }
                val topAddr = counts.entries.maxByOrNull { it.value }?.key
                evict = live.filter { it.sock.inetAddress == topAddr }.minByOrNull { it.lastActiveMs }
                evict?.closing = true
            }
        }
        if (evict != null) {
            try {
                evict!!.sock.close()
            } catch (_: Exception) {
            }
        }
    }

    private fun serveConnection(conn: ConnState) {
        try {
            val inp = BufferedInputStream(conn.sock.getInputStream(), 16384)
            val out = conn.sock.getOutputStream()
            while (running.get()) {
                val record = readRecord(inp) ?: break
                conn.lastActiveMs = System.currentTimeMillis()
                val reply = handleRecord(record, conn)
                if (reply != null) {
                    val lenMark = 0x80000000.toInt() or reply.size
                    val frame = ByteArray(4 + reply.size)
                    ByteBuffer.wrap(frame, 0, 4).order(ByteOrder.BIG_ENDIAN).putInt(lenMark)
                    System.arraycopy(reply, 0, frame, 4, reply.size)
                    out.write(frame)
                    out.flush()
                }
                if (inp.available() == 0 && conn.files.isNotEmpty()) {
                    conn.closeFiles()
                }
            }
        } catch (_: EOFException) {
        } catch (_: SocketException) {
        } catch (e: Exception) {
            if (running.get() && verbose) log("NFSv4 conn error: ${e.message}")
        } finally {
            conn.closeFiles()
            synchronized(connLock) { activeConns.remove(conn) }
            try {
                conn.sock.close()
            } catch (_: Exception) {
            }
        }
    }

    private fun readRecord(inp: BufferedInputStream): ByteArray? {
        val parts = ByteArrayOutputStream()
        var total = 0
        while (true) {
            val markBytes = ByteArray(4)
            var off = 0
            while (off < 4) {
                val n = inp.read(markBytes, off, 4 - off)
                if (n < 0) return null
                off += n
            }
            val mark = ByteBuffer.wrap(markBytes).order(ByteOrder.BIG_ENDIAN).int
            val fragLen = mark and 0x7FFFFFFF
            total += 4 + fragLen
            if (total > MAX_RECORD) throw IOException("RPC record exceeds MAX_RECORD")
            val chunk = ByteArray(fragLen)
            var cOff = 0
            while (cOff < fragLen) {
                val n = inp.read(chunk, cOff, fragLen - cOff)
                if (n < 0) return null
                cOff += n
            }
            parts.write(chunk)
            if ((mark and 0x80000000.toInt()) != 0) {
                return parts.toByteArray()
            }
        }
    }

    private fun p32(v: Int): ByteArray = ByteBuffer.allocate(4).order(ByteOrder.BIG_ENDIAN).putInt(v).array()
    private fun p64(v: Long): ByteArray = ByteBuffer.allocate(8).order(ByteOrder.BIG_ENDIAN).putLong(v).array()
    private fun opaque(b: ByteArray): ByteArray {
        val pad = (4 - (b.size and 3)) and 3
        val out = ByteArray(4 + b.size + pad)
        ByteBuffer.wrap(out, 0, 4).order(ByteOrder.BIG_ENDIAN).putInt(b.size)
        System.arraycopy(b, 0, out, 4, b.size)
        return out
    }

    private fun encodeBitmap(bits: IntArray): ByteArray {
        if (bits.isEmpty()) return p32(0)
        val wordCount = (bits.maxOrNull()!! / 32) + 1
        val words = IntArray(wordCount)
        for (b in bits) words[b / 32] = words[b / 32] or (1 shl (b % 32))
        val bb = ByteBuffer.allocate(4 + wordCount * 4).order(ByteOrder.BIG_ENDIAN).putInt(wordCount)
        for (w in words) bb.putInt(w)
        return bb.array()
    }

    private fun accepted(xid: Int, stat: Int, body: ByteArray = ByteArray(0)): ByteArray {
        return p32(xid) + p32(1) + p32(0) + p32(0) + p32(0) + p32(stat) + body
    }

    private fun denied(xid: Int, body: ByteArray): ByteArray {
        return p32(xid) + p32(1) + p32(1) + body
    }

    private fun handleRecord(record: ByteArray, conn: ConnState? = null): ByteArray? {
        val r = XdrReader(record)
        val xid: Int
        val msgType: Int
        try {
            xid = r.u32()
            msgType = r.u32()
        } catch (_: XdrErr) {
            return null
        }
        if (msgType != 0) return null
        val rpcvers: Int; val prog: Int; val vers: Int; val proc: Int; val flavor: Int
        try {
            rpcvers = r.u32(); prog = r.u32(); vers = r.u32(); proc = r.u32()
            flavor = r.u32(); r.opaque(); r.u32(); r.opaque()
        } catch (_: XdrErr) {
            return accepted(xid, 4) // GARBAGE_ARGS
        }
        if (rpcvers != 2) return denied(xid, p32(0) + p32(2) + p32(2))
        if (flavor != 0 && flavor != 1) return denied(xid, p32(1) + p32(5))
        if (prog != NFS_PROGRAM) return accepted(xid, 1)
        if (vers != NFS_V4) return accepted(xid, 2, p32(NFS_V4) + p32(NFS_V4))
        if (proc == 0) return accepted(xid, 0)
        if (proc != 1) return accepted(xid, 3)

        val body = try {
            handleCompound(r, conn)
        } catch (_: XdrErr) {
            return accepted(xid, 4)
        }
        return accepted(xid, 0, body)
    }

    private fun handleCompound(r: XdrReader, conn: ConnState?): ByteArray {
        val tag = r.opaque()
        val minor = r.u32()
        val count = r.u32()
        val results = ByteArrayOutputStream()
        var overallStatus = NFS4_OK
        var doneCount = 0

        if (minor != 0) {
            overallStatus = NFS4ERR_MINOR_VERS_MISMATCH
        } else if (count < 0 || count > MAX_OPS) {
            overallStatus = NFS4ERR_RESOURCE
        } else {
            synchronized(fsLock) {
                val st = CompoundState(conn)
                var replyBytes = 0
                for (i in 0 until count) {
                    val rawOp = r.u32()
                    val (opSt, opBody) = if (replyBytes >= MAX_IO) {
                        NFS4ERR_RESOURCE to ByteArray(0)
                    } else {
                        runOp(rawOp, r, st)
                    }
                    val outOp = if (rawOp < 3 || rawOp > 39) OP_ILLEGAL else rawOp
                    doneCount++
                    if (opSt == NFS4_OK) {
                        results.write(p32(outOp))
                        results.write(p32(NFS4_OK))
                        results.write(opBody)
                        replyBytes += opBody.size
                    } else {
                        results.write(p32(outOp))
                        results.write(p32(opSt))
                        if (outOp == OP_SETATTR) results.write(p32(0))
                        overallStatus = opSt
                        break
                    }
                }
            }
        }
        return p32(overallStatus) + opaque(tag) + p32(doneCount) + results.toByteArray()
    }

    private fun runOp(op: Int, r: XdrReader, s: CompoundState): Pair<Int, ByteArray> {
        if (op < 3 || op > 39) return NFS4ERR_OP_ILLEGAL to ByteArray(0)
        return try {
            val out = when (op) {
                OP_PUTROOTFH -> { s.cfh = emptyList(); ByteArray(0) }
                OP_PUTFH -> opPutfh(r, s)
                OP_GETFH -> opaque(handleFor(s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)))
                OP_SAVEFH -> { s.sfh = s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE); ByteArray(0) }
                OP_LOOKUP -> opLookup(r, s)
                OP_GETATTR -> opGetattr(r, s)
                OP_READ -> opRead(r, s)
                OP_READDIR -> opReaddir(r, s)
                OP_SETCLIENTID -> opSetClientId(r)
                OP_SETCLIENTID_CONFIRM -> opSetClientIdConfirm(r)
                OP_OPEN -> opOpen(r, s)
                OP_OPEN_CONFIRM -> opOpenConfirm(r, s)
                OP_CLOSE -> opClose(r, s)
                OP_WRITE -> opWrite(r, s)
                OP_SETATTR -> opSetattr(r, s)
                OP_CREATE -> opCreate(r, s)
                OP_REMOVE -> opRemove(r, s)
                OP_RENAME -> opRename(r, s)
                else -> throw Nfs4Err(NFS4ERR_NOTSUPP)
            }
            NFS4_OK to out
        } catch (_: XdrErr) {
            NFS4ERR_BADXDR to ByteArray(0)
        } catch (e: Nfs4Err) {
            e.status to ByteArray(0)
        } catch (_: VfsPathException) {
            NFS4ERR_BADNAME to ByteArray(0)
        } catch (_: Exception) {
            NFS4ERR_IO to ByteArray(0)
        }
    }

    private fun handleFor(comps: List<String>): ByteArray {
        val key = comps.joinToString("/")
        val num = synchronized(handleLock) {
            numByPath[key] ?: nextHandleNum++.also {
                numByPath[key] = it
                pathByNum[it] = comps
            }
        }
        return prefix + p64(num)
    }

    private fun lookupHandle(fh: ByteArray): Pair<Long, List<String>> {
        if (fh.size != 16) throw Nfs4Err(NFS4ERR_BADHANDLE)
        for (i in 0 until 8) {
            if (fh[i] != prefix[i]) throw Nfs4Err(NFS4ERR_STALE)
        }
        val num = ByteBuffer.wrap(fh, 8, 8).order(ByteOrder.BIG_ENDIAN).long
        val comps = synchronized(handleLock) { pathByNum[num] } ?: throw Nfs4Err(NFS4ERR_STALE)
        return num to comps
    }

    private fun statComps(comps: List<String>): VfsEntry {
        return vfs.stat(comps.joinToString("/")) ?: throw Nfs4Err(NFS4ERR_STALE)
    }

    private fun validateName(raw: ByteArray): String {
        if (raw.isEmpty()) throw Nfs4Err(NFS4ERR_INVAL)
        if (raw.size > 255) throw Nfs4Err(NFS4ERR_NAMETOOLONG)
        val name = String(raw, Charsets.UTF_8)
        if (name == "." || name == ".." || '/' in name || '\\' in name || '\u0000' in name) {
            throw Nfs4Err(NFS4ERR_BADNAME)
        }
        PathGuard.validateWindowsComponent(name)
        return name
    }

    private fun encodeAttrs(want: Set<Int>, entry: VfsEntry): ByteArray {
        val bits = GETTABLE_ATTRS.filter { it in want }.toIntArray()
        val vals = ByteArrayOutputStream()
        for (bit in bits) {
            when (bit) {
                FATTR4_TYPE -> vals.write(p32(if (entry.isDirectory) 2 else 1))
                FATTR4_SIZE -> vals.write(p64(if (entry.isDirectory) 0L else entry.size))
                FATTR4_TIME_MODIFY -> {
                    val sec = entry.lastModifiedMs / 1000L
                    val nsec = ((entry.lastModifiedMs % 1000L) * 1_000_000L).toInt()
                    vals.write(p64(sec))
                    vals.write(p32(nsec))
                }
            }
        }
        return encodeBitmap(bits) + opaque(vals.toByteArray())
    }

    private fun opPutfh(r: XdrReader, s: CompoundState): ByteArray {
        val fh = r.opaque()
        s.cfh = lookupHandle(fh).second
        return ByteArray(0)
    }

    private fun opLookup(r: XdrReader, s: CompoundState): ByteArray {
        val comps = s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        val dir = statComps(comps)
        if (!dir.isDirectory) throw Nfs4Err(NFS4ERR_NOTDIR)
        val name = validateName(r.opaque())
        val next = comps + name
        vfs.stat(next.joinToString("/")) ?: throw Nfs4Err(NFS4ERR_NOENT)
        s.cfh = next
        return ByteArray(0)
    }

    private fun opGetattr(r: XdrReader, s: CompoundState): ByteArray {
        val want = r.bitmap()
        val entry = statComps(s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE))
        return encodeAttrs(want, entry)
    }

    private fun opRead(r: XdrReader, s: CompoundState): ByteArray {
        r.fixed(16)
        val offset = r.u64()
        val reqCount = minOf(r.u32(), MAX_IO).coerceAtLeast(0)
        val comps = s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        val rel = comps.joinToString("/")
        val num = ByteBuffer.wrap(handleFor(comps), 8, 8).order(ByteOrder.BIG_ENDIAN).long
        val entry = statComps(comps)
        if (entry.isDirectory) throw Nfs4Err(NFS4ERR_ISDIR)

        val conn = s.conn
        val cached = conn?.files?.get(num)
        val ra = if (cached != null && cached.generation == generation) {
            cached.ra
        } else {
            cached?.ra?.close()
            if (cached != null) conn?.files?.remove(num)
            val opened = vfs.openRead(rel)
            if (conn != null) {
                if (conn.files.size >= 4) {
                    val firstKey = conn.files.keys.first()
                    conn.files.remove(firstKey)?.ra?.close()
                }
                conn.files[num] = CachedFile(generation, opened)
            }
            opened
        }
        val fileSize = ra.size()
        val data: ByteArray
        try {
            if (offset < 0L || offset >= fileSize || reqCount == 0) {
                data = ByteArray(0)
            } else {
                val want = minOf(reqCount.toLong(), fileSize - offset).toInt()
                val buf = ByteArray(want)
                var got = 0
                while (got < want) {
                    val n = ra.readAt(offset + got, buf, got, want - got)
                    if (n <= 0) break
                    got += n
                }
                data = if (got == want) buf else buf.copyOf(got)
            }
        } finally {
            if (conn == null) ra.close()
        }
        val eof = offset + data.size >= fileSize
        return p32(if (eof) 1 else 0) + opaque(data)
    }

    private fun opReaddir(r: XdrReader, s: CompoundState): ByteArray {
        val cookie = r.u64()
        r.fixed(8)
        r.u32()
        val maxCount = r.u32()
        val want = r.bitmap()
        val comps = s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        val dir = statComps(comps)
        if (!dir.isDirectory) throw Nfs4Err(NFS4ERR_NOTDIR)
        if (cookie == 1L || cookie == 2L) throw Nfs4Err(NFS4ERR_BAD_COOKIE)

        val entries = vfs.list(comps.joinToString("/"))
        val start = if (cookie == 0L) 0 else (cookie - 2L).toInt()
        val budget = minOf(maxCount, MAX_READDIR) - 16
        val out = ByteArrayOutputStream()
        var used = 0
        var idx = start
        while (idx < entries.size) {
            val e = entries[idx]
            val rec = p32(1) + p64((idx + 3).toLong()) + opaque(e.name.toByteArray(Charsets.UTF_8)) + encodeAttrs(want, e)
            if (used + rec.size > budget) break
            out.write(rec)
            used += rec.size
            idx++
        }
        val eof = idx >= entries.size
        if (used == 0 && !eof) throw Nfs4Err(NFS4ERR_TOOSMALL)
        return ByteArray(8) + out.toByteArray() + p32(0) + p32(if (eof) 1 else 0)
    }

    private fun opSetClientId(r: XdrReader): ByteArray {
        r.fixed(8); r.opaque(); r.u32(); r.opaque(); r.opaque(); r.u32()
        val confirm = Smb2Crypto.randomBytes(8)
        val id = synchronized(stateLock) {
            counter++
            if (clients.size >= 256) clients.remove(clients.keys.first())
            clients[counter] = confirm
            counter
        }
        return p64(id) + confirm
    }

    private fun opSetClientIdConfirm(r: XdrReader): ByteArray {
        val cid = r.u64()
        val confirm = r.fixed(8)
        val ok = synchronized(stateLock) { clients[cid]?.contentEquals(confirm) == true }
        if (!ok) throw Nfs4Err(NFS4ERR_STALE_CLIENTID)
        return ByteArray(0)
    }

    private fun parseSettable(r: XdrReader): Long? {
        val bits = r.bitmap()
        val vals = XdrReader(r.opaque())
        for (b in bits) {
            if (b != FATTR4_SIZE && b != FATTR4_MODE) throw Nfs4Err(NFS4ERR_ATTRNOTSUPP)
        }
        var size: Long? = null
        for (b in bits.sorted()) {
            if (b == FATTR4_SIZE) size = vals.u64() else vals.u32()
        }
        if (vals.remaining() != 0) throw XdrErr()
        return size
    }

    private fun requireWrites(s: CompoundState?) {
        if (!effectiveAllowWrites) throw Nfs4Err(NFS4ERR_ROFS)
        s?.conn?.closeFiles()
    }

    private fun opOpen(r: XdrReader, s: CompoundState): ByteArray {
        r.u32()
        val access = r.u32(); r.u32(); r.u64(); r.opaque()
        val create = r.u32()
        var setSize: Long? = null
        var mode = 0
        if (create == 1) {
            mode = r.u32()
            if (mode != 0 && mode != 1) throw Nfs4Err(NFS4ERR_NOTSUPP)
            setSize = parseSettable(r)
        } else if (create != 0) {
            throw XdrErr()
        }
        if (r.u32() != 0) throw Nfs4Err(NFS4ERR_NOTSUPP)
        val rawName = r.opaque()
        if (access !in 1..3) throw Nfs4Err(NFS4ERR_INVAL)
        if (create == 1 || (access and 2) != 0) requireWrites(s)

        val parentComps = s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        val parent = statComps(parentComps)
        if (!parent.isDirectory) throw Nfs4Err(NFS4ERR_NOTDIR)
        val name = validateName(rawName)
        val next = parentComps + name
        val rel = next.joinToString("/")
        val existing = vfs.stat(rel)
        if (create == 1) {
            if (existing != null && mode == 1) throw Nfs4Err(NFS4ERR_EXIST)
            if (existing == null) {
                vfs.openWrite(rel, create = true, truncate = true).close()
            }
            if (setSize != null) {
                vfs.openWrite(rel, create = false, truncate = false).use { it.setLength(setSize) }
            }
            synchronized(stateLock) { generation++ }
        }
        val entry = vfs.stat(rel) ?: throw Nfs4Err(NFS4ERR_NOENT)
        if (entry.isDirectory) throw Nfs4Err(NFS4ERR_ISDIR)
        s.cfh = next
        val seq = synchronized(stateLock) { ++counter }
        val stateid = p32(1) + stateidOther + p64(seq)
        val cinfo = p32(0) + p64(parent.lastModifiedMs) + p64(parent.lastModifiedMs + 1L)
        val attrset = if (setSize != null) encodeBitmap(intArrayOf(FATTR4_SIZE)) else p32(0)
        return stateid + cinfo + p32(0) + attrset + p32(0)
    }

    private fun opOpenConfirm(r: XdrReader, s: CompoundState): ByteArray {
        val stateid = r.fixed(16)
        r.u32()
        s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        val seq = ByteBuffer.wrap(stateid, 0, 4).order(ByteOrder.BIG_ENDIAN).int + 1
        return p32(seq) + stateid.copyOfRange(4, 16)
    }

    private fun opClose(r: XdrReader, s: CompoundState): ByteArray {
        r.u32()
        val stateid = r.fixed(16)
        s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        val seq = ByteBuffer.wrap(stateid, 0, 4).order(ByteOrder.BIG_ENDIAN).int + 1
        return p32(seq) + stateid.copyOfRange(4, 16)
    }

    private fun opWrite(r: XdrReader, s: CompoundState): ByteArray {
        r.fixed(16)
        val offset = r.u64()
        r.u32()
        val data = r.opaque()
        val comps = s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        requireWrites(s)
        val entry = statComps(comps)
        if (entry.isDirectory) throw Nfs4Err(NFS4ERR_ISDIR)
        if (offset < 0 || offset > Long.MAX_VALUE - data.size) throw Nfs4Err(NFS4ERR_FBIG)
        vfs.openWrite(comps.joinToString("/"), create = false, truncate = false).use { ra ->
            var written = 0
            while (written < data.size) {
                val n = ra.writeAt(offset + written, data, written, data.size - written)
                if (n <= 0) break
                written += n
            }
            ra.flush()
        }
        synchronized(stateLock) { generation++ }
        return p32(data.size) + p32(2) + writeVerifier
    }

    private fun opSetattr(r: XdrReader, s: CompoundState): ByteArray {
        r.fixed(16)
        val size = parseSettable(r)
        val comps = s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        requireWrites(s)
        val entry = statComps(comps)
        if (size == null) return p32(0)
        if (entry.isDirectory) throw Nfs4Err(NFS4ERR_ISDIR)
        vfs.openWrite(comps.joinToString("/"), create = false, truncate = false).use { it.setLength(size) }
        synchronized(stateLock) { generation++ }
        return encodeBitmap(intArrayOf(FATTR4_SIZE))
    }

    private fun opCreate(r: XdrReader, s: CompoundState): ByteArray {
        if (r.u32() != 2) throw Nfs4Err(NFS4ERR_BADTYPE)
        val name = validateName(r.opaque())
        parseSettable(r)
        requireWrites(s)
        val parentComps = s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        val parent = statComps(parentComps)
        if (!parent.isDirectory) throw Nfs4Err(NFS4ERR_NOTDIR)
        val next = parentComps + name
        val rel = next.joinToString("/")
        if (vfs.stat(rel) != null) throw Nfs4Err(NFS4ERR_EXIST)
        if (!vfs.mkdir(rel)) throw Nfs4Err(NFS4ERR_IO)
        synchronized(stateLock) { generation++ }
        s.cfh = next
        val cinfo = p32(0) + p64(parent.lastModifiedMs) + p64(parent.lastModifiedMs + 1L)
        return cinfo + p32(0)
    }

    private fun opRemove(r: XdrReader, s: CompoundState): ByteArray {
        val name = validateName(r.opaque())
        requireWrites(s)
        val parentComps = s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        val parent = statComps(parentComps)
        if (!parent.isDirectory) throw Nfs4Err(NFS4ERR_NOTDIR)
        val target = parentComps + name
        val rel = target.joinToString("/")
        val entry = vfs.stat(rel) ?: throw Nfs4Err(NFS4ERR_NOENT)
        val ok = if (entry.isDirectory) vfs.deleteDirectory(rel) else vfs.deleteFile(rel)
        if (!ok) throw Nfs4Err(if (entry.isDirectory) NFS4ERR_NOTEMPTY else NFS4ERR_IO)
        synchronized(handleLock) {
            val it = pathByNum.entries.iterator()
            while (it.hasNext()) {
                val (num, p) = it.next()
                if (p.size >= target.size && p.subList(0, target.size) == target) {
                    numByPath.remove(p.joinToString("/"))
                    it.remove()
                }
            }
        }
        synchronized(stateLock) { generation++ }
        return p32(0) + p64(parent.lastModifiedMs) + p64(parent.lastModifiedMs + 1L)
    }

    private fun opRename(r: XdrReader, s: CompoundState): ByteArray {
        val oldName = validateName(r.opaque())
        val newName = validateName(r.opaque())
        val srcDir = s.sfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        val dstDir = s.cfh ?: throw Nfs4Err(NFS4ERR_NOFILEHANDLE)
        requireWrites(s)
        val srcParent = statComps(srcDir)
        val dstParent = statComps(dstDir)
        if (!srcParent.isDirectory || !dstParent.isDirectory) throw Nfs4Err(NFS4ERR_NOTDIR)
        val oldComps = srcDir + oldName
        val newComps = dstDir + newName
        val oldRel = oldComps.joinToString("/")
        val newRel = newComps.joinToString("/")
        val srcEntry = vfs.stat(oldRel) ?: throw Nfs4Err(NFS4ERR_NOENT)
        val dstEntry = vfs.stat(newRel)
        if (dstEntry != null) {
            if (srcEntry.isDirectory != dstEntry.isDirectory) throw Nfs4Err(NFS4ERR_EXIST)
            if (dstEntry.isDirectory && vfs.list(newRel).isNotEmpty()) throw Nfs4Err(NFS4ERR_EXIST)
            if (dstEntry.isDirectory) vfs.deleteDirectory(newRel)
        }
        if (!vfs.rename(oldRel, newRel, replaceIfExists = true)) throw Nfs4Err(NFS4ERR_IO)
        if (oldComps != newComps) {
            synchronized(handleLock) {
                val moved = ArrayList<Pair<Long, List<String>>>()
                val it = pathByNum.entries.iterator()
                while (it.hasNext()) {
                    val (num, p) = it.next()
                    if (p.size >= newComps.size && p.subList(0, newComps.size) == newComps) {
                        numByPath.remove(p.joinToString("/"))
                        it.remove()
                    } else if (p.size >= oldComps.size && p.subList(0, oldComps.size) == oldComps) {
                        numByPath.remove(p.joinToString("/"))
                        moved.add(num to (newComps + p.subList(oldComps.size, p.size)))
                    }
                }
                for ((num, np) in moved) {
                    pathByNum[num] = np
                    numByPath[np.joinToString("/")] = num
                }
            }
        }
        synchronized(stateLock) { generation++ }
        val cinfo1 = p32(0) + p64(srcParent.lastModifiedMs) + p64(srcParent.lastModifiedMs + 1L)
        val cinfo2 = p32(0) + p64(dstParent.lastModifiedMs) + p64(dstParent.lastModifiedMs + 1L)
        return cinfo1 + cinfo2
    }
}
