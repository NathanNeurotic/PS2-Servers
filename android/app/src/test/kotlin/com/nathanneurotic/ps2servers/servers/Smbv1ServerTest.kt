package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.vfs.FileVfsRoot
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.Deflater

class Smbv1ServerTest {
    @Rule
    @JvmField
    val tmp = TemporaryFolder()

    @Test
    fun negotiateAuthFindCrudAndVirtualCsoRead() {
        val shareDir = tmp.newFolder("games")
        val dvd = File(shareDir, "DVD")
        assertTrue(dvd.mkdirs())

        // Create a compressible CSO so FIND_FIRST2 advertises GAME.iso and READ_ANDX decompresses it.
        val blockSize = 2048
        val isoBytes = ByteArray(blockSize) { i -> ((i * 7 + 11) and 0xFF).toByte() }
        val deflater = Deflater(Deflater.DEFAULT_COMPRESSION, true)
        deflater.setInput(isoBytes)
        deflater.finish()
        val buf = ByteArray(blockSize * 2)
        val cLen = deflater.deflate(buf)
        deflater.end()
        val compBlock = buf.copyOf(cLen)
        val off0 = 24 + 8
        File(dvd, "GAME.cso").outputStream().use { fos ->
            fos.write(
                ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
                    .put("CISO".toByteArray(Charsets.US_ASCII))
                    .putInt(24).putLong(isoBytes.size.toLong()).putInt(blockSize).put(1).put(0).putShort(0).array()
            )
            fos.write(ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putInt(off0).putInt(off0 + compBlock.size).array())
            fos.write(compBlock)
        }

        val server = Smbv1ServerEngine(
            vfs = FileVfsRoot(shareDir, isReadOnly = false),
            shareName = "games",
            port = 0,
            bindIp = "127.0.0.1",
            enableCompression = true
        )
        server.start()
        try {
            Socket("127.0.0.1", server.actualPort).use { sock ->
                val out = sock.getOutputStream()
                val inp = sock.getInputStream()

                // 1. NEGOTIATE
                val neg = sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_NEGOTIATE, 0, 0, ByteArray(0), "\u0002NT LM 0.12\u0000".toByteArray())
                assertEquals(0, statusOf(neg))
                assertEquals(17, neg[32].toInt() and 0xFF)

                // 2. SESSION_SETUP_ANDX (guest)
                val ss = sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_SESSION_SETUP_ANDX, 0, 0, ByteArray(26), "\u0000".toByteArray())
                assertEquals(0, statusOf(ss))
                val uid = u16(ss, 28)

                // 3. TREE_CONNECT_ANDX
                val tcParams = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
                    .put(0xFF.toByte()).put(0).putShort(0).putShort(0).putShort(1).array()
                val tc = sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_TREE_CONNECT_ANDX, 0, uid, tcParams, "\u0000\\\\127.0.0.1\\games\u0000?????\u0000".toByteArray())
                assertEquals(0, statusOf(tc))
                val tid = u16(tc, 24)

