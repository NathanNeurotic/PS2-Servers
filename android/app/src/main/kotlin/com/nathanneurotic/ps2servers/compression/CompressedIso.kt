package com.nathanneurotic.ps2servers.compression

import com.nathanneurotic.ps2servers.vfs.VfsRandomAccess
import com.nathanneurotic.ps2servers.vfs.VfsRoot
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.DataFormatException
import java.util.zip.Inflater

/**
 * Pure-Kotlin CSO/CISO and ZSO/ZISO block decompression matching
 * udpfs_server/compressed_iso/{base,cso,zso}.py.
 */
object CompressedIso {
    val SUPPORTED_EXTENSIONS = listOf(".zso", ".ziso", ".cso", ".ciso")

    fun isCompressedName(name: String): Boolean {
        val lower = name.lowercase()
        return SUPPORTED_EXTENSIONS.any { lower.endsWith(it) }
    }

    fun virtualIsoName(name: String): String {
        val lower = name.lowercase()
        for (ext in SUPPORTED_EXTENSIONS) {
            if (lower.endsWith(ext)) {
                return name.substring(0, name.length - ext.length) + ".iso"
            }
        }
        return name
    }

    fun probeInfo(access: VfsRandomAccess): Long? {
        return try {
            val header = ByteArray(24)
            val n = access.readAt(0L, header, 0, 24)
            if (n < 24) return null
            val bb = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)
            val magic = header.copyOfRange(0, 4)
            val isCso = magic.contentEquals("CISO".toByteArray(Charsets.US_ASCII))
            val isZso = magic.contentEquals("ZSO\u0000".toByteArray(Charsets.US_ASCII)) ||
                magic.contentEquals("ZISO".toByteArray(Charsets.US_ASCII))
            if (!isCso && !isZso) return null
            val uncompressedSize = bb.getLong(8)
            val blockSize = bb.getInt(16).toLong() and 0xFFFFFFFFL
            if (uncompressedSize <= 0L || blockSize <= 0L) return null
            uncompressedSize
        } catch (_: Exception) {
            null
        }
    }

    fun open(name: String, underlying: VfsRandomAccess, cacheSize: Int = 32): VfsRandomAccess {
        val lower = name.lowercase()
        return when {
            lower.endsWith(".cso") || lower.endsWith(".ciso") -> CsoRandomAccess(underlying, cacheSize)
            lower.endsWith(".zso") || lower.endsWith(".ziso") -> ZsoRandomAccess(underlying, cacheSize)
            else -> throw IOException("Unsupported compressed image extension: $name")
        }
    }

    fun resolveVirtualSibling(vfs: VfsRoot, relPath: String): Pair<String, VfsRandomAccess>? {
        if (!relPath.lowercase().endsWith(".iso")) return null
        val basePath = relPath.substring(0, relPath.length - 4)
        val dotIdx = basePath.lastIndexOf('.')
        val slashIdx = basePath.lastIndexOf('/')
        val parentDir = if (slashIdx >= 0) basePath.substring(0, slashIdx) else ""
        val baseName = if (slashIdx >= 0) basePath.substring(slashIdx + 1) else basePath
        val canonicalExt = if (dotIdx > slashIdx) basePath.substring(dotIdx).lowercase() else ""

        val candidates = ArrayList<String>()
        if (canonicalExt in SUPPORTED_EXTENSIONS) {
            vfs.stat(basePath)?.takeIf { !it.isDirectory }?.let { candidates.add(basePath) }
            val reqStem = baseName.substring(0, baseName.length - canonicalExt.length)
            for (entry in runCatching { vfs.list(parentDir) }.getOrDefault(emptyList())) {
                if (entry.isDirectory) continue
                val eDot = entry.name.lastIndexOf('.')
                if (eDot <= 0) continue
                if (entry.name.substring(0, eDot) == reqStem &&
                    entry.name.substring(eDot).lowercase() == canonicalExt
                ) {
                    if (entry.relativePath !in candidates) candidates.add(entry.relativePath)
                }
            }
        } else {
            val siblings = runCatching { vfs.list(parentDir) }.getOrDefault(emptyList())
            val byLowerExt = HashMap<String, String>()
            for (entry in siblings) {
                if (entry.isDirectory) continue
                val eDot = entry.name.lastIndexOf('.')
                if (eDot <= 0) continue
                if (entry.name.substring(0, eDot) == baseName) {
                    val lowerExt = entry.name.substring(eDot).lowercase()
                    if (!byLowerExt.containsKey(lowerExt)) {
                        byLowerExt[lowerExt] = entry.relativePath
                    }
                }
            }
            for (ext in SUPPORTED_EXTENSIONS) {
                val exact = basePath + ext
                val statEntry = vfs.stat(exact)
                if (statEntry != null && !statEntry.isDirectory) {
                    val actual = byLowerExt[ext] ?: exact
                    if (actual !in candidates) candidates.add(actual)
                } else {
                    val matched = byLowerExt[ext]
                    if (matched != null && matched !in candidates) candidates.add(matched)
                }
            }
        }

        for (candidate in candidates) {
            val raw = try {
                vfs.openRead(candidate)
            } catch (_: Exception) {
                continue
            }
            try {
                if (probeInfo(raw) != null) {
                    return candidate to open(candidate, raw)
                }
                raw.close()
            } catch (_: Exception) {
                raw.close()
            }
        }
        return null
    }
}

