package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.config.MinimalJson
import com.nathanneurotic.ps2servers.vfs.FileVfsRoot
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
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
import java.util.zip.Deflater

class UdpfsServerTest {
    @Rule
    @JvmField
    val tmp = TemporaryFolder()

    @Test
    fun classifyProfileMatchesSharedHandshakeFixtures() {
        val fixturesFile = listOf(
            File("../../conformance/fixtures/handshake_cases.json"),
            File("../conformance/fixtures/handshake_cases.json"),
            File("conformance/fixtures/handshake_cases.json")
        ).firstOrNull { it.isFile }
        assertNotNull("Expected handshake_cases.json fixture file", fixturesFile)

        val cases = MinimalJson.parseAny(fixturesFile!!.readText(Charsets.UTF_8)) as List<*>
        assertTrue(cases.isNotEmpty())
        for (raw in cases) {
            val obj = raw as Map<*, *>
            val name = obj["name"].toString()
            val disc = (obj["discovery"] as Number).toInt()
            val firstData = (obj["first_data"] as Number).toInt()
            val expected = obj["profile"].toString()
            assertEquals("Fixture '$name' mismatch", expected, UdpfsServerEngine.classifyProfile(disc, firstData))
        }
    }

    @Test
    fun loopbackHandshakeListGetstatAndReadCompressedCsoAsIso() {
        val rootDir = tmp.newFolder("share")
        val dvd = File(rootDir, "DVD")
        assertTrue(dvd.mkdirs())

        // Create a compressible 2-block CSO so DREAD advertises TEST.iso and READ returns uncompressed bytes.
        val blockSize = 2048
        val isoBytes = ByteArray(blockSize * 2) { i -> ((i % 251) and 0xFF).toByte() }
        val deflater = Deflater(Deflater.DEFAULT_COMPRESSION, true)
        val blocks = (0 until 2).map { b ->
            val slice = isoBytes.copyOfRange(b * blockSize, (b + 1) * blockSize)
            deflater.reset()
            deflater.setInput(slice)
            deflater.finish()
            val out = ByteArray(blockSize * 2)
            val n = deflater.deflate(out)
            out.copyOf(n)
        }
        deflater.end()

        var off = 24 + 3 * 4
        val idx = IntArray(3)
        for (b in 0 until 2) {
            idx[b] = off
            off += blocks[b].size
        }
        idx[2] = off
        File(dvd, "GAME.cso").outputStream().use { fos ->
            val hdr = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
                .put("CISO".toByteArray(Charsets.US_ASCII))
                .putInt(24)
                .putLong(isoBytes.size.toLong())
                .putInt(blockSize)
                .put(1.toByte())
                .put(0.toByte())
                .putShort(0)
            fos.write(hdr.array())
            val tb = ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN)
            for (v in idx) tb.putInt(v)
            fos.write(tb.array())
            for (b in blocks) fos.write(b)
        }

