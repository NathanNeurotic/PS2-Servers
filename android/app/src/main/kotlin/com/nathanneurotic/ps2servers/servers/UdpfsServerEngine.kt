package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.compression.CompressedIso
import com.nathanneurotic.ps2servers.config.LauncherConfig
import com.nathanneurotic.ps2servers.vfs.PathGuard
import com.nathanneurotic.ps2servers.vfs.VfsCursorHandle
import com.nathanneurotic.ps2servers.vfs.VfsEntry
import com.nathanneurotic.ps2servers.vfs.VfsPathException
import com.nathanneurotic.ps2servers.vfs.VfsRandomAccess
import com.nathanneurotic.ps2servers.vfs.VfsRoot
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketAddress
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.DatagramChannel
import java.nio.channels.SelectionKey
import java.nio.channels.Selector
import java.util.Calendar
import java.util.TimeZone
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Pure-Kotlin UDPFS server over UDPRDMA compatible with ps2servers_core.py.
 *
 * Supports automatic per-session protocol detection (`auto`, `standard`, `modulo`),
 * hot loader-handoff transitions keeping open handles alive, dual-socket or single-port
 * operation, transparent CSO/ZSO `.iso` decompression, block device handle 0, and
 * router-status queries (`0xF5F7` + `PS2S`).
 */