abstract class BlockCachedRandomAccess(
    protected val raw: VfsRandomAccess,
    cacheCapacity: Int = 32
) : VfsRandomAccess {
    protected var uncompressedSize: Long = 0L
    protected var blockSize: Int = 0
    protected var numBlocks: Int = 0
    private val capacity = cacheCapacity.coerceAtLeast(1)
    private val cache = object : LinkedHashMap<Int, ByteArray>(capacity, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<Int, ByteArray>?): Boolean {
            return size > capacity
        }
    }

    override fun size(): Long = uncompressedSize

    protected abstract fun decompressBlock(blockIndex: Int): ByteArray

    @Synchronized
    private fun getBlock(blockIndex: Int): ByteArray {
        val cached = cache[blockIndex]
        if (cached != null) return cached
        val data = decompressBlock(blockIndex)
        cache[blockIndex] = data
        return data
    }

    override fun readAt(offset: Long, buffer: ByteArray, bufferOffset: Int, length: Int): Int {
        if (offset < 0 || length < 0 || bufferOffset < 0 || bufferOffset + length > buffer.size) {
            throw IndexOutOfBoundsException("Invalid read bounds")
        }
        if (length == 0 || offset >= uncompressedSize) return 0
        val targetLen = minOf(length.toLong(), uncompressedSize - offset).toInt()
        var pos = offset
        var outPos = bufferOffset
        var remaining = targetLen
        while (remaining > 0) {
            val blockIdx = (pos / blockSize).toInt()
            val inBlockOffset = (pos % blockSize).toInt()
            val block = getBlock(blockIdx)
            val copyLen = minOf(remaining, block.size - inBlockOffset)
            if (copyLen <= 0) break
            System.arraycopy(block, inBlockOffset, buffer, outPos, copyLen)
            pos += copyLen
            outPos += copyLen
            remaining -= copyLen
        }
        return targetLen - remaining
    }

    override fun writeAt(offset: Long, data: ByteArray, dataOffset: Int, length: Int): Int {
        throw IOException("Compressed image is read-only")
    }

    override fun setLength(newSize: Long) {
        throw IOException("Compressed image is read-only")
    }

    override fun flush() {}

    override fun close() {
        raw.close()
    }
}

