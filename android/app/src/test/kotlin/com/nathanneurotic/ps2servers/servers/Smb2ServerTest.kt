package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.vfs.FileVfsRoot
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
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

class Smb2ServerTest {
    @Rule
    @JvmField
    val tmp = TemporaryFolder()

    @Test
    fun md4MatchesRfc1320Vector() {
        val digest = Smb2Crypto.md4(ByteArray(0))
        val hex = digest.joinToString("") { "%02x".format(it) }
        assertEquals("31d6cfe0d16ae931b73c59d7e0c089c0", hex)
    }

    @Test
    fun smb2HmacSha256SignedAuthSrvsvcCompoundAndCrud() {
        runSmb2OrSmb3Flow(dialect = Smb2ServerEngine.DIALECT_210, ceiling = Smb2ServerEngine.DIALECT_210, keyName = "smbv2")
    }

    @Test
    fun smb3AesCmacSignedAuthSrvsvcCompoundAndCrud() {
        runSmb2OrSmb3Flow(dialect = Smb2ServerEngine.DIALECT_302, ceiling = Smb2ServerEngine.DIALECT_302, keyName = "smbv3")
    }

    private fun runSmb2OrSmb3Flow(dialect: Int, ceiling: Int, keyName: String) {
        val shareDir = tmp.newFolder("games_$keyName")
        val dvd = File(shareDir, "DVD")
        assertTrue(dvd.mkdirs())
        val isoData = ByteArray(1024) { i -> ((i * 11 + 3) and 0xFF).toByte() }
        File(dvd, "TEST.ISO").writeBytes(isoData)

        val server = Smb2ServerEngine(
            vfs = FileVfsRoot(shareDir, isReadOnly = false),
            key = keyName,
            ceiling = ceiling,
            shareName = "games",
            port = 0,
            bindIp = "127.0.0.1",
            username = "ps2",
            password = "secret",
            openShare = false
        )
        server.start()
        try {
            Socket("127.0.0.1", server.actualPort).use { sock ->
                val out = sock.getOutputStream()
                val inp = sock.getInputStream()
                var msgId = 0L

                // 1. NEGOTIATE
                val negBody = ByteBuffer.allocate(36 + 2).order(ByteOrder.LITTLE_ENDIAN)
                    .putShort(36).putShort(1).putShort(1).putShort(0).putInt(0x04)
                    .put(ByteArray(16) { 0x42 }).putLong(0L).putShort(dialect.toShort()).array()
                val negResp = sendSmb2(out, inp, Smb2ServerEngine.CMD_NEGOTIATE, msgId++, 0, 0L, negBody, null, dialect)
                assertEquals(0, headerStatus(negResp))
                assertEquals(dialect, ByteBuffer.wrap(negResp, 64 + 4, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF)

                // 2. SESSION_SETUP with bare NTLMSSP Type 1 -> Type 2 -> Type 3 (proof + exported session key)
                val ntlmType1 = "NTLMSSP\u0000".toByteArray(Charsets.US_ASCII) +
                    ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
                        .putInt(1).putInt(0x60088205.toInt()).putInt(0).putInt(0).putInt(0).putInt(0).array()
                val ss1Body = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
                    .putShort(25).put(0).put(0x01).putInt(0).putInt(0).putShort(88).putShort(ntlmType1.size.toShort()).putLong(0L).array() + ntlmType1
                val ss1Resp = sendSmb2(out, inp, Smb2ServerEngine.CMD_SESSION_SETUP, msgId++, 0, 0L, ss1Body, null, dialect)
                assertEquals(Smb2ServerEngine.STATUS_MORE_PROCESSING_REQUIRED, headerStatus(ss1Resp))
                val sid = ByteBuffer.wrap(ss1Resp, 40, 8).order(ByteOrder.LITTLE_ENDIAN).long
                val cBufOff = ByteBuffer.wrap(ss1Resp, 64 + 4, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
                val cBufLen = ByteBuffer.wrap(ss1Resp, 64 + 6, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
                val chalMsg = ss1Resp.copyOfRange(cBufOff, cBufOff + cBufLen)
                val serverChal = chalMsg.copyOfRange(24, 32)
                val tiLen = ByteBuffer.wrap(chalMsg, 40, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
                val tiOff = ByteBuffer.wrap(chalMsg, 44, 4).order(ByteOrder.LITTLE_ENDIAN).int
                val targetInfo = chalMsg.copyOfRange(tiOff, tiOff + tiLen)

                // Build NTLMv2 response
                val clientChal = ByteArray(8) { 0x77 }
                val blob = byteArrayOf(1, 1, 0, 0, 0, 0, 0, 0) + ByteArray(8) + clientChal + ByteArray(4) + targetInfo + ByteArray(4)
                val ntowf = Smb2Crypto.ntowfv2("secret", "ps2", "")
                val proof = Smb2Crypto.hmacMd5(ntowf, serverChal + blob)
                val ntResp = proof + blob
                val baseKey = Smb2Crypto.hmacMd5(ntowf, proof)
                val sessionKey = baseKey
                val userUtf16 = "ps2".toByteArray(Charsets.UTF_16LE)
                val payStart = 64
                val ntOff = payStart + userUtf16.size
                val ntlmType3 = ByteBuffer.allocate(64 + userUtf16.size + ntResp.size).order(ByteOrder.LITTLE_ENDIAN).apply {
                    put("NTLMSSP\u0000".toByteArray(Charsets.US_ASCII))
                    putInt(3)
                    putShort(0); putShort(0); putInt(payStart) // lm
                    putShort(ntResp.size.toShort()); putShort(ntResp.size.toShort()); putInt(ntOff) // nt
                    putShort(0); putShort(0); putInt(payStart) // domain
                    putShort(userUtf16.size.toShort()); putShort(userUtf16.size.toShort()); putInt(payStart) // user
                    putShort(0); putShort(0); putInt(payStart) // workstation
                    putShort(0); putShort(0); putInt(payStart) // enc key
                    putInt(0x00080201)
                    put(userUtf16)
                    put(ntResp)
                }.array()

                val ss2Body = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
                    .putShort(25).put(0).put(0x03).putInt(0).putInt(0).putShort(88).putShort(ntlmType3.size.toShort()).putLong(0L).array() + ntlmType3
                val ss2Resp = sendSmb2(out, inp, Smb2ServerEngine.CMD_SESSION_SETUP, msgId++, 0, sid, ss2Body, null, dialect)
                assertEquals(0, headerStatus(ss2Resp))
                val signKey = Smb2Crypto.signingKey(sessionKey, dialect)
                assertTrue(Smb2Crypto.verifySignature(signKey, dialect, ss2Resp))

                // 3. TREE_CONNECT to IPC$ and srvsvc NetrShareEnum
                val ipcPath = "\\\\127.0.0.1\\IPC$".toByteArray(Charsets.UTF_16LE)
                val tcIpc = ByteBuffer.allocate(8 + ipcPath.size).order(ByteOrder.LITTLE_ENDIAN)
                    .putShort(9).putShort(0).putShort(72).putShort(ipcPath.size.toShort()).put(ipcPath).array()
                val ipcResp = sendSmb2(out, inp, Smb2ServerEngine.CMD_TREE_CONNECT, msgId++, 0, sid, tcIpc, signKey, dialect)
                assertEquals(0, headerStatus(ipcResp))
                val ipcTid = ByteBuffer.wrap(ipcResp, 36, 4).order(ByteOrder.LITTLE_ENDIAN).int

                val pipeName = "srvsvc".toByteArray(Charsets.UTF_16LE)
                val openPipe = ByteBuffer.allocate(56 + pipeName.size).order(ByteOrder.LITTLE_ENDIAN).apply {
                    putShort(0, 57); putInt(24, 0x001F01FF); putInt(36, 1)
                    putShort(44, (64 + 56).toShort()); putShort(46, pipeName.size.toShort())
                    position(56); put(pipeName)
                }.array()
                val pipeResp = sendSmb2(out, inp, Smb2ServerEngine.CMD_CREATE, msgId++, ipcTid, sid, openPipe, signKey, dialect)
                assertEquals(0, headerStatus(pipeResp))
                val pipeFid = pipeResp.copyOfRange(64 + 64, 64 + 80)

                val bindPdu = ByteBuffer.allocate(16 + 12 + 24 + 20).order(ByteOrder.LITTLE_ENDIAN).apply {
                    put(0, 5); put(1, 0); put(2, 11); put(3, 3)
                    putInt(4, 0x10); putShort(8, 72); putShort(10, 0); putInt(12, 1)
                    putShort(16, 4280); putShort(18, 4280); putInt(20, 0); put(24, 1)
                    putShort(28, 0); put(30, 1.toByte()); position(32)
                    put(byteArrayOf(0xC8.toByte(), 0x4F, 0x32, 0x4B, 0x70, 0x16, 0xD3.toByte(), 0x01, 0x12, 0x78, 0x5A, 0x47, 0xBF.toByte(), 0x6E, 0xE1.toByte(), 0x88.toByte(), 3, 0, 0, 0))
                    put(byteArrayOf(0x04, 0x5D, 0x88.toByte(), 0x8A.toByte(), 0xEB.toByte(), 0x1C, 0xC9.toByte(), 0x11, 0x9F.toByte(), 0xE8.toByte(), 0x08, 0x00, 0x2B, 0x10, 0x48, 0x60, 2, 0, 0, 0))
                }.array()
                val ioctlBind = ByteBuffer.allocate(56 + bindPdu.size).order(ByteOrder.LITTLE_ENDIAN).apply {
                    putShort(0, 57); putInt(4, 0x0011C017)
                    position(8); put(pipeFid)
                    putInt(24, 64 + 56); putInt(28, bindPdu.size); putInt(44, 4096)
                    position(56); put(bindPdu)
                }.array()
                val bindResp = sendSmb2(out, inp, Smb2ServerEngine.CMD_IOCTL, msgId++, ipcTid, sid, ioctlBind, signKey, dialect)
                assertEquals(0, headerStatus(bindResp))
                assertTrue(String(bindResp, Charsets.US_ASCII).contains("srvsvc"))

                // 4. TREE_CONNECT to games and signed compound CREATE("DVD") + QUERY_INFO + CLOSE
                val gPath = "\\\\127.0.0.1\\games".toByteArray(Charsets.UTF_16LE)
                val tcG = ByteBuffer.allocate(8 + gPath.size).order(ByteOrder.LITTLE_ENDIAN)
                    .putShort(9).putShort(0).putShort(72).putShort(gPath.size.toShort()).put(gPath).array()
                val gResp = sendSmb2(out, inp, Smb2ServerEngine.CMD_TREE_CONNECT, msgId++, 0, sid, tcG, signKey, dialect)
                assertEquals(0, headerStatus(gResp))
                val tid = ByteBuffer.wrap(gResp, 36, 4).order(ByteOrder.LITTLE_ENDIAN).int

                val dvdUtf16 = "DVD".toByteArray(Charsets.UTF_16LE)
                val createDvd = ByteBuffer.allocate(56 + dvdUtf16.size).order(ByteOrder.LITTLE_ENDIAN).apply {
                    putShort(0, 57); putInt(24, 0x00120089); putInt(36, 1)
                    putShort(44, (64 + 56).toShort()); putShort(46, dvdUtf16.size.toShort())
                    position(56); put(dvdUtf16)
                }.array()
                val qd = ByteBuffer.allocate(32 + 2).order(ByteOrder.LITTLE_ENDIAN).apply {
                    putShort(0, 33); put(2, 0x26); put(3, 1)
                    position(8); put(ByteArray(16) { 0xFF.toByte() })
                    putShort(24, (64 + 32).toShort()); putShort(26, 2); putInt(28, 65536)
                    position(32); put("*".toByteArray(Charsets.UTF_16LE))
                }.array()
                val cl = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN).apply {
                    putShort(0, 24); position(8); put(ByteArray(16) { 0xFF.toByte() })
                }.array()

                val compResp = sendSmb2Compound(
                    out, inp,
                    listOf(
                        Triple(Smb2ServerEngine.CMD_CREATE, createDvd, false),
                        Triple(Smb2ServerEngine.CMD_QUERY_DIRECTORY, qd, true),
                        Triple(Smb2ServerEngine.CMD_CLOSE, cl, true)
                    ),
                    msgId, tid, sid, signKey, dialect
                )
                msgId += 3
                assertTrue(String(compResp, Charsets.UTF_16LE).contains("TEST.ISO"))
            }
        } finally {
            server.stop()
        }
    }

    private fun packSmb2Hdr(cmd: Int, mid: Long, tid: Int, sid: Long, flags: Int, nextCmd: Int): ByteArray {
        val h = ByteArray(64)
        h[0] = 0xFE.toByte(); h[1] = 'S'.code.toByte(); h[2] = 'M'.code.toByte(); h[3] = 'B'.code.toByte()
        ByteBuffer.wrap(h, 4, 60).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(64).putShort(1).putInt(0).putShort(cmd.toShort()).putShort(16)
            .putInt(flags).putInt(nextCmd).putLong(mid).putInt(0).putInt(tid).putLong(sid)
        return h
    }

    private fun sendSmb2(
        out: OutputStream,
        inp: InputStream,
        cmd: Int,
        mid: Long,
        tid: Int,
        sid: Long,
        body: ByteArray,
        signKey: ByteArray?,
        dialect: Int
    ): ByteArray {
        var msg = packSmb2Hdr(cmd, mid, tid, sid, if (signKey != null) 0x08 else 0, 0) + body
        if (signKey != null) msg = Smb2Crypto.applySignature(signKey, dialect, msg)
        val frame = ByteArray(4 + msg.size)
        frame[1] = ((msg.size ushr 16) and 0xFF).toByte()
        frame[2] = ((msg.size ushr 8) and 0xFF).toByte()
        frame[3] = (msg.size and 0xFF).toByte()
        System.arraycopy(msg, 0, frame, 4, msg.size)
        out.write(frame); out.flush()
        return readNetbios(inp)
    }

    private fun sendSmb2Compound(
        out: OutputStream,
        inp: InputStream,
        items: List<Triple<Int, ByteArray, Boolean>>,
        baseMid: Long,
        tid: Int,
        sid: Long,
        signKey: ByteArray,
        dialect: Int
    ): ByteArray {
        var full = ByteArray(0)
        for (i in items.indices) {
            val (cmd, body, related) = items[i]
            val last = i == items.size - 1
            val total = 64 + body.size
            val aligned = if (last) total else (total + 7) and 7.inv()
            val pad = aligned - total
            val flags = 0x08 or (if (related) 0x04 else 0)
            var piece = packSmb2Hdr(cmd, baseMid + i, tid, sid, flags, if (last) 0 else aligned) + body + ByteArray(pad)
            piece = Smb2Crypto.applySignature(signKey, dialect, piece)
            full += piece
        }
        val frame = ByteArray(4 + full.size)
        frame[1] = ((full.size ushr 16) and 0xFF).toByte()
        frame[2] = ((full.size ushr 8) and 0xFF).toByte()
        frame[3] = (full.size and 0xFF).toByte()
        System.arraycopy(full, 0, frame, 4, full.size)
        out.write(frame); out.flush()
        return readNetbios(inp)
    }

    private fun readNetbios(inp: InputStream): ByteArray {
        val hdr = readExact(inp, 4)
        val len = ((hdr[1].toInt() and 0xFF) shl 16) or ((hdr[2].toInt() and 0xFF) shl 8) or (hdr[3].toInt() and 0xFF)
        return readExact(inp, len)
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

    private fun headerStatus(msg: ByteArray): Int = ByteBuffer.wrap(msg, 8, 4).order(ByteOrder.LITTLE_ENDIAN).int
}
