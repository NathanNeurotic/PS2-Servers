package com.nathanneurotic.ps2servers.vfs

import com.nathanneurotic.ps2servers.compression.CompressedIso
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.Deflater

class VfsAndCompressionTest {
    @Rule
    @JvmField
    val tmp = TemporaryFolder()

    @Test
    fun pathGuardBlocksTraversalAndReservedWindowsNames() {
        assertEquals("", PathGuard.normalizeRelative("/"))
        assertEquals("DVD/GAME.ISO", PathGuard.normalizeRelative("\\DVD//GAME.ISO"))
        try {
            PathGuard.normalizeRelative("../secret.txt")
            fail("Expected traversal rejection")
        } catch (_: VfsPathException) {
        }
        try {
            PathGuard.normalizeRelative("DVD/CON.txt", strictWindowsNames = true)
            fail("Expected Windows reserved device name rejection")
        } catch (_: VfsPathException) {
        }
    }

    @Test
    fun fileVfsSupportsCrudAndCursor() {
        val root = FileVfsRoot(tmp.root, isReadOnly = false)
        assertTrue(root.mkdir("DVD"))
        root.openWrite("DVD/SLUS_200.01.ISO", create = true, truncate = true).use { ra ->
            val payload = "PS2-DISC-HEADER".toByteArray(Charsets.US_ASCII)
            ra.writeAt(0L, payload, 0, payload.size)
            assertEquals(payload.size.toLong(), ra.size())
        }
        assertEquals(listOf("SLUS_200.01.ISO"), root.list("DVD").map { it.name })
        assertTrue(root.rename("DVD/SLUS_200.01.ISO", "DVD/RENAMED.ISO", replaceIfExists = true))
        assertNull(root.stat("DVD/SLUS_200.01.ISO"))
        assertNotNull(root.stat("DVD/RENAMED.ISO"))

        root.openRead("DVD/RENAMED.ISO").use { ra ->
            val cursor = VfsCursorHandle(ra, writable = false)
            cursor.seek(4L, 0)
            assertEquals("DISC", cursor.read(4).toString(Charsets.US_ASCII))
        }
        assertTrue(root.deleteFile("DVD/RENAMED.ISO"))
        assertTrue(root.deleteDirectory("DVD"))
        assertFalse(File(tmp.root, "DVD").exists())
    }

    @Test
    fun csoAndZsoDecompressExactBytesAcrossBlockBoundaries() {
        val blockSize = 2048
        val raw = ByteArray(blockSize * 3) { i -> ((i * 31 + 7) and 0xFF).toByte() }
        // Make block 1 highly compressible so it uses compressed storage.
        for (i in blockSize until blockSize * 2) {
            raw[i] = (0x41 + (i % 4)).toByte()
        }

        // Build CSO v1
        val csoBlocks = ArrayList<Pair<Boolean, ByteArray>>()
        val deflater = Deflater(Deflater.DEFAULT_COMPRESSION, true)
        for (b in 0 until 3) {
            val slice = raw.copyOfRange(b * blockSize, (b + 1) * blockSize)
            if (b == 1) {
                deflater.reset()
                deflater.setInput(slice)
                deflater.finish()
                val buf = ByteArray(blockSize * 2)
                val n = deflater.deflate(buf)
                csoBlocks.add(false to buf.copyOf(n))
            } else {
                csoBlocks.add(true to slice)
            }
        }
        deflater.end()

        val csoHeaderSize = 24
        val indexSize = 4 * 4
        var offset = csoHeaderSize + indexSize
        val index = IntArray(4)
        for (b in 0 until 3) {
            val (rawFlag, payload) = csoBlocks[b]
            index[b] = offset or (if (rawFlag) 0x80000000.toInt() else 0)
            offset += payload.size
        }
        index[3] = offset

        val csoFile = File(tmp.root, "TEST.CSO")
        csoFile.outputStream().use { out ->
            val hdr = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
            hdr.put("CISO".toByteArray(Charsets.US_ASCII))
            hdr.putInt(24)
            hdr.putLong(raw.size.toLong())
            hdr.putInt(blockSize)
            hdr.put(1.toByte())
            hdr.put(0.toByte())
            hdr.putShort(0)
            out.write(hdr.array())
            val idxBuf = ByteBuffer.allocate(index.size * 4).order(ByteOrder.LITTLE_ENDIAN)
            for (v in index) idxBuf.putInt(v)
            out.write(idxBuf.array())
            for ((_, payload) in csoBlocks) out.write(payload)
        }

        // Also build ZSO using raw block + LZ4 token literal-only block for block 1
        val zsoFile = File(tmp.root, "TEST2.ZSO")
        val block1 = raw.copyOfRange(blockSize, blockSize * 2)
        // Encode literal length 2048 = 15 + 255*7 + 248
        val lz4Block = ByteArray(1 + 8 + blockSize)
        lz4Block[0] = 0xF0.toByte()
        for (k in 1..7) lz4Block[k] = 255.toByte()
        lz4Block[8] = (2048 - 15 - 7 * 255).toByte()
        System.arraycopy(block1, 0, lz4Block, 9, blockSize)

        var zOff = 24 + 4 * 4
        val zIdx = IntArray(4)
        val zPayloads = listOf(
            true to raw.copyOfRange(0, blockSize),
            false to lz4Block,
            true to raw.copyOfRange(blockSize * 2, blockSize * 3)
        )
        for (b in 0 until 3) {
            val (rawFlag, payload) = zPayloads[b]
            zIdx[b] = zOff or (if (rawFlag) 0x80000000.toInt() else 0)
            zOff += payload.size
        }
        zIdx[3] = zOff
        zsoFile.outputStream().use { out ->
            val hdr = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
            hdr.put("ZISO".toByteArray(Charsets.US_ASCII))
            hdr.putInt(24)
            hdr.putLong(raw.size.toLong())
            hdr.putInt(blockSize)
            hdr.put(1.toByte())
            hdr.put(0.toByte())
            hdr.putShort(0)
            out.write(hdr.array())
            val idxBuf = ByteBuffer.allocate(zIdx.size * 4).order(ByteOrder.LITTLE_ENDIAN)
            for (v in zIdx) idxBuf.putInt(v)
            out.write(idxBuf.array())
            for ((_, p) in zPayloads) out.write(p)
        }

        val vfs = FileVfsRoot(tmp.root)
        CompressedIso.open("TEST.CSO", vfs.openRead("TEST.CSO")).use { ra ->
            assertEquals(raw.size.toLong(), ra.size())
            val read = ByteArray(3000)
            var got = 0
            while (got < read.size) {
                val n = ra.readAt(1500L + got, read, got, read.size - got)
                if (n <= 0) break
                got += n
            }
            assertEquals(3000, got)
            assertArrayEquals(raw.copyOfRange(1500, 4500), read)
        }
        CompressedIso.open("TEST2.ZSO", vfs.openRead("TEST2.ZSO")).use { ra ->
            val read = ByteArray(2500)
            var got = 0
            while (got < read.size) {
                val n = ra.readAt(1800L + got, read, got, read.size - got)
                if (n <= 0) break
                got += n
            }
            assertEquals(2500, got)
            assertArrayEquals(raw.copyOfRange(1800, 4300), read)
        }
        val sibling = CompressedIso.resolveVirtualSibling(vfs, "TEST.ISO")
        assertNotNull(sibling)
        sibling!!.second.use {
            assertEquals("TEST.CSO", sibling.first)
        }
    }