class CsoRandomAccess(
    raw: VfsRandomAccess,
    cacheCapacity: Int = 32
) : BlockCachedRandomAccess(raw, cacheCapacity) {
    private val align: Int
    private val blockOffsets: IntArray

    init {
        val header = ByteArray(24)
        if (raw.readAt(0L, header, 0, 24) < 24) {
            throw IOException("Invalid CSO header: too short")
        }
        val bb = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)
        if (bb.getInt(0) != 0x4F534943) {
            throw IOException("Invalid CSO magic")
        }
        uncompressedSize = bb.getLong(8)
        blockSize = bb.getInt(16)
        align = header[21].toInt() and 0xFF
        if (blockSize <= 0 || uncompressedSize <= 0L) {
            throw IOException("Invalid CSO geometry")
        }
        numBlocks = ((uncompressedSize + blockSize - 1) / blockSize).toInt()
        val tableBytes = ByteArray((numBlocks + 1) * 4)
        if (raw.readAt(24L, tableBytes, 0, tableBytes.size) < tableBytes.size) {
            throw IOException("Truncated CSO offset table")
        }
        val tb = ByteBuffer.wrap(tableBytes).order(ByteOrder.LITTLE_ENDIAN)
        blockOffsets = IntArray(numBlocks + 1) { tb.getInt() }
    }

    override fun decompressBlock(blockIndex: Int): ByteArray {
        val rawOff = blockOffsets[blockIndex].toLong() and 0xFFFFFFFFL
        val nextRawOff = blockOffsets[blockIndex + 1].toLong() and 0xFFFFFFFFL
        val isUncompressed = (rawOff and 0x80000000L) != 0L
        val offset = (rawOff and 0x7FFFFFFFL) shl align
        val readSize = if (isUncompressed) {
            blockSize
        } else {
            val nextOffset = (nextRawOff and 0x7FFFFFFFL) shl align
            (nextOffset - offset).toInt()
        }
        if (readSize < 0) throw IOException("Corrupt CSO offset at block $blockIndex")
        val compressed = ByteArray(readSize)
        if (raw.readAt(offset, compressed, 0, readSize) < readSize) {
            throw IOException("Truncated CSO block $blockIndex")
        }
        if (isUncompressed) return compressed

        val out = tryInflate(compressed, nowrap = true) ?: tryInflate(compressed, nowrap = false)
            ?: throw IOException("CSO zlib decompression failed for block $blockIndex")
        return if (out.size >= blockSize) {
            if (out.size == blockSize) out else out.copyOf(blockSize)
        } else {
            out.copyOf(blockSize)
        }
    }

    private fun tryInflate(input: ByteArray, nowrap: Boolean): ByteArray? {
        val inflater = Inflater(nowrap)
        return try {
            inflater.setInput(input)
            val out = ByteArray(blockSize)
            var total = 0
            while (!inflater.finished() && total < blockSize) {
                val n = inflater.inflate(out, total, blockSize - total)
                if (n == 0) {
                    if (inflater.needsInput() || inflater.needsDictionary()) break
                }
                total += n
            }
            out.copyOf(total)
        } catch (_: DataFormatException) {
            null
        } finally {
            inflater.end()
        }
    }
}

