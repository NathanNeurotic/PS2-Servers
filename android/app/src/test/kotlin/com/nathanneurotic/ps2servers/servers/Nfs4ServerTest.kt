package com.nathanneurotic.ps2servers.servers

import com.nathanneurotic.ps2servers.vfs.FileVfsRoot
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder

class Nfs4ServerTest {
    @Rule
    @JvmField
    val tmp = TemporaryFolder()

    @Test
    fun rpcNullCompoundLookupReaddirReadAndRoGuard() {
        val rootDir = tmp.newFolder("nfs_ro")
        val dvd = File(rootDir, "DVD")
        assertTrue(dvd.mkdirs())
        val payload = "NFS4-PS2-ISO-PAYLOAD".toByteArray(Charsets.US_ASCII)
        File(dvd, "TEST.ISO").writeBytes(payload)

        val server = Nfs4ServerEngine(
            vfs = FileVfsRoot(rootDir, isReadOnly = false),
            port = 0,
            bindIp = "127.0.0.1",
            allowWrites = false
        )
        server.start()
        try {
            Socket("127.0.0.1", server.actualPort).use { sock ->
                val out = sock.getOutputStream()
                val inp = sock.getInputStream()

                // 1. ONC RPC NULL (proc = 0)
                val nullResp = callRpc(out, inp, xid = 1, proc = 0, body = ByteArray(0))
                assertEquals(0, u32(nullResp, 20)) // ACCEPT_SUCCESS

                // 2. COMPOUND: PUTROOTFH + LOOKUP("DVD") + LOOKUP("TEST.ISO") + GETFH + GETATTR([1, 4, 53])
                val comp = compoundBody(
                    "tag1",
                    listOf(
                        p32(Nfs4ServerEngine.OP_PUTROOTFH),
                        p32(Nfs4ServerEngine.OP_LOOKUP) + opaque("DVD".toByteArray()),
                        p32(Nfs4ServerEngine.OP_LOOKUP) + opaque("TEST.ISO".toByteArray()),
                        p32(Nfs4ServerEngine.OP_GETFH),
                        p32(Nfs4ServerEngine.OP_GETATTR) + bitmap(1, 4, 53)
                    )
                )
                val resp = callRpc(out, inp, xid = 2, proc = 1, body = comp)
                assertEquals(Nfs4ServerEngine.NFS4_OK, u32(resp, 24))

                // Extract 16-byte FH after tag("tag1"), rescount(5), op PUTROOTFH(8B), op LOOKUP(8B), op LOOKUP(8B), op GETFH(op+stat+len+16B)
                val fhOffset = 24 + 4 + 8 + 4 + 8 + 8 + 8 + 12
                val fh = resp.copyOfRange(fhOffset, fhOffset + 16)

                // 3. PUTFH(fh) + READ(offset=0, count=64)
                val readComp = compoundBody(
                    "rd",
                    listOf(
                        p32(Nfs4ServerEngine.OP_PUTFH) + opaque(fh),
                        p32(Nfs4ServerEngine.OP_READ) + ByteArray(16) + p64(0L) + p32(64)
                    )
                )
                val readResp = callRpc(out, inp, xid = 3, proc = 1, body = readComp)
                assertEquals(Nfs4ServerEngine.NFS4_OK, u32(readResp, 24))
                assertTrue(String(readResp, Charsets.US_ASCII).contains("NFS4-PS2-ISO-PAYLOAD"))

                // 4. CREATE directory denied when allowWrites=false (NFS4ERR_ROFS = 30)
                val mkComp = compoundBody(
                    "mk",
                    listOf(
                        p32(Nfs4ServerEngine.OP_PUTROOTFH),
                        p32(Nfs4ServerEngine.OP_CREATE) + p32(2) + opaque("VMC".toByteArray()) + p32(0) + opaque(ByteArray(0))
                    )
                )
                val mkResp = callRpc(out, inp, xid = 4, proc = 1, body = mkComp)
                assertEquals(Nfs4ServerEngine.NFS4ERR_ROFS, u32(mkResp, 24))
            }
        } finally {
            server.stop()
        }
    }

