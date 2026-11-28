package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.vfs.FileRandomAccess
import com.nathanneurotic.ps2servers.vfs.FileVfsRoot
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.nio.ByteBuffer
import java.nio.ByteOrder

class UdpbdServerTest {
    @Rule
    @JvmField
    val tmp = TemporaryFolder()

    @Test
    fun virtualExfatInfoAndSectorReadOverUdpLoopback() {
        val folder = tmp.newFolder("games")
        assertTrue(File(folder, "DVD").mkdirs())
        File(folder, "DVD/TEST.ISO").writeBytes(ByteArray(2048) { 0x5A })

        val server = UdpbdServerEngine(
            vfsRoot = FileVfsRoot(folder, isReadOnly = true),
            port = 0,
            bindIp = "127.0.0.1"
        )
        server.start()
        try {
            val sock = DatagramSocket().apply { soTimeout = 3000 }
            val loopback = InetAddress.getByName("127.0.0.1")

            val infoReq = ByteArray(2).apply {
                val h = UdpbdServerEngine.CMD_INFO or (1 shl 5)
                this[0] = (h and 0xFF).toByte()
                this[1] = ((h ushr 8) and 0xFF).toByte()
            }
            sock.send(DatagramPacket(infoReq, infoReq.size, loopback, server.actualPort))
            val infoReply = recv(sock)
            assertEquals(UdpbdServerEngine.CMD_INFO_REPLY, infoReply[0].toInt() and 0x1F)
            val secSize = ByteBuffer.wrap(infoReply, 2, 8).order(ByteOrder.LITTLE_ENDIAN).int
            val secCount = ByteBuffer.wrap(infoReply, 6, 4).order(ByteOrder.LITTLE_ENDIAN).int
            assertEquals(512, secSize)
            assertTrue(secCount > 2048)

            // Read sector 0 (MBR) -> check 0x55 0xAA trailer
            val readReq = ByteArray(8)
            val rh = UdpbdServerEngine.CMD_READ or (2 shl 5)
            ByteBuffer.wrap(readReq).order(ByteOrder.LITTLE_ENDIAN)
                .putShort(rh.toShort())
                .putInt(0)
                .putShort(1.toShort())
            sock.send(DatagramPacket(readReq, readReq.size, loopback, server.actualPort))
            val mbrData = ByteArray(512)
            var filled = 0
            while (filled < 512) {
                val rdma = recv(sock)
                assertEquals(UdpbdServerEngine.CMD_READ_RDMA, rdma[0].toInt() and 0x1F)
                val chunk = rdma.size - 6
                System.arraycopy(rdma, 6, mbrData, filled, chunk)
                filled += chunk
            }
            assertEquals(0x55, mbrData[510].toInt() and 0xFF)
            assertEquals(0xAA, mbrData[511].toInt() and 0xFF)
            sock.close()
        } finally {
            server.stop()
        }
    }

    @Test
    fun writableImageSupportsCmdWriteAndReadBack() {
        val imgFile = File(tmp.root, "disk.img")
        imgFile.writeBytes(ByteArray(512 * 8))

        val server = UdpbdServerEngine(
            vfsRoot = null,
            rawDeviceFactory = { FileRandomAccess(imgFile, writable = true) },
            port = 0,
            bindIp = "127.0.0.1",
            readOnly = false
        )
        server.start()
        try {
            val sock = DatagramSocket().apply { soTimeout = 3000 }
            val loopback = InetAddress.getByName("127.0.0.1")

            val cmdId = 3
            val writeReq = ByteArray(8)
            ByteBuffer.wrap(writeReq).order(ByteOrder.LITTLE_ENDIAN)
                .putShort((UdpbdServerEngine.CMD_WRITE or (cmdId shl 5)).toShort())
                .putInt(2)
                .putShort(1.toShort())
            sock.send(DatagramPacket(writeReq, writeReq.size, loopback, server.actualPort))

            val payload = ByteArray(512) { i -> ((i * 7 + 3) and 0xFF).toByte() }
            val rdmaPkt = ByteArray(6 + 512)
            ByteBuffer.wrap(rdmaPkt).order(ByteOrder.LITTLE_ENDIAN)
                .putShort((UdpbdServerEngine.CMD_WRITE_RDMA or (cmdId shl 5)).toShort())
                .putInt(7 or (1 shl 4)) // shift 7 = 512B blocks, 1 block
            System.arraycopy(payload, 0, rdmaPkt, 6, 512)
            sock.send(DatagramPacket(rdmaPkt, rdmaPkt.size, loopback, server.actualPort))

            val done = recv(sock)
            assertEquals(UdpbdServerEngine.CMD_WRITE_DONE, done[0].toInt() and 0x1F)
            assertEquals(0, ByteBuffer.wrap(done, 2, 4).order(ByteOrder.LITTLE_ENDIAN).int)

            assertArrayEquals(payload, imgFile.readBytes().copyOfRange(512 * 2, 512 * 3))
            sock.close()
        } finally {
            server.stop()
        }
    }

    private fun recv(sock: DatagramSocket): ByteArray {
        val buf = ByteArray(2048)
        val p = DatagramPacket(buf, buf.size)
        sock.receive(p)
        return p.data.copyOf(p.length)
    }
}