class ZsoRandomAccess(
    raw: VfsRandomAccess,
    cacheCapacity: Int = 32
) : BlockCachedRandomAccess(raw, cacheCapacity) {
    private val align: Int
    private val isZiso: Boolean
    private val blockOffsets: IntArray
    private val rawFileSize: Long = raw.size()

    init {
        val header = ByteArray(24)
        if (raw.readAt(0L, header, 0, 24) < 24) {
            throw IOException("Invalid ZSO header: too short")
        }
        val magic = header.copyOfRange(0, 4)
        isZiso = magic.contentEquals("ZISO".toByteArray(Charsets.US_ASCII))
        if (!isZiso && !magic.contentEquals("ZSO\u0000".toByteArray(Charsets.US_ASCII))) {
            throw IOException("Invalid ZSO magic")
        }
        val bb = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)
        val headerSize = bb.getInt(4).toLong() and 0xFFFFFFFFL
        uncompressedSize = bb.getLong(8)
        blockSize = bb.getInt(16)
        align = if (isZiso) header[21].toInt() and 0xFF else 0
        if (blockSize <= 0 || uncompressedSize <= 0L) {
            throw IOException("Invalid ZSO geometry")
        }
        numBlocks = ((uncompressedSize + blockSize - 1) / blockSize).toInt()
        val entryCount = if (isZiso) numBlocks + 1 else numBlocks
        val tableBytes = ByteArray(entryCount * 4)
        if (raw.readAt(headerSize, tableBytes, 0, tableBytes.size) < tableBytes.size) {
            throw IOException("Truncated ZSO offset table")
        }
        val tb = ByteBuffer.wrap(tableBytes).order(ByteOrder.LITTLE_ENDIAN)
        blockOffsets = IntArray(entryCount) { tb.getInt() }
    }

    override fun decompressBlock(blockIndex: Int): ByteArray {
        val rawOff = blockOffsets[blockIndex].toLong() and 0xFFFFFFFFL
        val isUncompressed = (rawOff and 0x80000000L) != 0L
        val offset: Long
        val compressedSize: Int
        if (isZiso) {
            val nextRawOff = blockOffsets[blockIndex + 1].toLong() and 0xFFFFFFFFL
            offset = (rawOff and 0x7FFFFFFFL) shl align
            val nextOffset = (nextRawOff and 0x7FFFFFFFL) shl align
            compressedSize = (nextOffset - offset).toInt()
        } else {
            offset = rawOff and 0x7FFFFFFFL
            val nextOffset = if (blockIndex + 1 < numBlocks) {
                blockOffsets[blockIndex + 1].toLong() and 0x7FFFFFFFL
            } else {
                rawFileSize
            }
            compressedSize = (nextOffset - offset).toInt()
        }
        if (compressedSize < 0) throw IOException("Corrupt ZSO offset at block $blockIndex")
        val compressed = ByteArray(compressedSize)
        if (raw.readAt(offset, compressed, 0, compressedSize) < compressedSize) {
            throw IOException("Truncated ZSO block $blockIndex")
        }
        if (isUncompressed) {
            return if (compressed.size == blockSize) compressed else compressed.copyOf(blockSize)
        }
        // OPL-style trailing alignment padding retry loop matching zso.py.
        var len = compressed.size
        while (len > 0) {
            val out = tryDecompressLz4Block(compressed, len, blockSize)
            if (out != null) return out
            len--
        }
        throw IOException("LZ4 decompression failed for ZSO block $blockIndex")
    }

    companion object {
        fun tryDecompressLz4Block(src: ByteArray, srcLen: Int, expectedSize: Int): ByteArray? {
            return try {
                val dst = ByteArray(expectedSize)
                var ip = 0
                var op = 0
                while (ip < srcLen) {
                    val token = src[ip++].toInt() and 0xFF
                    var litLen = token ushr 4
                    if (litLen == 15) {
                        var b: Int
                        do {
                            if (ip >= srcLen) return null
                            b = src[ip++].toInt() and 0xFF
                            litLen += b
                        } while (b == 255)
                    }
                    if (ip + litLen > srcLen || op + litLen > expectedSize) return null
                    System.arraycopy(src, ip, dst, op, litLen)
                    ip += litLen
                    op += litLen
                    if (ip >= srcLen) break

                    if (ip + 2 > srcLen) return null
                    val offset = (src[ip].toInt() and 0xFF) or ((src[ip + 1].toInt() and 0xFF) shl 8)
                    ip += 2
                    if (offset == 0 || offset > op) return null

                    var matchLen = (token and 0x0F) + 4
                    if ((token and 0x0F) == 15) {
                        var b: Int
                        do {
                            if (ip >= srcLen) return null
                            b = src[ip++].toInt() and 0xFF
                            matchLen += b
                        } while (b == 255)
                    }
                    if (op + matchLen > expectedSize) return null
                    var matchPos = op - offset
                    for (i in 0 until matchLen) {
                        dst[op++] = dst[matchPos++]
                    }
                }
                if (op == expectedSize) dst else null
            } catch (_: Exception) {
                null
            }
        }
    }
}