    @Test
    fun writableOpenWriteAndRemove() {
        val rootDir = tmp.newFolder("nfs_rw")
        val server = Nfs4ServerEngine(
            vfs = FileVfsRoot(rootDir, isReadOnly = false),
            port = 0,
            bindIp = "127.0.0.1",
            allowWrites = true
        )
        server.start()
        try {
            Socket("127.0.0.1", server.actualPort).use { sock ->
                val out = sock.getOutputStream()
                val inp = sock.getInputStream()

                // OPEN(CREATE, "save.bin") + WRITE("ps2save")
                val openReq = p32(Nfs4ServerEngine.OP_OPEN) +
                    p32(1) + p32(3) + p32(0) + p64(1L) + opaque("cli".toByteArray()) +
                    p32(1) + p32(0) + p32(0) + opaque(ByteArray(0)) +
                    p32(0) + opaque("save.bin".toByteArray())
                val writeReq = p32(Nfs4ServerEngine.OP_WRITE) + ByteArray(16) + p64(0L) + p32(2) + opaque("ps2save".toByteArray())
                val comp = compoundBody("rw", listOf(p32(Nfs4ServerEngine.OP_PUTROOTFH), openReq, writeReq))
                val resp = callRpc(out, inp, xid = 10, proc = 1, body = comp)
                assertEquals(Nfs4ServerEngine.NFS4_OK, u32(resp, 24))
                assertArrayEquals("ps2save".toByteArray(), File(rootDir, "save.bin").readBytes())
            }
        } finally {
            server.stop()
        }
    }

    private fun callRpc(out: OutputStream, inp: InputStream, xid: Int, proc: Int, body: ByteArray): ByteArray {
        val hdr = p32(xid) + p32(0) + p32(2) + p32(Nfs4ServerEngine.NFS_PROGRAM) + p32(Nfs4ServerEngine.NFS_V4) + p32(proc) +
            p32(0) + opaque(ByteArray(0)) + p32(0) + opaque(ByteArray(0))
        val pkt = hdr + body
        val frame = p32(0x80000000.toInt() or pkt.size) + pkt
        out.write(frame); out.flush()

        val mark = u32(readExact(inp, 4), 0)
        return readExact(inp, mark and 0x7FFFFFFF)
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

    private fun compoundBody(tag: String, ops: List<ByteArray>): ByteArray {
        val baos = ByteArrayOutputStream()
        baos.write(opaque(tag.toByteArray()))
        baos.write(p32(0)) // minorversion 0
        baos.write(p32(ops.size))
        for (op in ops) baos.write(op)
        return baos.toByteArray()
    }

    private fun bitmap(vararg bits: Int): ByteArray {
        val wc = (bits.maxOrNull()!! / 32) + 1
        val words = IntArray(wc)
        for (b in bits) words[b / 32] = words[b / 32] or (1 shl (b % 32))
        val bb = ByteBuffer.allocate(4 + wc * 4).order(ByteOrder.BIG_ENDIAN).putInt(wc)
        for (w in words) bb.putInt(w)
        return bb.array()
    }

    private fun p32(v: Int): ByteArray = ByteBuffer.allocate(4).order(ByteOrder.BIG_ENDIAN).putInt(v).array()
    private fun p64(v: Long): ByteArray = ByteBuffer.allocate(8).order(ByteOrder.BIG_ENDIAN).putLong(v).array()
    private fun opaque(b: ByteArray): ByteArray {
        val pad = (4 - (b.size and 3)) and 3
        return p32(b.size) + b + ByteArray(pad)
    }

    private fun u32(b: ByteArray, off: Int): Int = ByteBuffer.wrap(b, off, 4).order(ByteOrder.BIG_ENDIAN).int
}
