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
import java.net.Socket

class HttpServerTest {
    @Rule
    @JvmField
    val tmp = TemporaryFolder()

    @Test
    fun legacyFlatCatalogRange206And416Under512ByteHeaderBudget() {
        val rootDir = tmp.newFolder("opl")
        val dvd = File(rootDir, "DVD")
        assertTrue(dvd.mkdirs())
        val isoBytes = ByteArray(4096) { i -> ((i * 19 + 1) and 0xFF).toByte() }
        File(dvd, "SLUS_201.74.Rumble Racing.iso").writeBytes(isoBytes)

        val server = HttpServerEngine(
            vfs = FileVfsRoot(rootDir, isReadOnly = true),
            port = 0,
            bindIp = "127.0.0.1",
            catalogProfile = HttpServerEngine.PROFILE_LEGACY_FLAT
        )
        server.start()
        try {
            Socket("127.0.0.1", server.actualPort).use { sock ->
                val out = sock.getOutputStream()
                val inp = sock.getInputStream()

                // 1. /PS2SMB/games.csv returns unchunked 200 OK
                out.write("GET /PS2SMB/games.csv HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".toByteArray())
                out.flush()
                val (csvHdr, csvBody) = readHttpResponse(inp)
                assertTrue(csvHdr.startsWith("HTTP/1.1 200 OK"))
                assertTrue(csvBody.toString(Charsets.UTF_8).contains("SLUS_201.74,SLUS_201.74.Rumble Racing.iso,DVD"))

                // 2. Same keep-alive socket: Range bytes=100-355 -> 206 Partial Content, header < 512 bytes
                val req2 = "GET /SLUS_201.74.Rumble%20Racing.iso HTTP/1.1\r\nHost: 127.0.0.1\r\nRange: bytes=100-355\r\n\r\n"
                out.write(req2.toByteArray())
                out.flush()
                val (rangeHdr, rangeBody) = readHttpResponse(inp)
                assertTrue(rangeHdr.startsWith("HTTP/1.1 206 Partial Content"))
                assertTrue("Header length ${rangeHdr.length} must fit 512B IOP buffer", rangeHdr.toByteArray().size < 512)
                assertEquals(256, rangeBody.size)
                assertArrayEquals(isoBytes.copyOfRange(100, 356), rangeBody)

                // 3. Out-of-bounds range -> 416
                val req3 = "GET /SLUS_201.74.Rumble%20Racing.iso HTTP/1.1\r\nHost: 127.0.0.1\r\nRange: bytes=4096-5000\r\nConnection: close\r\n\r\n"
                out.write(req3.toByteArray())
                out.flush()
                val (errHdr, _) = readHttpResponse(inp)
                assertTrue(errHdr.startsWith("HTTP/1.1 416"))
            }
        } finally {
            server.stop()
        }
    }

    @Test
    fun riptoplPathAwareQuotedCsvAndRecursiveLookup() {
        val rootDir = tmp.newFolder("riptopl")
        val sub = File(rootDir, "DVD/Action")
        assertTrue(sub.mkdirs())
        val isoBytes = ByteArray(2048) { 0x33 }
        File(sub, "SLUS_200.01.Demo, Vol 1.iso").writeBytes(isoBytes)

        val server = HttpServerEngine(
            vfs = FileVfsRoot(rootDir, isReadOnly = true),
            port = 0,
            bindIp = "127.0.0.1",
            catalogProfile = HttpServerEngine.PROFILE_RIPTOPL_PATH_AWARE,
            recursive = true
        )
        server.start()
        try {
            Socket("127.0.0.1", server.actualPort).use { sock ->
                val out = sock.getOutputStream()
                val inp = sock.getInputStream()

                out.write("GET /games.csv HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".toByteArray())
                out.flush()
                val (csvHdr, csvBody) = readHttpResponse(inp)
                assertTrue(csvHdr.startsWith("HTTP/1.1 200 OK"))
                val text = csvBody.toString(Charsets.UTF_8).trim()
                assertEquals("\"SLUS_200.01\",\"Demo, Vol 1\",DVD,\"DVD/Action/SLUS_200.01.Demo, Vol 1.iso\"", text)

                val req2 = "GET /PS2SMB/DVD/Action/SLUS_200.01.Demo,%20Vol%201.iso HTTP/1.1\r\nHost: 127.0.0.1\r\nRange: bytes=0-63\r\nConnection: close\r\n\r\n"
                out.write(req2.toByteArray())
                out.flush()
                val (rHdr, rBody) = readHttpResponse(inp)
                assertTrue(rHdr.startsWith("HTTP/1.1 206 Partial Content"))
                assertArrayEquals(isoBytes.copyOfRange(0, 64), rBody)
            }
        } finally {
            server.stop()
        }
    }

    private fun readHttpResponse(inp: InputStream): Pair<String, ByteArray> {
        val hdrBuf = ByteArrayOutputStream()
        var state = 0
        while (true) {
            val b = inp.read()
            if (b < 0) break
            hdrBuf.write(b)
            state = when {
                b == '\r'.code && (state == 0 || state == 2) -> state + 1
                b == '\n'.code && (state == 1 || state == 3) -> state + 1
                b == '\r'.code -> 1
                else -> 0
            }
            if (state == 4) break
        }
        val hdrText = hdrBuf.toString("ISO-8859-1")
        val contentLength = hdrText.lines()
            .firstOrNull { it.lowercase().startsWith("content-length:") }
            ?.substringAfter(':')?.trim()?.toIntOrNull() ?: 0
        val body = ByteArray(contentLength)
        var read = 0
        while (read < contentLength) {
            val n = inp.read(body, read, contentLength - read)
            if (n <= 0) break
            read += n
        }
        return hdrText to (if (read == contentLength) body else body.copyOf(read))
    }
}
