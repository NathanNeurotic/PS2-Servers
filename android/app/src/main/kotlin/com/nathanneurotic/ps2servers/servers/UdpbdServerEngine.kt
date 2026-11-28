package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.vfs.VfsRandomAccess
import com.nathanneurotic.ps2servers.vfs.VfsRoot
import com.nathanneurotic.ps2servers.vfs.VirtualExfatDevice
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketAddress
import java.net.SocketException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Pure-Kotlin UDPBD v2 server compatible with Open PS2 Loader and Neutrino.
 *
 * Serves either a raw disk image [VfsRandomAccess] or a read-only [VirtualExfatDevice]
 * built over a [VfsRoot] folder on UDP port 0xBDBD (48573).
 */
class UdpbdServerEngine(
    private val vfsRoot: VfsRoot?,
    private val rawDeviceFactory: (() -> VfsRandomAccess)? = null,
    private val port: Int = DEFAULT_PORT,
    private val bindIp: String = "",
    private val readOnly: Boolean = false,
    private val verbose: Boolean = false,
    private val log: (String) -> Unit = {}
) : ServerEngine {
    override val key: String = "udpbd"
    override val displayName: String = "UDPBD"
    override val boundPort: Int
        get() = actualPort
    companion object {
        const val DEFAULT_PORT = 0xBDBD // 48573

        const val CMD_INFO = 0x00
        const val CMD_INFO_REPLY = 0x01
        const val CMD_READ = 0x02
        const val CMD_READ_RDMA = 0x03
        const val CMD_WRITE = 0x04
        const val CMD_WRITE_RDMA = 0x05
        const val CMD_WRITE_DONE = 0x06

        const val SECTOR_SIZE = 512
        const val RDMA_MAX_PAYLOAD = 1472 - 2 - 4 // 1466
    }

    private val running = AtomicBoolean(false)
    private var socket: DatagramSocket? = null
    private var workerThread: Thread? = null
    private var device: VfsRandomAccess? = null
    private var virtualDevice: VirtualExfatDevice? = null
    private var isVirtual = false
    private var effectiveReadOnly = readOnly

    @Volatile
    var actualPort: Int = 0
        private set

    private var positionBytes: Long = 0L
    private var requestEndBytes: Long = 0L
    private var blockShift: Int = -1
    private var blockSize: Int = 128
    private var blocksPerPacket: Int = RDMA_MAX_PAYLOAD / 128
    private var blocksPerSector: Int = SECTOR_SIZE / 128
    private var writeLeft: Int = 0
    private var writeOwner: Pair<SocketAddress, Int>? = null
    private var totalReadBytes: Long = 0L
    private var totalWriteBytes: Long = 0L

    override val isRunning: Boolean
        get() = running.get()

    override fun start() {
        if (!running.compareAndSet(false, true)) return
        ServerPortGuard.ensureBindable(port, redirect445Enabled = false)
        try {
            if (rawDeviceFactory != null) {
                device = rawDeviceFactory.invoke()
                isVirtual = false
                effectiveReadOnly = readOnly || (vfsRoot?.isReadOnly == true)
            } else if (vfsRoot != null) {
                val exfat = VirtualExfatDevice(vfsRoot)
                virtualDevice = exfat
                device = exfat
                isVirtual = true
                effectiveReadOnly = true
            } else {
                throw IOException("UDPBD requires a virtual folder or raw disk image")
            }

            val requestedPort = this.port
            val requestedBindIp = this.bindIp.trim()
            val bindAddr = if (requestedBindIp.isEmpty()) {
                InetSocketAddress(requestedPort)
            } else {
                InetSocketAddress(InetAddress.getByName(requestedBindIp), requestedPort)
            }
            val sock = DatagramSocket(null).apply {
                reuseAddress = true
                broadcast = true
                try {
                    receiveBufferSize = 1 shl 20
                    sendBufferSize = 1 shl 20
                } catch (_: Exception) {
                }
                bind(bindAddr)
            }
            socket = sock
            actualPort = sock.localPort
            setBlockShift(5)

            val dev = device!!
            val modeLabel = if (effectiveReadOnly) "read-only" else "read/write"
            val kindLabel = if (isVirtual) "Virtual exFAT (${vfsRoot?.displayPath})" else "Raw image"
            log("UDPBD listening on ${bindIp.ifBlank { "0.0.0.0" }}:$actualPort ($kindLabel, $modeLabel, ${dev.size() / SECTOR_SIZE} sectors)")

            val thread = Thread({ runLoop(sock, dev) }, "ps2-udpbd")
            thread.isDaemon = true
            workerThread = thread
            thread.start()
        } catch (e: Exception) {
            running.set(false)
            cleanup()
            throw e
        }
    }

    override fun stop() {
        running.set(false)
        try {
            socket?.close()
        } catch (_: Exception) {
        }
        try {
            workerThread?.join(1500L)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        cleanup()
    }

    private fun cleanup() {
        try {
            device?.close()
        } catch (_: Exception) {
        }
        device = null
        virtualDevice = null
        socket = null
        workerThread = null
    }

    private fun runLoop(sock: DatagramSocket, dev: VfsRandomAccess) {
        val buf = ByteArray(2048)
        while (running.get()) {
            val packet = DatagramPacket(buf, buf.size)
            try {
                sock.receive(packet)
            } catch (_: SocketException) {
                break
            } catch (e: IOException) {
                if (!running.get()) break
                if (verbose) log("UDPBD recv warning: ${e.message}")
                continue
            }
            val len = packet.length
            if (len < 2) continue
            val data = packet.data.copyOf(len)
            val addr = packet.socketAddress
            val cmd = data[0].toInt() and 0x1F
            try {
                when (cmd) {
                    CMD_INFO -> handleInfo(sock, dev, addr, data)
                    CMD_READ -> if (len >= 8) handleRead(sock, dev, addr, data)
                    CMD_WRITE -> if (len >= 8) handleWrite(sock, dev, addr, data)
                    CMD_WRITE_RDMA -> if (len >= 6) handleWriteRdma(sock, dev, addr, data)
                    else -> if (verbose) log("UDPBD ignored unknown cmd=0x${cmd.toString(16)} from $addr")
                }
            } catch (e: Exception) {
                log("UDPBD error handling cmd=0x${cmd.toString(16)}: ${e.message}")
            }
        }
    }

    private fun setBlockShift(shift: Int) {
        if (shift == blockShift) return
        blockShift = shift
        blockSize = 1 shl (shift + 2)
        blocksPerPacket = RDMA_MAX_PAYLOAD / blockSize
        blocksPerSector = SECTOR_SIZE / blockSize
    }

    private fun setBlockShiftForSectors(sectors: Int) {
        val size = sectors * SECTOR_SIZE
        val packetsMin = ceilDiv(size, 1440)
        val shift = when {
            ceilDiv(size, 1024) == packetsMin -> 7
            ceilDiv(size, 1280) == packetsMin -> 6
            ceilDiv(size, 1408) == packetsMin -> 5
            else -> 3
        }
        setBlockShift(shift)
    }

    private fun ceilDiv(a: Int, b: Int): Int = if (a <= 0) 0 else (a + b - 1) / b

    private fun packHeader(cmd: Int, cmdId: Int, cmdPkt: Int): ByteArray {
        val v = (cmd and 0x1F) or ((cmdId and 0x07) shl 5) or ((cmdPkt and 0xFF) shl 8)
        return byteArrayOf((v and 0xFF).toByte(), ((v ushr 8) and 0xFF).toByte())
    }

    private fun unpackHeader(data: ByteArray): Triple<Int, Int, Int> {
        val v = (data[0].toInt() and 0xFF) or ((data[1].toInt() and 0xFF) shl 8)
        return Triple(v and 0x1F, (v ushr 5) and 0x07, (v ushr 8) and 0xFF)
    }

    private fun packBlockType(shift: Int, count: Int): ByteArray {
        val v = (shift and 0x0F) or ((count and 0x1FF) shl 4)
        return ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(v).array()
    }

    private fun unpackBlockType(data: ByteArray, offset: Int = 2): Pair<Int, Int> {
        val v = ByteBuffer.wrap(data, offset, 4).order(ByteOrder.LITTLE_ENDIAN).int
        return (v and 0x0F) to ((v ushr 4) and 0x1FF)
    }

    private fun send(sock: DatagramSocket, payload: ByteArray, addr: SocketAddress) {
        sock.send(DatagramPacket(payload, payload.size, addr))
    }

    private fun handleInfo(sock: DatagramSocket, dev: VfsRandomAccess, addr: SocketAddress, data: ByteArray) {
        val (_, cmdId, _) = unpackHeader(data)
        writeLeft = 0
        val sectorCount = (dev.size() / SECTOR_SIZE).toInt()
        log("UDPBD_CMD_INFO from $addr (readKiB=${totalReadBytes / 1024}, writeKiB=${totalWriteBytes / 1024})")
        val reply = ByteArray(10)
        System.arraycopy(packHeader(CMD_INFO_REPLY, cmdId, 1), 0, reply, 0, 2)
        ByteBuffer.wrap(reply, 2, 8).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(SECTOR_SIZE)
            .putInt(sectorCount)
        send(sock, reply, addr)
    }

    private fun handleRead(sock: DatagramSocket, dev: VfsRandomAccess, addr: SocketAddress, data: ByteArray) {
        val (_, cmdId, _) = unpackHeader(data)
        val bb = ByteBuffer.wrap(data, 0, 8).order(ByteOrder.LITTLE_ENDIAN)
        val sectorNr = bb.getInt(2).toLong() and 0xFFFFFFFFL
        val sectorCount = bb.getShort(6).toInt() and 0xFFFF
        val totalSectors = dev.size() / SECTOR_SIZE
        if (isVirtual && sectorNr + sectorCount > totalSectors) {
            throw IOException("Read request exceeds virtual disk capacity")
        }
        if (verbose) log("UDPBD_CMD_READ(cmdid=$cmdId, start=$sectorNr, count=$sectorCount)")

        writeLeft = 0
        setBlockShiftForSectors(sectorCount)
        var blocksLeft = sectorCount * blocksPerSector
        totalReadBytes += blocksLeft.toLong() * blockSize.toLong()

        val vexfat = virtualDevice
        if (vexfat != null) {
            vexfat.seek(sectorNr, sectorCount.toLong())
        } else {
            positionBytes = sectorNr * SECTOR_SIZE
            requestEndBytes = positionBytes + sectorCount.toLong() * SECTOR_SIZE
        }

        var cmdPkt = 1
        while (blocksLeft > 0) {
            val blockCount = minOf(blocksLeft, blocksPerPacket)
            blocksLeft -= blockCount
            val chunkLen = blockCount * blockSize
            val chunk = if (vexfat != null) {
                vexfat.readSequential(chunkLen)
            } else {
                val out = ByteArray(chunkLen)
                var got = 0
                while (got < chunkLen) {
                    val n = dev.readAt(positionBytes + got, out, got, chunkLen - got)
                    if (n <= 0) break
                    got += n
                }
                positionBytes += chunkLen
                out
            }
            val packet = ByteArray(6 + chunk.size)
            System.arraycopy(packHeader(CMD_READ_RDMA, cmdId, cmdPkt), 0, packet, 0, 2)
            System.arraycopy(packBlockType(blockShift, blockCount), 0, packet, 2, 4)
            System.arraycopy(chunk, 0, packet, 6, chunk.size)
            send(sock, packet, addr)
            cmdPkt = (cmdPkt + 1) and 0xFF
        }
    }

    private fun handleWrite(sock: DatagramSocket, dev: VfsRandomAccess, addr: SocketAddress, data: ByteArray) {
        val (_, cmdId, _) = unpackHeader(data)
        val bb = ByteBuffer.wrap(data, 0, 8).order(ByteOrder.LITTLE_ENDIAN)
        val sectorNr = bb.getInt(2).toLong() and 0xFFFFFFFFL
        val sectorCount = bb.getShort(6).toInt() and 0xFFFF
        if (verbose) log("UDPBD_CMD_WRITE(cmdid=$cmdId, start=$sectorNr, count=$sectorCount)")

        writeLeft = 0
        writeOwner = addr to cmdId
        val vexfat = virtualDevice
        try {
            if (vexfat != null) {
                vexfat.seek(sectorNr, sectorCount.toLong())
            } else {
                val pos = sectorNr * SECTOR_SIZE
                val end = pos + sectorCount.toLong() * SECTOR_SIZE
                if (pos < 0 || end > dev.size()) {
                    throw IOException("Write out of bounds")
                }
                positionBytes = pos
                requestEndBytes = end
            }
        } catch (_: Exception) {
            sendWriteDone(sock, addr, cmdId, -1)
            return
        }
        writeLeft = sectorCount * SECTOR_SIZE
        totalWriteBytes += writeLeft.toLong()
    }

    private fun handleWriteRdma(sock: DatagramSocket, dev: VfsRandomAccess, addr: SocketAddress, data: ByteArray) {
        val (_, cmdId, _) = unpackHeader(data)
        if (writeLeft <= 0) return
        if (writeOwner != (addr to cmdId)) return
        val (shift, count) = unpackBlockType(data)
        var size = count * (1 shl (shift + 2))
        if (data.size < 6 + size) return
        size = minOf(size, writeLeft)
        try {
            if (!effectiveReadOnly) {
                var written = 0
                while (written < size) {
                    val n = dev.writeAt(positionBytes + written, data, 6 + written, size - written)
                    if (n <= 0) throw IOException("Short block write")
                    written += n
                }
                positionBytes += size
                if (size == writeLeft) dev.flush()
            }
        } catch (e: Exception) {
            log("UDPBD write error from $addr: ${e.message}")
            writeLeft = 0
            sendWriteDone(sock, addr, cmdId, -1)
            return
        }
        writeLeft -= size
        if (writeLeft <= 0) {
            sendWriteDone(sock, addr, cmdId, if (effectiveReadOnly) -1 else 0)
        }
    }

    private fun sendWriteDone(sock: DatagramSocket, addr: SocketAddress, cmdId: Int, result: Int) {
        val reply = ByteArray(6)
        System.arraycopy(packHeader(CMD_WRITE_DONE, cmdId, (cmdId + 1) and 0xFF), 0, reply, 0, 2)
        ByteBuffer.wrap(reply, 2, 4).order(ByteOrder.LITTLE_ENDIAN).putInt(result)
        send(sock, reply, addr)
    }
}