                // 4. TRANS2 FIND_FIRST2 on "\DVD\*" -> expects GAME.iso
                val tp = ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN)
                    .putShort(0x16).putShort(16).putShort(0).putShort(0x0104).putInt(0).array() + "\\DVD\\*\u0000".toByteArray()
                val trans2Params = ByteBuffer.allocate(30).order(ByteOrder.LITTLE_ENDIAN)
                    .putShort(tp.size.toShort()) // TotalParamCount (0)
                    .putShort(0)                 // TotalDataCount (2)
                    .putShort(10)                // MaxParamCount (4)
                    .putShort(2048)              // MaxDataCount (6)
                    .put(0).put(0)               // MaxSetupCount + Reserved (8)
                    .putShort(0)                 // Flags (10)
                    .putInt(0)                   // Timeout (12)
                    .putShort(0)                 // Reserved2 (16)
                    .putShort(tp.size.toShort()) // ParamCount (18)
                    .putShort(68)                // ParamOffset (20)
                    .putShort(0)                 // DataCount (22)
                    .putShort(0)                 // DataOffset (24)
                    .put(1).put(0)               // SetupCount=1 + Reserved3 (26)
                    .putShort(Smbv1ServerEngine.TRANS2_FIND_FIRST2.toShort()) // Setup[0] (28)
                    .array()
                val bodyOff = 32 + 1 + trans2Params.size + 2
                val pad = 68 - bodyOff
                ByteBuffer.wrap(trans2Params, 20, 2).order(ByteOrder.LITTLE_ENDIAN).putShort((bodyOff + pad).toShort())
                val ff = sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_TRANS2, tid, uid, trans2Params, ByteArray(pad) + tp)
                assertEquals(0, statusOf(ff))
                assertTrue(String(ff, Charsets.US_ASCII).contains("GAME.iso"))

                // 5. OPEN_ANDX on virtual "DVD\\GAME.iso" and READ_ANDX
                val op = sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_OPEN_ANDX, tid, uid, ByteArray(30), "DVD\\GAME.iso\u0000".toByteArray())
                assertEquals(0, statusOf(op))
                val gameFid = u16(op, 33 + 4)
                val rdParams = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
                    .put(0xFF.toByte()).put(0).putShort(0).putShort(gameFid.toShort()).putInt(0)
                    .putShort(512).putShort(512).putInt(0).putShort(0).putInt(0).array()
                val rd = sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_READ_ANDX, tid, uid, rdParams, ByteArray(0))
                assertEquals(0, statusOf(rd))
                assertArrayEquals(isoBytes.copyOfRange(0, 512), rd.copyOfRange(59, 59 + 512))
                sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_CLOSE, tid, uid, ByteBuffer.allocate(6).order(ByteOrder.LITTLE_ENDIAN).putShort(gameFid.toShort()).putInt(0).array(), ByteArray(0))

                // 6. NT_CREATE_ANDX -> WRITE_ANDX -> CLOSE -> RENAME -> DELETE
                val nameBytes = "TEST.CFG\u0000".toByteArray(Charsets.US_ASCII)
                val ntParams = ByteBuffer.allocate(48).order(ByteOrder.LITTLE_ENDIAN).apply {
                    putShort(5, (nameBytes.size - 1).toShort())
                    putInt(35, Smbv1ServerEngine.FILE_OVERWRITE_IF)
                }.array()
                val nc = sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_NT_CREATE_ANDX, tid, uid, ntParams, nameBytes)
                assertEquals(0, statusOf(nc))
                val cfgFid = u16(nc, 33 + 5)

                val wrPayload = "opl-config".toByteArray()
                val wrParams = ByteBuffer.allocate(28).order(ByteOrder.LITTLE_ENDIAN).apply {
                    put(0, 0xFF.toByte())
                    putShort(4, cfgFid.toShort())
                    putInt(6, 0)
                    putShort(20, wrPayload.size.toShort())
                    putShort(22, (32 + 1 + 28 + 2).toShort())
                }.array()
                val wr = sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_WRITE_ANDX, tid, uid, wrParams, wrPayload)
                assertEquals(0, statusOf(wr))
                sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_CLOSE, tid, uid, ByteBuffer.allocate(6).order(ByteOrder.LITTLE_ENDIAN).putShort(cfgFid.toShort()).putInt(0).array(), ByteArray(0))
                assertEquals("opl-config", File(shareDir, "TEST.CFG").readText())

                val renData = "\u0004TEST.CFG\u0000\u0004REN.CFG\u0000".toByteArray()
                assertEquals(0, statusOf(sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_RENAME, tid, uid, ByteArray(2), renData)))
                assertTrue(File(shareDir, "REN.CFG").isFile)

                val delData = "\u0004REN.CFG\u0000".toByteArray()
                assertEquals(0, statusOf(sendCmd(out, inp, Smbv1ServerEngine.SMB_COM_DELETE, tid, uid, ByteArray(2), delData)))
                assertFalse(File(shareDir, "REN.CFG").exists())
            }
        } finally {
            server.stop()
        }
    }

    private fun sendCmd(
        out: OutputStream,
        inp: InputStream,
        cmd: Int,
        tid: Int,
        uid: Int,
        params: ByteArray,
        data: ByteArray
    ): ByteArray {
        val len = 32 + 1 + params.size + 2 + data.size
        val pkt = ByteArray(4 + len)
        pkt[1] = ((len ushr 16) and 0xFF).toByte()
        pkt[2] = ((len ushr 8) and 0xFF).toByte()
        pkt[3] = (len and 0xFF).toByte()
        pkt[4] = 0xFF.toByte(); pkt[5] = 'S'.code.toByte(); pkt[6] = 'M'.code.toByte(); pkt[7] = 'B'.code.toByte()
        pkt[8] = cmd.toByte()
        ByteBuffer.wrap(pkt, 28, 8).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(tid.toShort()).putShort(1).putShort(uid.toShort()).putShort(1)
        pkt[36] = (params.size / 2).toByte()
        System.arraycopy(params, 0, pkt, 37, params.size)
        val bcOff = 37 + params.size
        ByteBuffer.wrap(pkt, bcOff, 2).order(ByteOrder.LITTLE_ENDIAN).putShort(data.size.toShort())
        System.arraycopy(data, 0, pkt, bcOff + 2, data.size)
        out.write(pkt)
        out.flush()

        val nb = readExact(inp, 4)
        val respLen = ((nb[1].toInt() and 0xFF) shl 16) or ((nb[2].toInt() and 0xFF) shl 8) or (nb[3].toInt() and 0xFF)
        return readExact(inp, respLen)
    }

    private fun readExact(inp: InputStream, n: Int): ByteArray {
        val buf = ByteArray(n)
        var off = 0
        while (off < n) {
            val k = inp.read(buf, off, n - off)
            if (k < 0) break
            off += k
        }
        return buf
    }

    private fun u16(b: ByteArray, off: Int): Int =
        (b[off].toInt() and 0xFF) or ((b[off + 1].toInt() and 0xFF) shl 8)

    private fun statusOf(msg: ByteArray): Int =
        (msg[5].toInt() and 0xFF) or ((msg[7].toInt() and 0xFF) shl 16) or ((msg[8].toInt() and 0xFF) shl 24)
}