class UdpfsServerEngine(
    private val vfs: VfsRoot?,
    private val blockDeviceFactory: (() -> VfsRandomAccess)? = null,
    private val port: Int = DEFAULT_PORT,
    private val dataPort: Int = 0,
    private val bindIp: String = "",
    private val protocolModeSetting: String = "Auto (recommended)",
    private val enableCompression: Boolean = true,
    private val readOnly: Boolean = false,
    private val sectorSize: Int = 512,
    private val txDelayMs: Double = 0.0,
    private val peerTimeoutSec: Double = DEFAULT_PEER_TIMEOUT_SEC,
    private val maxTransferBytes: Int = DEFAULT_MAX_TRANSFER_BYTES,
    private val maxSessions: Int = DEFAULT_MAX_SESSIONS,
    private val fallbackIntervalMs: Long = DEFAULT_FALLBACK_MS,
    private val verbose: Boolean = false,
    private val log: (String) -> Unit = {}
) : ServerEngine {

    companion object {
        const val DEFAULT_PORT = 0xF5F6
        const val UDPRDMA_SVC_UDPFS = 0xF5F5
        const val SVC_STATUS = 0xF5F7
        val STATUS_MAGIC = "PS2S".toByteArray(Charsets.US_ASCII)

        const val PKT_DISCOVERY = 0
        const val PKT_INFORM = 1
        const val PKT_DATA = 2

        const val FLAG_ACK = 1
        const val FLAG_FIN = 2

        const val MSG_OPEN_REQ = 0x10
        const val MSG_OPEN_REPLY = 0x11
        const val MSG_CLOSE_REQ = 0x12
        const val MSG_CLOSE_REPLY = 0x13
        const val MSG_READ_REQ = 0x14
        const val MSG_WRITE_REQ = 0x16
        const val MSG_WRITE_DATA = 0x17
        const val MSG_WRITE_DONE = 0x18
        const val MSG_LSEEK_REQ = 0x1A
        const val MSG_LSEEK_REPLY = 0x1B
        const val MSG_DREAD_REQ = 0x1C
        const val MSG_DREAD_REPLY = 0x1D
        const val MSG_GETSTAT_REQ = 0x1E
        const val MSG_GETSTAT_REPLY = 0x1F
        const val MSG_MKDIR_REQ = 0x20
        const val MSG_REMOVE_REQ = 0x22
        const val MSG_RMDIR_REQ = 0x24
        const val MSG_RESULT_REPLY = 0x26
        const val MSG_BREAD_REQ = 0x28
        const val MSG_BWRITE_REQ = 0x2A

        const val PROFILE_PENDING = "pending"
        const val PROFILE_STANDARD = "standard"
        const val PROFILE_MODULO = "modulo"

        const val SOCKET_DATA = "data"
        const val SOCKET_DISCOVERY = "discovery"

        const val FIO_S_IFREG = 0x2000
        const val FIO_S_IFDIR = 0x1000

        const val BLOCK_DEVICE_HANDLE = 0
        const val MAX_HANDLES = 64
        const val MAX_DATA_PAYLOAD = 1408
        const val SEND_WINDOW = 8
        const val WINDOW_ACK_TIMEOUT_MS = 100L
        const val MAX_WINDOW_RETRIES = 200
        const val DEFAULT_MAX_TRANSFER_BYTES = 8 * 1024 * 1024
        const val MIN_MAX_TRANSFER_BYTES = 256 * 1024
        const val DEFAULT_PEER_TIMEOUT_SEC = 3600.0
        const val MIN_PEER_TIMEOUT_SEC = 60.0
        const val MAX_PEER_TIMEOUT_SEC = 86400.0
        const val DEFAULT_MAX_SESSIONS = 256
        const val DEFAULT_FALLBACK_MS = 250L

        private const val ERR_EIO = 5
        private const val ERR_EBADF = 9
        private const val ERR_EACCES = 13
        private const val ERR_EEXIST = 17
        private const val ERR_ENOTDIR = 20
        private const val ERR_EISDIR = 21
        private const val ERR_EINVAL = 22
        private const val ERR_EMFILE = 24
        private const val ERR_ENOENT = 2
        private const val ERR_ENOTEMPTY = 39

        fun classifyProfile(discoverySeq: Int, firstDataSeq: Int): String {
            val d = discoverySeq and 0xFFF
            val f = firstDataSeq and 0xFFF
            val moduloSeq = (d + 1) and 0xFFF
            if (f == moduloSeq && (f != 0 || d != 0)) return PROFILE_MODULO
            if (d == 0 && f == 0) return PROFILE_STANDARD
            if (f == moduloSeq) return PROFILE_MODULO
            return PROFILE_STANDARD
        }
    }

    override val key: String = "udpfs"
    override val displayName: String = "UDPFS"

    private val protocolMode: String = LauncherConfig.protocolModeWireValue(protocolModeSetting) ?: "auto"
    private val singlePort: Boolean = protocolMode == PROFILE_MODULO
    private val effectiveReadOnly: Boolean = readOnly || (vfs?.isReadOnly == true)
    private val effectiveMaxTransfer: Int = maxOf(MIN_MAX_TRANSFER_BYTES, maxTransferBytes)
    private val effectivePeerTimeoutMs: Long = (peerTimeoutSec.coerceIn(MIN_PEER_TIMEOUT_SEC, MAX_PEER_TIMEOUT_SEC) * 1000.0).toLong()
    private val effectiveMaxSessions: Int = maxOf(1, maxSessions)

    private val running = AtomicBoolean(false)
    private var selector: Selector? = null
    private var discChannel: DatagramChannel? = null
    private var dataChannel: DatagramChannel? = null
    private var demuxThread: Thread? = null
    private var fallbackScheduler: ScheduledExecutorService? = null

    private var bdHandle: VfsRandomAccess? = null
    private var bdSectorCount: Long = 0L
    private val startTimeMs = System.currentTimeMillis()
    private val sendLock = Any()
    private val sessions = ConcurrentHashMap<SocketAddress, PeerSession>()

    @Volatile
    var actualDiscoveryPort: Int = 0
        private set

    @Volatile
    var actualDataPort: Int = 0
        private set

    override val boundPort: Int
        get() = actualDiscoveryPort

    override val isRunning: Boolean
        get() = running.get()

    private class InboundItem(val data: ByteArray, val addr: SocketAddress, val ingress: String)
    private class DirListingState(val entries: List<AdvertisedDirEntry>, var index: Int = 0)
    private class AdvertisedDirEntry(val displayName: String, val entry: VfsEntry, val virtualCompressedSize: Long?)
    private class OpenHandle(
        val isDir: Boolean,
        val cursor: VfsCursorHandle? = null,
        val dirState: DirListingState? = null
    ) {
        fun close() {
            cursor?.close()
        }
    }

    private inner class PeerSession(val addr: SocketAddress) {
        val lock = Any()
        var txSeqNr = 0
        var txSeqAcked = 0
        var rxSeqExpected = 0
        val txBuffer = ArrayList<Pair<Int, ByteArray>>()

        val handles = HashMap<Int, OpenHandle>()
        var nextHandle = 1

        var writeHandle = -1
        var writeIsBlock = false
        var writeSectorNr = 0L
        var writeSectorCount = 0
        var writeBuf = ByteArray(0)
        var writeLen = 0
        var writeTotalChunks = 0
        var writeReceivedChunks = 0

        var protocolProfile = if (protocolMode == "auto") PROFILE_PENDING else protocolMode
        var discoverySeq = 0
        var responseSocket = SOCKET_DATA
        var handshakeGen = 0L
        var fallbackSent = false
        var firstDataSeen = false
        var rxStreaming = false
        var pendingZeroDiscAt = 0L
        var pendingDiscSeq: Int? = null
        var lastDiscSeq: Int? = null
        var lastDiscAt = 0L
        var lastRetxSeq: Int? = null
        var lastRetxAt = 0L

        @Volatile
        var lastActivityMs = System.currentTimeMillis()

        val queue = LinkedBlockingQueue<InboundItem?>()
        val pushback = ArrayDeque<InboundItem>()
        private val poison = InboundItem(ByteArray(0), addr, "")
        private val closing = AtomicBoolean(false)
        private val thread = Thread({ runWorker() }, "ps2-udpfs-peer").apply { isDaemon = true }

        fun start() {
            thread.start()
        }

        fun enqueue(item: InboundItem) {
            if (!closing.get()) {
                lastActivityMs = System.currentTimeMillis()
                queue.offer(item)
            }
        }

        fun shutdown() {
            if (closing.compareAndSet(false, true)) {
                queue.offer(poison)
            }
        }

        fun join(timeoutMs: Long) {
            try {
                thread.join(timeoutMs)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
            }
        }

        fun resetTransport(nextProfile: String) {
            txSeqNr = 0
            txSeqAcked = 0
            rxSeqExpected = 0
            txBuffer.clear()
            writeHandle = -1
            writeIsBlock = false
            writeSectorNr = 0L
            writeSectorCount = 0
            writeLen = 0
            writeTotalChunks = 0
            writeReceivedChunks = 0
            rxStreaming = false
            protocolProfile = nextProfile
            responseSocket = SOCKET_DATA
            fallbackSent = false
            firstDataSeen = false
            pendingZeroDiscAt = 0L
            pendingDiscSeq = null
        }

        fun resetAll(nextProfile: String) {
            val it = handles.entries.iterator()
            while (it.hasNext()) {
                val entry = it.next()
                if (entry.key == BLOCK_DEVICE_HANDLE) continue
                try {
                    entry.value.close()
                } catch (_: Exception) {
                }
                it.remove()
            }
            nextHandle = 1
            resetTransport(nextProfile)
        }

        fun resetWrite() {
            writeHandle = -1
            writeIsBlock = false
            writeSectorNr = 0L
            writeSectorCount = 0
            writeLen = 0
            writeTotalChunks = 0
            writeReceivedChunks = 0
        }

        private fun nextPacket(timeoutMs: Long): InboundItem? {
            if (pushback.isNotEmpty()) return pushback.removeFirst()
            val item = if (timeoutMs < 0) queue.take() else queue.poll(timeoutMs, TimeUnit.MILLISECONDS)
            return if (item === poison) {
                closing.set(true)
                null
            } else {
                item
            }
        }

        private fun runWorker() {
            try {
                while (!closing.get() && running.get()) {
                    val item = nextPacket(1000L) ?: continue
                    try {
                        handleData(this, item.data, item.ingress)
                    } catch (e: Exception) {
                        log("UDPFS [$addr] error: ${e.message}")
                    }
                }
            } finally {
                for ((hid, h) in handles) {
                    if (hid == BLOCK_DEVICE_HANDLE) continue
                    try {
                        h.close()
                    } catch (_: Exception) {
                    }
                }
                handles.clear()
            }
        }

        fun pollWaitPacket(timeoutMs: Long): InboundItem? = nextPacket(timeoutMs)
        fun pushBack(item: InboundItem) {
            pushback.addLast(item)
        }
    }

    override fun start() {
        if (!running.compareAndSet(false, true)) return
        ServerPortGuard.ensureBindable(port, redirect445Enabled = false)
        if (!singlePort && dataPort != 0 && port != 0 && dataPort == port) {
            running.set(false)
            throw IOException("UDPFS data port ($dataPort) cannot match discovery port ($port) in dual-port mode")
        }
        try {
            if (blockDeviceFactory != null) {
                val dev = blockDeviceFactory.invoke()
                bdHandle = dev
                bdSectorCount = dev.size() / sectorSize
            }

            val bindHost = bindIp.trim().takeIf { it.isNotEmpty() }?.let { InetAddress.getByName(it) }
            val discAddr = if (bindHost == null) InetSocketAddress(port) else InetSocketAddress(bindHost, port)
            val dataAddr = if (bindHost == null) InetSocketAddress(dataPort) else InetSocketAddress(bindHost, dataPort)

            val disc = DatagramChannel.open().apply {
                configureBlocking(false)
                socket().reuseAddress = true
                socket().broadcast = true
                socket().bind(discAddr)
                widenBuffers(this, if (singlePort) (1 shl 20) else (1 shl 16))
            }
            discChannel = disc
            actualDiscoveryPort = disc.socket().localPort

            val dataCh = if (singlePort) {
                disc
            } else {
                DatagramChannel.open().apply {
                    configureBlocking(false)
                    socket().reuseAddress = true
                    socket().bind(dataAddr)
                    widenBuffers(this, 1 shl 20)
                }
            }
            dataChannel = dataCh
            actualDataPort = dataCh.socket().localPort

            val sel = Selector.open()
            disc.register(sel, SelectionKey.OP_READ, SOCKET_DISCOVERY)
            if (!singlePort) {
                dataCh.register(sel, SelectionKey.OP_READ, SOCKET_DATA)
            }
            selector = sel
            fallbackScheduler = Executors.newSingleThreadScheduledExecutor { r ->
                Thread(r, "ps2-udpfs-timer").apply { isDaemon = true }
            }

            log("UDPFS listening disc=${bindIp.ifBlank { "0.0.0.0" }}:$actualDiscoveryPort data=$actualDataPort mode=$protocolMode (${vfs?.displayPath ?: "block-only"})")
            demuxThread = Thread({ demuxLoop(sel) }, "ps2-udpfs-demux").apply {
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
            selector?.wakeup()
        } catch (_: Exception) {
        }
        for (sess in sessions.values) {
            sess.shutdown()
        }
        try {
            demuxThread?.join(1500L)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        for (sess in sessions.values) {
            sess.join(1000L)
        }
        sessions.clear()
        cleanup()
    }

    private fun cleanup() {
        fallbackScheduler?.shutdownNow()
        fallbackScheduler = null
        try {
            selector?.close()
        } catch (_: Exception) {
        }
        selector = null
        if (dataChannel != null && dataChannel !== discChannel) {
            try {
                dataChannel?.close()
            } catch (_: Exception) {
            }
        }
        dataChannel = null
        try {
            discChannel?.close()
        } catch (_: Exception) {
        }
        discChannel = null
        try {
            bdHandle?.close()
        } catch (_: Exception) {
        }
        bdHandle = null
        demuxThread = null
    }

    private fun widenBuffers(ch: DatagramChannel, target: Int) {
        val sock = ch.socket()
        try {
            if (sock.receiveBufferSize < target) {
                sock.receiveBufferSize = target
            }
        } catch (_: Exception) {
        }
        try {
            if (sock.sendBufferSize < target) {
                sock.sendBufferSize = target
            }
        } catch (_: Exception) {
        }
    }

    private fun demuxLoop(sel: Selector) {
        val rxBuf = ByteBuffer.allocate(4096)
        var lastSweepMs = System.currentTimeMillis()
        while (running.get()) {
            try {
                sel.select(1000L)
                if (!running.get()) break
                val keys = sel.selectedKeys().iterator()
                while (keys.hasNext()) {
                    val key = keys.next()
                    keys.remove()
                    if (!key.isValid || !key.isReadable) continue
                    val ch = key.channel() as DatagramChannel
                    val tag = key.attachment() as String
                    rxBuf.clear()
                    val addr = ch.receive(rxBuf) ?: continue
                    rxBuf.flip()
                    val len = rxBuf.remaining()
                    if (len < 2) continue
                    val data = ByteArray(len)
                    rxBuf.get(data)
                    val pktType = data[0].toInt() and 0x0F
                    if (pktType == PKT_DISCOVERY) {
                        handleDiscovery(data, addr)
                    } else if (pktType == PKT_DATA && len >= 6) {
                        routeData(data, addr, tag)
                    }
                }
                val now = System.currentTimeMillis()
                if (now - lastSweepMs >= 5000L) {
                    lastSweepMs = now
                    sweepIdleSessions(now)
                }
            } catch (_: Exception) {
                if (!running.get()) break
            }
        }
    }

    private fun sweepIdleSessions(nowMs: Long) {
        val it = sessions.entries.iterator()
        while (it.hasNext()) {
            val (addr, sess) = it.next()
            if (nowMs - sess.lastActivityMs > effectivePeerTimeoutMs) {
                it.remove()
                sess.shutdown()
                if (verbose) log("UDPFS [$addr] session expired")
            }
        }
    }

    private fun getOrCreateSession(addr: SocketAddress): Pair<PeerSession, Boolean> {
        sessions[addr]?.let {
            it.lastActivityMs = System.currentTimeMillis()
            return it to false
        }
        synchronized(sessions) {
            sessions[addr]?.let {
                it.lastActivityMs = System.currentTimeMillis()
                return it to false
            }
            if (sessions.size >= effectiveMaxSessions) {
                val oldest = sessions.entries.minByOrNull { it.value.lastActivityMs }
                if (oldest != null) {
                    sessions.remove(oldest.key)
                    oldest.value.shutdown()
                }
            }
            val created = PeerSession(addr)
            sessions[addr] = created
            created.start()
            return created to true
        }
    }

    private fun handleDiscovery(data: ByteArray, addr: SocketAddress) {
        if (data.size < 6) return
        if (maybeAnswerStatus(data, addr)) return
        val hdr = unpackHeader(data)
        val serviceId = u16Le(data, 2)
        if (hdr.first != PKT_DISCOVERY || serviceId != UDPRDMA_SVC_UDPFS) return

        val (sess, isNew) = getOrCreateSession(addr)
        if (isNew) {
            log("UDPFS [$addr] DISCOVERY received")
        } else if (verbose) {
            log("UDPFS [$addr] DISCOVERY seq=${hdr.second}")
        }

        if (protocolMode == PROFILE_MODULO) {
            synchronized(sess.lock) {
                val now = System.currentTimeMillis()
                if (sess.lastDiscSeq == hdr.second && now - sess.lastDiscAt < 2000L) {
                    val pkt = packHeader(PKT_INFORM, (sess.txSeqNr - 1) and 0xFFF) +
                        packDiscHeader(UDPRDMA_SVC_UDPFS, 0) + moduloInfoPayload()
                    sendRaw(discChannel!!, pkt, addr)
                    return
                }
                sess.lastDiscSeq = hdr.second
                sess.lastDiscAt = now
                sess.rxSeqExpected = (hdr.second + 1) and 0xFFF
                sess.responseSocket = SOCKET_DISCOVERY
                sendCompatibilityInform(sess)
            }
            return
        }

        val now = System.currentTimeMillis()
        synchronized(sess.lock) {
            if (sess.rxStreaming) {
                if (hdr.second == 0) {
                    sess.pendingZeroDiscAt = now
                    sess.pendingDiscSeq = null
                } else {
                    sess.pendingDiscSeq = hdr.second
                    sess.pendingZeroDiscAt = 0L
                }
                sendCanonicalInform(addr)
                return
            }
            val initProfile = if (protocolMode == "auto") PROFILE_PENDING else protocolMode
            sess.resetAll(initProfile)
            sess.discoverySeq = hdr.second
            sess.handshakeGen++
            val gen = sess.handshakeGen
            if (protocolMode == PROFILE_STANDARD) {
                sess.rxSeqExpected = 0
                sendCanonicalInform(addr)
                return
            }
            sendCanonicalInform(addr)
            scheduleFallback(sess, gen)
        }
    }

    private fun maybeAnswerStatus(data: ByteArray, addr: SocketAddress): Boolean {
        if (data.size < 10) return false
        val hdr = unpackHeader(data)
        val svc = u16Le(data, 2)
        if (hdr.first != PKT_DISCOVERY || svc != SVC_STATUS) return false
        for (i in STATUS_MAGIC.indices) {
            if (data[6 + i] != STATUS_MAGIC[i]) return false
        }
        val state = if (sessions.values.any { it.rxStreaming || it.writeHandle >= 0 }) 2 else 1
        var flags = 1
        if (effectiveReadOnly) flags = flags or 8
        val nameBytes = "PS2 Servers (Android)".toByteArray(Charsets.UTF_8)
        val uptime = ((System.currentTimeMillis() - startTimeMs) / 1000L).toInt().coerceAtLeast(0)
        val out = ByteArray(19 + nameBytes.size)
        val hdrB = packHeader(PKT_INFORM, 0)
        out[0] = hdrB[0]
        out[1] = hdrB[1]
        val bb = ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN)
        bb.putShort(2, SVC_STATUS.toShort())
        System.arraycopy(STATUS_MAGIC, 0, out, 4, 4)
        out[8] = 1
        out[9] = state.toByte()
        bb.putShort(10, flags.toShort())
        bb.putShort(12, minOf(sessions.size, 0xFFFF).toShort())
        bb.putInt(14, uptime)
        out[18] = nameBytes.size.toByte()
        System.arraycopy(nameBytes, 0, out, 19, nameBytes.size)
        sendRaw(discChannel!!, out, addr)
        return true
    }

    private fun sendCanonicalInform(addr: SocketAddress) {
        val pkt = packHeader(PKT_INFORM, 1) + packDiscHeader(UDPRDMA_SVC_UDPFS, actualDataPort)
        sendRaw(dataChannel!!, pkt, addr)
    }

    private fun sendCompatibilityInform(sess: PeerSession) {
        val pkt = packHeader(PKT_INFORM, sess.txSeqNr) +
            packDiscHeader(UDPRDMA_SVC_UDPFS, 0) + moduloInfoPayload()
        sendRaw(discChannel!!, pkt, sess.addr)
        sess.txSeqAcked = sess.txSeqNr
        sess.txSeqNr = (sess.txSeqNr + 1) and 0xFFF
        sess.fallbackSent = true
    }

    private fun scheduleFallback(sess: PeerSession, gen: Long) {
        fallbackScheduler?.schedule({
            if (!running.get() || sessions[sess.addr] !== sess) return@schedule
            synchronized(sess.lock) {
                if (sess.handshakeGen != gen ||
                    sess.protocolProfile != PROFILE_PENDING ||
                    sess.firstDataSeen ||
                    sess.rxStreaming ||
                    sess.fallbackSent
                ) {
                    return@schedule
                }
                sendCompatibilityInform(sess)
            }
        }, fallbackIntervalMs, TimeUnit.MILLISECONDS)
    }

    private fun moduloInfoPayload(): ByteArray {
        val name = "PS2-Servers".toByteArray(Charsets.UTF_8)
        val rawLen = 2 + name.size
        val padded = (rawLen + 3) and 3.inv()
        return ByteArray(padded).also {
            it[0] = name.size.toByte()
            System.arraycopy(name, 0, it, 1, name.size)
            it[1 + name.size] = 0
        }
    }

    private fun routeData(data: ByteArray, addr: SocketAddress, ingress: String) {
        val hdr = unpackHeader(data)
        if (hdr.first != PKT_DATA) return
        val dataHdr = unpackDataHeader(data)
        val payloadSize = dataHdr.hdrWords * 4 + dataHdr.dataBytes
        if (payloadSize > data.size - 6) return
        val (sess, _) = getOrCreateSession(addr)
        sess.enqueue(InboundItem(data, addr, ingress))
    }

    private fun handleData(sess: PeerSession, data: ByteArray, ingress: String) {
        if (data.size < 6) return
        val hdr = unpackHeader(data)
        if (hdr.first != PKT_DATA) return
        val dataHdr = unpackDataHeader(data)
        val hdrSize = dataHdr.hdrWords * 4
        val payloadSize = hdrSize + dataHdr.dataBytes
        val payloadEnd = minOf(data.size, 6 + payloadSize)
        val payload = data.copyOfRange(6, payloadEnd)

        synchronized(sess.lock) {
            val pending = sess.pendingDiscSeq
            if (pending != null && payloadSize > 0) {
                if (hdr.second == sess.rxSeqExpected) {
                    sess.pendingDiscSeq = null
                } else if (hdr.second == ((pending + 1) and 0xFFF)) {
                    val initProf = if (protocolMode == "auto") PROFILE_PENDING else protocolMode
                    sess.resetTransport(initProf)
                    sess.discoverySeq = pending
                    sess.handshakeGen++
                }
            }
            if (sess.pendingZeroDiscAt != 0L && payloadSize > 0) {
                if (hdr.second == sess.rxSeqExpected) {
                    sess.pendingZeroDiscAt = 0L
                } else if (hdr.second == 0 || hdr.second == 1) {
                    val initProf = if (protocolMode == "auto") PROFILE_PENDING else protocolMode
                    sess.resetTransport(initProf)
                    sess.discoverySeq = 0
                    sess.handshakeGen++
                }
            }
            if (sess.protocolProfile == PROFILE_PENDING && payloadSize > 0) {
                val prof = classifyProfile(sess.discoverySeq, hdr.second)
                sess.protocolProfile = prof
                sess.responseSocket = ingress
                sess.firstDataSeen = true
                if (prof == PROFILE_MODULO) {
                    sess.rxSeqExpected = hdr.second
                    if (!sess.fallbackSent) {
                        sendCompatibilityInform(sess)
                    }
                } else {
                    sess.rxSeqExpected = 0
                    if (sess.fallbackSent) {
                        sess.txSeqNr = 0
                        sess.txSeqAcked = 0
                        sess.txBuffer.clear()
                        sess.fallbackSent = false
                    }
                }
                if (verbose) log("UDPFS [${sess.addr}] protocol=$prof via=$ingress")
            } else if (!sess.rxStreaming) {
                sess.responseSocket = ingress
                if (sess.protocolProfile == PROFILE_MODULO) {
                    sess.rxSeqExpected = hdr.second
                }
            }
        }

        if ((dataHdr.flags and FLAG_ACK) != 0) {
            acknowledgeTx(sess, dataHdr.seqAck)
        }
        if (payloadSize == 0 && (dataHdr.flags and FLAG_ACK) != 0) return
        if (payloadSize == 0 && (dataHdr.flags and FLAG_ACK) == 0) {
            acknowledgeTx(sess, (dataHdr.seqAck - 1) and 0xFFF)
            retransmitFrom(sess, dataHdr.seqAck)
            return
        }

        if (hdr.second != sess.rxSeqExpected) {
            val prev = (sess.rxSeqExpected - 1) and 0xFFF
            if (hdr.second == prev) {
                sendAck(sess, isAck = true)
                if (sess.txBuffer.isNotEmpty()) {
                    retransmitFrom(sess, sess.txBuffer[0].first)
                }
                return
            } else if (hdr.second == 0) {
                sess.txSeqNr = 0
                sess.txSeqAcked = 0
                sess.txBuffer.clear()
                sess.rxSeqExpected = 0
            } else {
                sendAck(sess, isAck = false)
                return
            }
        }

        sess.rxSeqExpected = (sess.rxSeqExpected + 1) and 0xFFF
        sess.rxStreaming = true
        sendAck(sess, isAck = true)
        if (payload.isEmpty()) return

        when (payload[0].toInt() and 0xFF) {
            MSG_OPEN_REQ -> handleOpen(sess, payload)
            MSG_CLOSE_REQ -> handleClose(sess, payload)
            MSG_READ_REQ -> handleRead(sess, payload)
            MSG_WRITE_REQ -> handleWriteReq(sess, payload)
            MSG_WRITE_DATA -> handleWriteData(sess, payload)
            MSG_LSEEK_REQ -> handleLseek(sess, payload)
            MSG_DREAD_REQ -> handleDread(sess, payload)
            MSG_GETSTAT_REQ -> handleGetstat(sess, payload)
            MSG_MKDIR_REQ -> handleMkdir(sess, payload)
            MSG_REMOVE_REQ -> handleRemove(sess, payload)
            MSG_RMDIR_REQ -> handleRmdir(sess, payload)
            MSG_BREAD_REQ -> handleBread(sess, payload)
            MSG_BWRITE_REQ -> handleBwriteReq(sess, payload)
            else -> sendAck(sess, isAck = true)
        }
    }

    private fun handleOpen(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 8) {
            sendOpenReply(sess, -ERR_EINVAL, null)
            return
        }
        val bb = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        val isDir = (payload[1].toInt() and 0xFF) != 0
        val flags = bb.getShort(2).toInt() and 0xFFFF
        val rawPath = readCString(payload, 8)

        val root = vfs
        if (root == null) {
            sendOpenReply(sess, -ERR_EACCES, null)
            return
        }
        val relPath = try {
            PathGuard.normalizeRelative(rawPath, strictWindowsNames = false)
        } catch (_: VfsPathException) {
            sendOpenReply(sess, -ERR_EACCES, null)
            return
        }

        if (isDir) {
            val dirEntry = root.stat(relPath)
            if (dirEntry == null || !dirEntry.isDirectory) {
                sendOpenReply(sess, -ERR_ENOENT, null)
                return
            }
            if (sess.handles.size >= MAX_HANDLES) {
                sendOpenReply(sess, -ERR_EMFILE, null)
                return
            }
            val listed = try {
                prepareDirectoryEntries(root, root.list(relPath))
            } catch (_: Exception) {
                sendOpenReply(sess, -ERR_EIO, null)
                return
            }
            val hid = sess.nextHandle++
            sess.handles[hid] = OpenHandle(isDir = true, dirState = DirListingState(listed))
            sendOpenReply(sess, hid, entryToStat(dirEntry, null))
            return
        }

        if (effectiveReadOnly && (flags and 0x02) != 0) {
            sendOpenReply(sess, -ERR_EACCES, null)
            return
        }

        val realEntry = root.stat(relPath)
        var openedCompressed: Pair<String, VfsRandomAccess>? = null
        if (realEntry == null && enableCompression) {
            openedCompressed = CompressedIso.resolveVirtualSibling(root, relPath)
        }

        if (realEntry == null && openedCompressed == null && (flags and 0x0200) == 0) {
            sendOpenReply(sess, -ERR_ENOENT, null)
            return
        }
        if (sess.handles.size >= MAX_HANDLES) {
            openedCompressed?.second?.close()
            sendOpenReply(sess, -ERR_EMFILE, null)
            return
        }

        try {
            if (openedCompressed != null) {
                val (compPath, compRa) = openedCompressed
                val compEntry = root.stat(compPath)
                val statInfo = entryToStat(compEntry ?: VfsEntry(compPath, compPath, false, compRa.size(), 0L), compRa.size())
                val cursor = VfsCursorHandle(compRa, writable = false)
                val hid = sess.nextHandle++
                sess.handles[hid] = OpenHandle(isDir = false, cursor = cursor)
                sendOpenReply(sess, hid, statInfo)
                return
            }

            val accessBits = flags and 0x03
            val writable = accessBits == 0x02 || accessBits == 0x03
            val ra = if (writable) {
                val create = (flags and 0x0200) != 0
                val trunc = (flags and 0x0400) != 0
                val opened = root.openWrite(relPath, create = create, truncate = trunc)
                val cursor = VfsCursorHandle(opened, writable = true)
                if ((flags and 0x0100) != 0) {
                    cursor.seek(0L, 2)
                }
                cursor
            } else {
                VfsCursorHandle(root.openRead(relPath), writable = false)
            }
            val updatedEntry = root.stat(relPath)
            val statInfo = entryToStat(updatedEntry ?: VfsEntry(relPath, relPath, false, ra.access.size(), 0L), null)
            val hid = sess.nextHandle++
            sess.handles[hid] = OpenHandle(isDir = false, cursor = ra)
            sendOpenReply(sess, hid, statInfo)
        } catch (_: Exception) {
            sendOpenReply(sess, -ERR_EIO, null)
        }
    }

    private fun prepareDirectoryEntries(root: VfsRoot, entries: List<VfsEntry>): List<AdvertisedDirEntry> {
        val realNames = entries.map { it.name }.toSet()
        val out = ArrayList<AdvertisedDirEntry>(entries.size)
        for (entry in entries) {
            if (!enableCompression || entry.isDirectory || !CompressedIso.isCompressedName(entry.name)) {
                out.add(AdvertisedDirEntry(entry.name, entry, null))
                continue
            }
            val display = CompressedIso.virtualIsoName(entry.name)
            if (display == entry.name || display in realNames || display.toByteArray(Charsets.UTF_8).size > 255) {
                out.add(AdvertisedDirEntry(entry.name, entry, null))
                continue
            }
            val probedSize = try {
                root.openRead(entry.relativePath).use { CompressedIso.probeInfo(it) }
            } catch (_: Exception) {
                null
            }
            if (probedSize == null) {
                out.add(AdvertisedDirEntry(entry.name, entry, null))
            } else {
                out.add(AdvertisedDirEntry(display, entry, probedSize))
            }
        }
        return out
    }

    private fun handleClose(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 8) {
            sendCloseReply(sess, -ERR_EINVAL)
            return
        }
        val handle = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN).getInt(4)
        if (handle == BLOCK_DEVICE_HANDLE) {
            sendCloseReply(sess, 0)
            return
        }
        val existing = sess.handles.remove(handle)
        if (existing == null) {
            sendCloseReply(sess, -ERR_EBADF)
            return
        }
        try {
            existing.close()
        } catch (_: Exception) {
        }
        sendCloseReply(sess, 0)
    }

    private fun handleRead(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 12) {
            sendReadResult(sess, -ERR_EINVAL, ByteArray(0))
            return
        }
        val bb = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        val handle = bb.getInt(4)
        val sizeLong = bb.getInt(8).toLong() and 0xFFFFFFFFL
        if (sizeLong > effectiveMaxTransfer) {
            sendReadResult(sess, -ERR_EINVAL, ByteArray(0))
            return
        }
        val oh = sess.handles[handle]
        if (oh == null || oh.isDir || oh.cursor == null) {
            sendReadResult(sess, -ERR_EBADF, ByteArray(0))
            return
        }
        try {
            val data = oh.cursor.read(sizeLong.toInt())
            sendReadResult(sess, data.size, data)
        } catch (_: Exception) {
            sendReadResult(sess, -ERR_EIO, ByteArray(0))
        }
    }

    private fun handleWriteReq(sess: PeerSession, payload: ByteArray) {
        sess.resetWrite()
        if (payload.size < 12) {
            sendAck(sess, isAck = true)
            return
        }
        val bb = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        val handle = bb.getInt(4)
        val sizeLong = bb.getInt(8).toLong() and 0xFFFFFFFFL
        if (effectiveReadOnly) {
            sendWriteDone(sess, -ERR_EACCES)
            return
        }
        if (sizeLong > effectiveMaxTransfer) {
            sendWriteDone(sess, -ERR_EINVAL)
            return
        }
        val oh = sess.handles[handle]
        if (oh == null || oh.isDir || oh.cursor == null) {
            sendWriteDone(sess, -ERR_EBADF)
            return
        }
        if (!oh.cursor.writable) {
            sendWriteDone(sess, -ERR_EACCES)
            return
        }
        sess.writeHandle = handle
        if (payload.size > 12) {
            handleWriteData(sess, payload.copyOfRange(12, payload.size))
        } else {
            sendAck(sess, isAck = true)
        }
    }

    private fun handleWriteData(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 8) {
            sendAck(sess, isAck = true)
            return
        }
        val chunkNr = u16Le(payload, 2)
        val chunkSize = u16Le(payload, 4)
        val totalChunks = u16Le(payload, 6)
        if (sess.writeHandle < 0) {
            sendAck(sess, isAck = true)
            return
        }
        if (chunkNr != sess.writeReceivedChunks) {
            sendAck(sess, isAck = true)
            return
        }
        val avail = minOf(chunkSize, payload.size - 8).coerceAtLeast(0)
        if (sess.writeLen + avail > effectiveMaxTransfer) {
            sess.resetWrite()
            sendWriteDone(sess, -ERR_EINVAL)
            return
        }
        ensureWriteCapacity(sess, sess.writeLen + avail)
        System.arraycopy(payload, 8, sess.writeBuf, sess.writeLen, avail)
        sess.writeLen += avail
        sess.writeTotalChunks = totalChunks
        sess.writeReceivedChunks++
        if (sess.writeReceivedChunks >= totalChunks) {
            if (sess.writeIsBlock) completeBwrite(sess) else completeWrite(sess)
        } else {
            sendAck(sess, isAck = true)
        }
    }

    private fun ensureWriteCapacity(sess: PeerSession, needed: Int) {
        if (sess.writeBuf.size >= needed) return
        val newCap = maxOf(needed, maxOf(4096, sess.writeBuf.size * 2))
        sess.writeBuf = sess.writeBuf.copyOf(newCap)
    }

    private fun completeWrite(sess: PeerSession) {
        val handle = sess.writeHandle
        val len = sess.writeLen
        val data = sess.writeBuf.copyOf(len)
        sess.resetWrite()
        val oh = sess.handles[handle]
        if (oh == null || oh.isDir || oh.cursor == null) {
            sendWriteDone(sess, -ERR_EBADF)
            return
        }
        try {
            val written = oh.cursor.write(data, 0, len)
            oh.cursor.flush()
            sendWriteDone(sess, written)
        } catch (_: Exception) {
            sendWriteDone(sess, -ERR_EIO)
        }
    }

    private fun completeBwrite(sess: PeerSession) {
        val handle = sess.writeHandle
        val sectorNr = sess.writeSectorNr
        val sectorCount = sess.writeSectorCount
        val data = sess.writeBuf.copyOf(sess.writeLen)
        sess.resetWrite()

        val secSize = if (handle == BLOCK_DEVICE_HANDLE) sectorSize else 512
        val expected = sectorCount * secSize
        try {
            if (handle == BLOCK_DEVICE_HANDLE) {
                val dev = bdHandle ?: run {
                    sendWriteDone(sess, -ERR_EBADF)
                    return
                }
                val toWrite = minOf(expected, data.size)
                var off = 0
                while (off < toWrite) {
                    val n = dev.writeAt(sectorNr * secSize + off, data, off, toWrite - off)
                    if (n <= 0) throw IOException("Short block write")
                    off += n
                }
                dev.flush()
            } else {
                val oh = sess.handles[handle]
                if (oh == null || oh.isDir || oh.cursor == null) {
                    sendWriteDone(sess, -ERR_EBADF)
                    return
                }
                oh.cursor.seek(sectorNr * secSize, 0)
                val toWrite = minOf(expected, data.size)
                oh.cursor.write(data, 0, toWrite)
                oh.cursor.flush()
            }
            sendWriteDone(sess, 0)
        } catch (_: Exception) {
            sendWriteDone(sess, -ERR_EIO)
        }
    }

    private fun handleLseek(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 16) {
            sendLseekReply(sess, -ERR_EINVAL.toLong())
            return
        }
        val bb = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        val whence = payload[1].toInt() and 0xFF
        val handle = bb.getInt(4)
        val lo = bb.getInt(8).toLong() and 0xFFFFFFFFL
        val hi = bb.getInt(12).toLong()
        val offset = (hi shl 32) or lo

        val oh = sess.handles[handle]
        if (oh == null || oh.isDir || oh.cursor == null) {
            sendLseekReply(sess, -ERR_EBADF.toLong())
            return
        }
        try {
            val pos = oh.cursor.seek(offset, whence)
            sendLseekReply(sess, pos)
        } catch (_: Exception) {
            sendLseekReply(sess, -ERR_EINVAL.toLong())
        }
    }

    private fun handleDread(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 8) {
            sendDreadReply(sess, -ERR_EINVAL, "", null)
            return
        }
        val handle = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN).getInt(4)
        val oh = sess.handles[handle]
        if (oh == null || !oh.isDir || oh.dirState == null) {
            sendDreadReply(sess, -ERR_EBADF, "", null)
            return
        }
        val state = oh.dirState
        if (state.index >= state.entries.size) {
            sendDreadReply(sess, 0, "", null)
            return
        }
        val item = state.entries[state.index++]
        sendDreadReply(sess, 1, item.displayName, entryToStat(item.entry, item.virtualCompressedSize))
    }

    private fun handleGetstat(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 4) {
            sendGetstatReply(sess, -ERR_EINVAL, null)
            return
        }
        val rawPath = readCString(payload, 4)
        if (rawPath.isEmpty() && bdHandle != null) {
            val totalBytes = bdSectorCount * sectorSize
            sendGetstatReply(
                sess,
                0,
                StatInfo(mode = 0, attr = 0, size = totalBytes, ctime = ByteArray(8), atime = ByteArray(8), mtime = ByteArray(8))
            )
            return
        }
        val root = vfs
        if (root == null) {
            sendGetstatReply(sess, -ERR_EACCES, null)
            return
        }
        val relPath = try {
            PathGuard.normalizeRelative(rawPath, strictWindowsNames = false)
        } catch (_: VfsPathException) {
            sendGetstatReply(sess, -ERR_EACCES, null)
            return
        }

        val entry = root.stat(relPath)
        if (entry != null) {
            sendGetstatReply(sess, 0, entryToStat(entry, null))
            return
        }
        if (enableCompression) {
            val sibling = CompressedIso.resolveVirtualSibling(root, relPath)
            if (sibling != null) {
                val (compPath, ra) = sibling
                val virtualSize = ra.use { it.size() }
                val baseEntry = root.stat(compPath) ?: VfsEntry(compPath, compPath, false, virtualSize, 0L)
                sendGetstatReply(sess, 0, entryToStat(baseEntry, virtualSize))
                return
            }
        }
        sendGetstatReply(sess, -ERR_ENOENT, null)
    }

    private fun handleMkdir(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 4) {
            sendResultReply(sess, -ERR_EINVAL)
            return
        }
        if (effectiveReadOnly) {
            sendResultReply(sess, -ERR_EACCES)
            return
        }
        val root = vfs ?: run {
            sendResultReply(sess, -ERR_EACCES)
            return
        }
        val relPath = try {
            PathGuard.normalizeRelative(readCString(payload, 4))
        } catch (_: VfsPathException) {
            sendResultReply(sess, -ERR_EACCES)
            return
        }
        try {
            sendResultReply(sess, if (root.mkdir(relPath)) 0 else -ERR_EEXIST)
        } catch (_: Exception) {
            sendResultReply(sess, -ERR_EIO)
        }
    }

    private fun handleRemove(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 4) {
            sendResultReply(sess, -ERR_EINVAL)
            return
        }
        if (effectiveReadOnly) {
            sendResultReply(sess, -ERR_EACCES)
            return
        }
        val root = vfs ?: run {
            sendResultReply(sess, -ERR_EACCES)
            return
        }
        val relPath = try {
            PathGuard.normalizeRelative(readCString(payload, 4))
        } catch (_: VfsPathException) {
            sendResultReply(sess, -ERR_EACCES)
            return
        }
        val existing = root.stat(relPath)
        if (existing == null) {
            sendResultReply(sess, -ERR_ENOENT)
            return
        }
        if (existing.isDirectory) {
            sendResultReply(sess, -ERR_EISDIR)
            return
        }
        sendResultReply(sess, if (root.deleteFile(relPath)) 0 else -ERR_EIO)
    }

    private fun handleRmdir(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 4) {
            sendResultReply(sess, -ERR_EINVAL)
            return
        }
        if (effectiveReadOnly) {
            sendResultReply(sess, -ERR_EACCES)
            return
        }
        val root = vfs ?: run {
            sendResultReply(sess, -ERR_EACCES)
            return
        }
        val relPath = try {
            PathGuard.normalizeRelative(readCString(payload, 4))
        } catch (_: VfsPathException) {
            sendResultReply(sess, -ERR_EACCES)
            return
        }
        if (relPath.isEmpty()) {
            sendResultReply(sess, -ERR_EACCES)
            return
        }
        val existing = root.stat(relPath)
        if (existing == null) {
            sendResultReply(sess, -ERR_ENOENT)
            return
        }
        if (!existing.isDirectory) {
            sendResultReply(sess, -ERR_ENOTDIR)
            return
        }
        sendResultReply(sess, if (root.deleteDirectory(relPath)) 0 else -ERR_ENOTEMPTY)
    }

    private fun handleBread(sess: PeerSession, payload: ByteArray) {
        if (payload.size < 16) {
            sendReadResult(sess, -ERR_EINVAL, ByteArray(0))
            return
        }
        val bb = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        val sectorCount = bb.getShort(2).toInt() and 0xFFFF
        val handle = bb.getInt(4)
        val secLo = bb.getInt(8).toLong() and 0xFFFFFFFFL
        val secHi = bb.getInt(12).toLong() and 0xFFFFFFFFL
        val sectorNr = secLo or (secHi shl 32)

        val secSize = if (handle == BLOCK_DEVICE_HANDLE) sectorSize else 512
        val totalSize = sectorCount.toLong() * secSize.toLong()
        if (totalSize > effectiveMaxTransfer || sectorNr > Long.MAX_VALUE / secSize) {
            sendReadResult(sess, -ERR_EINVAL, ByteArray(0))
            return
        }

        if (handle == BLOCK_DEVICE_HANDLE) {
            val dev = bdHandle ?: run {
                sendReadResult(sess, -ERR_EBADF, ByteArray(0))
                return
            }
            try {
                val out = ByteArray(totalSize.toInt())
                var got = 0
                val offBase = sectorNr * secSize
                while (got < out.size) {
                    val n = dev.readAt(offBase + got, out, got, out.size - got)
                    if (n <= 0) break
                    got += n
                }
                val sliced = if (got == out.size) out else out.copyOf(got)
                sendReadResult(sess, sliced.size, sliced)
            } catch (_: Exception) {
                sendReadResult(sess, -ERR_EIO, ByteArray(0))
            }
            return
        }

        val oh = sess.handles[handle]
        if (oh == null || oh.isDir || oh.cursor == null) {
            sendReadResult(sess, -ERR_EBADF, ByteArray(0))
            return
        }
        try {
            oh.cursor.seek(sectorNr * secSize, 0)
            val data = oh.cursor.read(totalSize.toInt())
            sendReadResult(sess, data.size, data)
        } catch (_: Exception) {
            sendReadResult(sess, -ERR_EIO, ByteArray(0))
        }
    }

    private fun handleBwriteReq(sess: PeerSession, payload: ByteArray) {
        sess.resetWrite()
        if (payload.size < 16) {
            sendAck(sess, isAck = true)
            return
        }
        if (effectiveReadOnly) {
            sendWriteDone(sess, -ERR_EACCES)
            return
        }
        val bb = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        val sectorCount = bb.getShort(2).toInt() and 0xFFFF
        val handle = bb.getInt(4)
        val secLo = bb.getInt(8).toLong() and 0xFFFFFFFFL
        val secHi = bb.getInt(12).toLong() and 0xFFFFFFFFL
        val sectorNr = secLo or (secHi shl 32)
        val secSize = if (handle == BLOCK_DEVICE_HANDLE) sectorSize else 512
        val declared = sectorCount.toLong() * secSize.toLong()
        if (declared > effectiveMaxTransfer || sectorNr > Long.MAX_VALUE / secSize) {
            sendWriteDone(sess, -ERR_EINVAL)
            return
        }
        if (handle == BLOCK_DEVICE_HANDLE) {
            if (bdHandle == null) {
                sendWriteDone(sess, -ERR_EBADF)
                return
            }
        } else {
            val oh = sess.handles[handle]
            if (oh == null || oh.isDir || oh.cursor == null) {
                sendWriteDone(sess, -ERR_EBADF)
                return
            }
            if (!oh.cursor.writable) {
                sendWriteDone(sess, -ERR_EACCES)
                return
            }
        }
        sess.writeHandle = handle
        sess.writeIsBlock = true
        sess.writeSectorNr = sectorNr
        sess.writeSectorCount = sectorCount
        if (payload.size > 16) {
            handleWriteData(sess, payload.copyOfRange(16, payload.size))
        } else {
            sendAck(sess, isAck = true)
        }
    }

    private class StatInfo(
        val mode: Int,
        val attr: Int,
        val size: Long,
        val ctime: ByteArray,
        val atime: ByteArray,
        val mtime: ByteArray
    )

    private fun entryToStat(entry: VfsEntry, overrideSize: Long?): StatInfo {
        val sz = overrideSize ?: entry.size
        val mtime = encodePs2Time(entry.lastModifiedMs)
        return StatInfo(
            mode = if (entry.isDirectory) FIO_S_IFDIR else FIO_S_IFREG,
            attr = 0,
            size = sz,
            ctime = mtime,
            atime = mtime,
            mtime = mtime
        )
    }

    private fun encodePs2Time(ms: Long): ByteArray {
        if (ms <= 0L) return ByteArray(8)
        val cal = Calendar.getInstance(TimeZone.getDefault()).apply { timeInMillis = ms }
        val out = ByteArray(8)
        out[0] = 0
        out[1] = cal.get(Calendar.SECOND).toByte()
        out[2] = cal.get(Calendar.MINUTE).toByte()
        out[3] = cal.get(Calendar.HOUR_OF_DAY).toByte()
        out[4] = cal.get(Calendar.DAY_OF_MONTH).toByte()
        out[5] = (cal.get(Calendar.MONTH) + 1).toByte()
        val year = cal.get(Calendar.YEAR)
        out[6] = (year and 0xFF).toByte()
        out[7] = ((year ushr 8) and 0xFF).toByte()
        return out
    }

    // ---- Reply builders & UDPRDMA transport ----

    private fun sendOpenReply(sess: PeerSession, handle: Int, stat: StatInfo?) {
        val s = stat ?: StatInfo(0, 0, 0L, ByteArray(8), ByteArray(8), ByteArray(8))
        val out = ByteArray(40)
        val bb = ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN)
        out[0] = MSG_OPEN_REPLY.toByte()
        bb.putInt(4, handle)
        bb.putInt(8, s.mode)
        bb.putInt(12, (s.size and 0xFFFFFFFFL).toInt())
        bb.putInt(16, ((s.size ushr 32) and 0xFFFFFFFFL).toInt())
        System.arraycopy(s.ctime, 0, out, 24, 8)
        System.arraycopy(s.mtime, 0, out, 32, 8)
        sendData(sess, out)
    }

    private fun sendCloseReply(sess: PeerSession, result: Int) {
        val out = ByteArray(8)
        out[0] = MSG_CLOSE_REPLY.toByte()
        ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN).putInt(4, result)
        sendData(sess, out)
    }

    private fun sendResultReply(sess: PeerSession, result: Int) {
        val out = ByteArray(8)
        out[0] = MSG_RESULT_REPLY.toByte()
        ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN).putInt(4, result)
        sendData(sess, out)
    }

    private fun sendWriteDone(sess: PeerSession, result: Int) {
        val out = ByteArray(8)
        out[0] = MSG_WRITE_DONE.toByte()
        ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN).putInt(4, result)
        sendData(sess, out)
    }

    private fun sendLseekReply(sess: PeerSession, pos: Long) {
        val out = ByteArray(12)
        val bb = ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN)
        out[0] = MSG_LSEEK_REPLY.toByte()
        if (pos < 0L) {
            bb.putInt(4, pos.toInt())
            bb.putInt(8, -1)
        } else {
            bb.putInt(4, (pos and 0xFFFFFFFFL).toInt())
            bb.putInt(8, ((pos ushr 32) and 0xFFFFFFFFL).toInt())
        }
        sendData(sess, out)
    }

    private fun sendDreadReply(sess: PeerSession, result: Int, name: String, stat: StatInfo?) {
        val s = stat ?: StatInfo(0, 0, 0L, ByteArray(8), ByteArray(8), ByteArray(8))
        val nameRaw = name.toByteArray(Charsets.UTF_8)
        val nameWithNul = nameRaw + byteArrayOf(0)
        val paddedNameLen = (nameWithNul.size + 3) and 3.inv()
        val extra = if (result > 0) paddedNameLen else 0
        val out = ByteArray(48 + extra)
        val bb = ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN)
        out[0] = MSG_DREAD_REPLY.toByte()
        bb.putShort(2, nameRaw.size.toShort())
        bb.putInt(4, result)
        bb.putInt(8, s.mode)
        bb.putInt(12, s.attr)
        bb.putInt(16, (s.size and 0xFFFFFFFFL).toInt())
        bb.putInt(20, ((s.size ushr 32) and 0xFFFFFFFFL).toInt())
        System.arraycopy(s.ctime, 0, out, 24, 8)
        System.arraycopy(s.atime, 0, out, 32, 8)
        System.arraycopy(s.mtime, 0, out, 40, 8)
        if (result > 0) {
            System.arraycopy(nameWithNul, 0, out, 48, nameWithNul.size)
        }
        sendData(sess, out)
    }

    private fun sendGetstatReply(sess: PeerSession, result: Int, stat: StatInfo?) {
        val s = stat ?: StatInfo(0, 0, 0L, ByteArray(8), ByteArray(8), ByteArray(8))
        val out = ByteArray(48)
        val bb = ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN)
        out[0] = MSG_GETSTAT_REPLY.toByte()
        bb.putInt(4, result)
        bb.putInt(8, s.mode)
        bb.putInt(12, s.attr)
        bb.putInt(16, (s.size and 0xFFFFFFFFL).toInt())
        bb.putInt(20, ((s.size ushr 32) and 0xFFFFFFFFL).toInt())
        System.arraycopy(s.ctime, 0, out, 24, 8)
        System.arraycopy(s.atime, 0, out, 32, 8)
        System.arraycopy(s.mtime, 0, out, 40, 8)
        sendData(sess, out)
    }

    private fun sendReadResult(sess: PeerSession, result: Int, data: ByteArray) {
        val hdr = ByteArray(8)
        hdr[0] = MSG_RESULT_REPLY.toByte()
        ByteBuffer.wrap(hdr).order(ByteOrder.LITTLE_ENDIAN).putInt(4, result)
        sendRawDataWithHeader(sess, hdr, data)
    }

    private fun sendAck(sess: PeerSession, isAck: Boolean) {
        val pkt = ByteArray(6)
        val h = packHeader(PKT_DATA, sess.txSeqNr)
        val d = packDataHeader(
            seqAck = if (isAck) (sess.rxSeqExpected - 1) and 0xFFF else sess.rxSeqExpected,
            flags = if (isAck) FLAG_ACK else 0,
            hdrWords = 0,
            dataBytes = 0
        )
        pkt[0] = h[0]
        pkt[1] = h[1]
        System.arraycopy(d, 0, pkt, 2, 4)
        sendSessionPacket(sess, pkt)
    }

    private fun sendData(sess: PeerSession, payload: ByteArray) {
        val padded = (payload.size + 3) and 3.inv()
        val pkt = ByteArray(6 + padded)
        val h = packHeader(PKT_DATA, sess.txSeqNr)
        val d = packDataHeader(
            seqAck = (sess.rxSeqExpected - 1) and 0xFFF,
            flags = FLAG_FIN,
            hdrWords = 0,
            dataBytes = padded
        )
        pkt[0] = h[0]
        pkt[1] = h[1]
        System.arraycopy(d, 0, pkt, 2, 4)
        System.arraycopy(payload, 0, pkt, 6, payload.size)
        sess.txBuffer.clear()
        sess.txBuffer.add(sess.txSeqNr to pkt)
        sendSessionPacket(sess, pkt)
        sess.txSeqNr = (sess.txSeqNr + 1) and 0xFFF
    }

    private fun sendDataPacket(sess: PeerSession, payload: ByteArray, fin: Boolean, hdrSize: Int = 0) {
        val dataSize = payload.size - hdrSize
        val paddedDataSize = (dataSize + 3) and 3.inv()
        val pkt = ByteArray(6 + hdrSize + paddedDataSize)
        val h = packHeader(PKT_DATA, sess.txSeqNr)
        val d = packDataHeader(
            seqAck = (sess.rxSeqExpected - 1) and 0xFFF,
            flags = if (fin) FLAG_FIN else 0,
            hdrWords = hdrSize / 4,
            dataBytes = paddedDataSize
        )
        pkt[0] = h[0]
        pkt[1] = h[1]
        System.arraycopy(d, 0, pkt, 2, 4)
        System.arraycopy(payload, 0, pkt, 6, payload.size)
        sess.txBuffer.add(sess.txSeqNr to pkt)
        sendSessionPacket(sess, pkt)
        sess.txSeqNr = (sess.txSeqNr + 1) and 0xFFF
    }

    private fun optimalChunkSize(totalBytes: Int): Int {
        if (totalBytes <= 0) return 1408
        val candidates = listOf(1024 to 512, 1280 to 256, 1408 to 128)
        var bestChunk = 1408
        var bestPackets = (totalBytes + 1407) / 1408
        var bestAlign = 128
        for ((maxChunk, align) in candidates) {
            val packets = (totalBytes + maxChunk - 1) / maxChunk
            if (packets < bestPackets || (packets == bestPackets && align > bestAlign)) {
                bestPackets = packets
                bestChunk = maxChunk
                bestAlign = align
            }
        }
        return bestChunk
    }

    private fun sendRawDataWithHeader(sess: PeerSession, appHdr: ByteArray, data: ByteArray) {
        sess.txBuffer.clear()
        val maxChunk = optimalChunkSize(data.size)
        val firstDataMax = minOf(maxChunk, MAX_DATA_PAYLOAD - appHdr.size)
        val firstLen = minOf(firstDataMax, data.size)
        val firstBuf = ByteArray(appHdr.size + firstLen)
        System.arraycopy(appHdr, 0, firstBuf, 0, appHdr.size)
        if (firstLen > 0) System.arraycopy(data, 0, firstBuf, appHdr.size, firstLen)
        sendDataPacket(sess, firstBuf, fin = (firstLen >= data.size), hdrSize = appHdr.size)

        var offset = firstLen
        var retries = 0
        while (offset < data.size && running.get()) {
            if (inFlight(sess) >= SEND_WINDOW) {
                val oldAcked = sess.txSeqAcked
                if (waitForWindowAck(sess)) return
                if (sess.txSeqAcked == oldAcked) {
                    retries++
                    if (retries >= MAX_WINDOW_RETRIES) return
                } else {
                    retries = 0
                }
                continue
            }
            retries = 0
            val chunkLen = minOf(maxChunk, data.size - offset)
            val isLast = offset + chunkLen >= data.size
            sendDataPacket(sess, data.copyOfRange(offset, offset + chunkLen), fin = isLast, hdrSize = 0)
            offset += chunkLen
        }
        waitForFinalAck(sess)
    }

    private fun inFlight(sess: PeerSession): Int = (sess.txSeqNr - sess.txSeqAcked - 1) and 0xFFF

    private fun acknowledgeTx(sess: PeerSession, acknowledged: Int): Boolean {
        val advance = (acknowledged - sess.txSeqAcked) and 0xFFF
        val sent = (sess.txSeqNr - sess.txSeqAcked - 1) and 0xFFF
        if (advance >= 2048 || sent >= 2048 || advance > sent) return false
        sess.txSeqAcked = acknowledged
        val it = sess.txBuffer.iterator()
        while (it.hasNext()) {
            val seq = it.next().first
            if (((seq - acknowledged - 1) and 0xFFF) >= 2048) {
                it.remove()
            }
        }
        return true
    }

    private fun retransmitFrom(sess: PeerSession, fromSeq: Int) {
        val now = System.currentTimeMillis()
        if (sess.lastRetxSeq == fromSeq && now - sess.lastRetxAt < 50L) return
        sess.lastRetxSeq = fromSeq
        sess.lastRetxAt = now
        var sent = 0
        for ((seq, pkt) in sess.txBuffer) {
            if (((seq - fromSeq) and 0xFFF) < 2048) {
                if (sent > 0) Thread.sleep(1L)
                sendSessionPacket(sess, pkt)
                sent++
            }
        }
    }

    private fun waitForWindowAck(sess: PeerSession): Boolean {
        val deadline = System.currentTimeMillis() + WINDOW_ACK_TIMEOUT_MS
        while (running.get()) {
            val rem = deadline - System.currentTimeMillis()
            if (rem <= 0L) {
                if (sess.txBuffer.isNotEmpty()) retransmitFrom(sess, sess.txBuffer[0].first)
                return false
            }
            val item = sess.pollWaitPacket(rem) ?: run {
                if (sess.txBuffer.isNotEmpty()) retransmitFrom(sess, sess.txBuffer[0].first)
                return false
            }
            if (item.data.size < 6) continue
            val hdr = unpackHeader(item.data)
            if (hdr.first == PKT_DISCOVERY) {
                sess.txBuffer.clear()
                sess.pushBack(item)
                return true
            }
            if (hdr.first != PKT_DATA) continue
            val dh = unpackDataHeader(item.data)
            val payloadSize = dh.hdrWords * 4 + dh.dataBytes
            if (payloadSize > 0 && hdr.second == ((sess.rxSeqExpected - 1) and 0xFFF)) {
                sendAck(sess, isAck = true)
                continue
            }
            if ((dh.flags and FLAG_ACK) != 0) {
                val prev = sess.txSeqAcked
                acknowledgeTx(sess, dh.seqAck)
                if (payloadSize > 0) {
                    sess.pushBack(item)
                    return true
                }
                if (sess.txSeqAcked != prev) return false
            } else if (payloadSize == 0) {
                val accepted = acknowledgeTx(sess, (dh.seqAck - 1) and 0xFFF)
                retransmitFrom(sess, dh.seqAck)
                if (accepted && dh.seqAck == sess.txSeqNr) return false
            } else {
                sess.pushBack(item)
                return true
            }
        }
        return false
    }

    private fun waitForAck(sess: PeerSession, timeoutMs: Long): Boolean {
        val finSeq = (sess.txSeqNr - 1) and 0xFFF
        var deadline = System.currentTimeMillis() + timeoutMs
        while (running.get()) {
            val rem = deadline - System.currentTimeMillis()
            if (rem <= 0L) return false
            val item = sess.pollWaitPacket(rem) ?: return false
            if (item.data.size < 6) continue
            val hdr = unpackHeader(item.data)
            if (hdr.first == PKT_DISCOVERY) {
                sess.txBuffer.clear()
                sess.pushBack(item)
                return false
            }
            if (hdr.first != PKT_DATA) continue
            val dh = unpackDataHeader(item.data)
            val payloadSize = dh.hdrWords * 4 + dh.dataBytes
            if (payloadSize > 0 && hdr.second == ((sess.rxSeqExpected - 1) and 0xFFF)) {
                sendAck(sess, isAck = true)
                continue
            }
            if ((dh.flags and FLAG_ACK) != 0) {
                val prev = sess.txSeqAcked
                val accepted = acknowledgeTx(sess, dh.seqAck)
                if (accepted && dh.seqAck == finSeq) {
                    sess.txBuffer.clear()
                    if (payloadSize > 0) sess.pushBack(item)
                    return true
                }
                if (sess.txSeqAcked != prev) deadline = System.currentTimeMillis() + timeoutMs
                if (payloadSize > 0) {
                    sess.pushBack(item)
                    return true
                }
            } else if (payloadSize == 0) {
                val prev = sess.txSeqAcked
                acknowledgeTx(sess, (dh.seqAck - 1) and 0xFFF)
                retransmitFrom(sess, dh.seqAck)
                if (sess.txSeqAcked != prev) deadline = System.currentTimeMillis() + timeoutMs
            } else {
                sess.pushBack(item)
                return true
            }
        }
        return false
    }

    private fun waitForFinalAck(sess: PeerSession) {
        for (attempt in 0..MAX_WINDOW_RETRIES) {
            if (waitForAck(sess, WINDOW_ACK_TIMEOUT_MS)) return
            if (sess.txBuffer.isEmpty() || !running.get()) return
            retransmitFrom(sess, sess.txBuffer[0].first)
        }
    }

    private fun sendSessionPacket(sess: PeerSession, pkt: ByteArray) {
        val ch = if (singlePort || sess.responseSocket == SOCKET_DISCOVERY) discChannel else dataChannel
        if (ch != null) sendRaw(ch, pkt, sess.addr)
    }

    private fun sendRaw(ch: DatagramChannel, pkt: ByteArray, addr: SocketAddress) {
        if (txDelayMs > 0.0) {
            val ms = txDelayMs.toLong()
            val ns = ((txDelayMs - ms) * 1_000_000.0).toInt().coerceIn(0, 999_999)
            Thread.sleep(ms, ns)
        }
        val buf = ByteBuffer.wrap(pkt)
        synchronized(sendLock) {
            for (attempt in 0 until 10) {
                try {
                    buf.rewind()
                    ch.send(buf, addr)
                    return
                } catch (e: IOException) {
                    if (attempt == 9) return
                    Thread.sleep(minOf(10L, (attempt + 1).toLong()))
                }
            }
        }
    }

    private fun readCString(data: ByteArray, start: Int): String {
        var end = start
        while (end < data.size && data[end] != 0.toByte()) end++
        return String(data, start, (end - start).coerceAtLeast(0), Charsets.UTF_8)
    }

    private fun u16Le(data: ByteArray, off: Int): Int =
        (data[off].toInt() and 0xFF) or ((data[off + 1].toInt() and 0xFF) shl 8)

    private fun packHeader(packetType: Int, seqNr: Int): ByteArray {
        val v = (packetType and 0x0F) or ((seqNr and 0xFFF) shl 4)
        return byteArrayOf((v and 0xFF).toByte(), ((v ushr 8) and 0xFF).toByte())
    }

    private fun unpackHeader(data: ByteArray): Pair<Int, Int> {
        val v = u16Le(data, 0)
        return (v and 0x0F) to ((v ushr 4) and 0xFFF)
    }

    private fun packDiscHeader(serviceId: Int, port: Int): ByteArray {
        return byteArrayOf(
            (serviceId and 0xFF).toByte(),
            ((serviceId ushr 8) and 0xFF).toByte(),
            (port and 0xFF).toByte(),
            ((port ushr 8) and 0xFF).toByte()
        )
    }

    private data class DataHdr(val seqAck: Int, val flags: Int, val hdrWords: Int, val dataBytes: Int)

    private fun packDataHeader(seqAck: Int, flags: Int, hdrWords: Int, dataBytes: Int): ByteArray {
        val v = (seqAck and 0xFFF) or
            ((flags and 0x3) shl 12) or
            ((hdrWords and 0xF) shl 14) or
            ((dataBytes and 0x3FFF) shl 18)
        return ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(v).array()
    }

    private fun unpackDataHeader(data: ByteArray): DataHdr {
        val v = ByteBuffer.wrap(data, 2, 4).order(ByteOrder.LITTLE_ENDIAN).int
        return DataHdr(
            seqAck = v and 0xFFF,
            flags = (v ushr 12) and 0x3,
            hdrWords = (v ushr 14) and 0xF,
            dataBytes = (v ushr 18) and 0x3FFF
        )
    }
}