        val server = UdpfsServerEngine(
            vfs = FileVfsRoot(rootDir, isReadOnly = false),
            port = 0,
            dataPort = 0,
            bindIp = "127.0.0.1",
            protocolModeSetting = "Standard only",
            enableCompression = true
        )
        server.start()
        try {
            val client = DatagramSocket().apply { soTimeout = 3000 }
            val loopback = InetAddress.getByName("127.0.0.1")

            // 1. Router status query should answer without opening a session.
            val query = byteArrayOf(0x00, 0x00, 0xF7.toByte(), 0xF5.toByte(), 0, 0) +
                "PS2S".toByteArray(Charsets.US_ASCII)
            client.send(DatagramPacket(query, query.size, loopback, server.actualDiscoveryPort))
            val statusReply = recv(client)
            assertEquals(UdpfsServerEngine.PKT_INFORM, statusReply[0].toInt() and 0x0F)
            assertEquals(UdpfsServerEngine.SVC_STATUS, u16(statusReply, 2))

            // 2. UDPFS DISCOVERY -> INFORM
            val disc = ByteBuffer.allocate(6).order(ByteOrder.LITTLE_ENDIAN)
                .putShort(UdpfsServerEngine.PKT_DISCOVERY.toShort())
                .putShort(UdpfsServerEngine.UDPRDMA_SVC_UDPFS.toShort())
                .putShort(0)
                .array()
            client.send(DatagramPacket(disc, disc.size, loopback, server.actualDiscoveryPort))
            val inform = recv(client)
            assertEquals(UdpfsServerEngine.PKT_INFORM, inform[0].toInt() and 0x0F)
            val dataPort = u16(inform, 4)
            assertEquals(server.actualDataPort, dataPort)

            var txSeq = 0
            fun sendReq(payload: ByteArray): ByteArray {
                val padded = (payload.size + 3) and 3.inv()
                val pkt = ByteArray(6 + padded)
                val hdrVal = UdpfsServerEngine.PKT_DATA or ((txSeq and 0xFFF) shl 4)
                val dHdrVal = ((txSeq - 1) and 0xFFF) or (UdpfsServerEngine.FLAG_FIN shl 12) or ((padded and 0x3FFF) shl 18)
                ByteBuffer.wrap(pkt, 0, 6).order(ByteOrder.LITTLE_ENDIAN)
                    .putShort(hdrVal.toShort())
                    .putInt(dHdrVal)
                System.arraycopy(payload, 0, pkt, 6, payload.size)
                client.send(DatagramPacket(pkt, pkt.size, loopback, dataPort))
                txSeq++

                while (true) {
                    val reply = recv(client)
                    val dh = ByteBuffer.wrap(reply, 2, 4).order(ByteOrder.LITTLE_ENDIAN).int
                    val hWords = (dh ushr 14) and 0xF
                    val dBytes = (dh ushr 18) and 0x3FFF
                    val pLen = hWords * 4 + dBytes
                    if (pLen == 0) continue // request ACK
                    val rSeq = (u16(reply, 0) ushr 4) and 0xFFF
                    val ack = ByteArray(6)
                    val aHdr = UdpfsServerEngine.PKT_DATA or ((txSeq and 0xFFF) shl 4)
                    val aDh = (rSeq and 0xFFF) or (UdpfsServerEngine.FLAG_ACK shl 12)
                    ByteBuffer.wrap(ack).order(ByteOrder.LITTLE_ENDIAN).putShort(aHdr.toShort()).putInt(aDh)
                    client.send(DatagramPacket(ack, ack.size, loopback, dataPort))
                    return reply.copyOfRange(6, reply.size)
                }
            }

            // 3. GETSTAT "DVD/GAME.iso" returns decompressed size 4096.
            val gsReq = byteArrayOf(UdpfsServerEngine.MSG_GETSTAT_REQ.toByte(), 0, 0, 0) +
                "DVD/GAME.iso\u0000".toByteArray(Charsets.UTF_8)
            val gsReply = sendReq(gsReq)
            assertEquals(UdpfsServerEngine.MSG_GETSTAT_REPLY, gsReply[0].toInt() and 0xFF)
            assertEquals(0, ByteBuffer.wrap(gsReply).order(ByteOrder.LITTLE_ENDIAN).getInt(4))
            assertEquals(isoBytes.size, ByteBuffer.wrap(gsReply).order(ByteOrder.LITTLE_ENDIAN).getInt(16))

            // 4. OPEN "DVD/GAME.iso" -> LSEEK -> READ first 512 bytes
            val openReq = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
                .put(UdpfsServerEngine.MSG_OPEN_REQ.toByte())
                .put(0.toByte())
                .putShort(0x01.toShort())
                .putInt(0)
                .array() + "DVD/GAME.iso\u0000".toByteArray(Charsets.UTF_8)
            val openReply = sendReq(openReq)
            val handle = ByteBuffer.wrap(openReply).order(ByteOrder.LITTLE_ENDIAN).getInt(4)
            assertTrue(handle > 0)

            val readReq = ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN)
                .put(UdpfsServerEngine.MSG_READ_REQ.toByte())
                .put(0).put(0).put(0)
                .putInt(handle)
                .putInt(512)
                .array()
            val readReply = sendReq(readReq)
            assertEquals(UdpfsServerEngine.MSG_RESULT_REPLY, readReply[0].toInt() and 0xFF)
            assertEquals(512, ByteBuffer.wrap(readReply).order(ByteOrder.LITTLE_ENDIAN).getInt(4))
            assertArrayEquals(isoBytes.copyOfRange(0, 512), readReply.copyOfRange(8, 520))

            client.close()
        } finally {
            server.stop()
        }
    }

    private fun recv(sock: DatagramSocket): ByteArray {
        val buf = ByteArray(4096)
        val p = DatagramPacket(buf, buf.size)
        sock.receive(p)
        return p.data.copyOf(p.length)
    }

    private fun u16(b: ByteArray, off: Int): Int =
        (b[off].toInt() and 0xFF) or ((b[off + 1].toInt() and 0xFF) shl 8)
}