    @Test
    fun virtualExfatGeneratesValidMbrBootAndReadsHostedFilePayload() {
        val folder = tmp.newFolder("games")
        val dvd = File(folder, "DVD")
        assertTrue(dvd.mkdirs())
        val payload = ByteArray(4096) { i -> ((i * 13 + 5) and 0xFF).toByte() }
        File(dvd, "DEMO.ISO").writeBytes(payload)

        val vfs = FileVfsRoot(folder, isReadOnly = true)
        VirtualExfatDevice(vfs).use { disk ->
            assertTrue(disk.sectorCount() > 2048L)

            // MBR at sector 0
            disk.seek(0L, 1L)
            val mbr = disk.readSequential(512)
            assertEquals(0x55, mbr[510].toInt() and 0xFF)
            assertEquals(0xAA, mbr[511].toInt() and 0xFF)
            assertEquals(0x07, mbr[446 + 4].toInt() and 0xFF)

            // VBR at partition start (sector 2048)
            disk.seek(VirtualExfatDevice.PARTITION_OFFSET_SECTORS, 1L)
            val vbr = disk.readSequential(512)
            assertEquals("EXFAT   ", String(vbr, 3, 8, Charsets.US_ASCII))
            val bb = ByteBuffer.wrap(vbr).order(ByteOrder.LITTLE_ENDIAN)
            val heapOffset = bb.getInt(88).toLong()
            val rootCluster = bb.getInt(96).toLong()

            // Root directory holds 0x81 bitmap, 0x82 upcase, 0x83 PS2SERVER label, and DVD dir (0x85)
            val rootByteOffset = (VirtualExfatDevice.PARTITION_OFFSET_SECTORS + heapOffset) * 512L +
                (rootCluster - 2L) * VirtualExfatDevice.CLUSTER_SIZE
            val rootDir = ByteArray(512)
            disk.readAt(rootByteOffset, rootDir, 0, 512)
            assertEquals(0x81, rootDir[0].toInt() and 0xFF)
            assertEquals(0x82, rootDir[32].toInt() and 0xFF)
            assertEquals(0x83, rootDir[64].toInt() and 0xFF)
            assertEquals(0x85, rootDir[96].toInt() and 0xFF)

            val dvdCluster = ByteBuffer.wrap(rootDir, 96 + 32 + 20, 4).order(ByteOrder.LITTLE_ENDIAN).int.toLong()
            val dvdByteOffset = (VirtualExfatDevice.PARTITION_OFFSET_SECTORS + heapOffset) * 512L +
                (dvdCluster - 2L) * VirtualExfatDevice.CLUSTER_SIZE
            val dvdDir = ByteArray(256)
            disk.readAt(dvdByteOffset, dvdDir, 0, 256)
            assertEquals(0x85, dvdDir[0].toInt() and 0xFF)
            val fileCluster = ByteBuffer.wrap(dvdDir, 32 + 20, 4).order(ByteOrder.LITTLE_ENDIAN).int.toLong()
            val fileLen = ByteBuffer.wrap(dvdDir, 32 + 24, 8).order(ByteOrder.LITTLE_ENDIAN).long
            assertEquals(payload.size.toLong(), fileLen)

            val fileByteOffset = (VirtualExfatDevice.PARTITION_OFFSET_SECTORS + heapOffset) * 512L +
                (fileCluster - 2L) * VirtualExfatDevice.CLUSTER_SIZE
            val readBack = ByteArray(payload.size)
            val got = disk.readAt(fileByteOffset, readBack, 0, readBack.size)
            assertEquals(payload.size, got)
            assertArrayEquals(payload, readBack)
        }
    }
}
