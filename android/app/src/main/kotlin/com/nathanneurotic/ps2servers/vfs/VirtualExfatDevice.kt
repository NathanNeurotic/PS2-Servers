package com.nathanneurotic.ps2servers.vfs

import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale

/**
 * Read-only, lazy virtual exFAT disk image backed by any [VfsRoot] (filesystem or SAF).
 *
 * Metadata (MBR, exFAT VBR + backup boot region, FAT, allocation bitmap, compressed
 * upcase table, and directory entry clusters) is generated in memory while file payloads
 * are streamed on demand via LRU-cached [VfsRandomAccess] handles.
 */
class VirtualExfatDevice(
    private val vfs: VfsRoot
) : VfsRandomAccess {

    companion object {
        const val SECTOR_SIZE = 512
        const val CLUSTER_SIZE = 32768
        const val SECTORS_PER_CLUSTER = CLUSTER_SIZE / SECTOR_SIZE
        const val PARTITION_OFFSET_SECTORS = 2048L
        private const val VOLUME_SERIAL = 0x50533253 // "PS2S"
        private const val MAX_DEPTH = 64
        private const val MAX_NODES = 100_000
        private const val MAX_OPEN_HANDLES = 16
        private val INVALID_EXFAT_CHARS = setOf('"', '*', '/', ':', '<', '>', '?', '\\', '|')

        fun checksum(data: ByteArray, bits: Int = 32, skipIndices: Set<Int> = emptySet()): Long {
            val mask = if (bits == 32) 0xFFFFFFFFL else (1L shl bits) - 1L
            var value = 0L
            for (i in data.indices) {
                if (i !in skipIndices) {
                    val rotated = ((value ushr 1) or ((value and 1L) shl (bits - 1))) and mask
                    value = (rotated + (data[i].toInt() and 0xFF)) and mask
                }
            }
            return value
        }

        fun nameUnits(name: String): IntArray {
            if (name.isEmpty()) {
                throw IOException("Empty name cannot be represented in exFAT")
            }
            val bytes = name.toByteArray(Charsets.UTF_16LE)
            val units = IntArray(bytes.size / 2)
            for (i in units.indices) {
                val u = (bytes[i * 2].toInt() and 0xFF) or ((bytes[i * 2 + 1].toInt() and 0xFF) shl 8)
                if (u < 32 || INVALID_EXFAT_CHARS.contains(u.toChar())) {
                    throw IOException("Name cannot be represented in exFAT: $name")
                }
                units[i] = u
            }
            if (units.isEmpty() || units.size > 255) {
                throw IOException("exFAT name length out of range: $name")
            }
            return units
        }

        private fun buildUpcaseMap(): IntArray {
            val table = IntArray(65536)
            for (c in 0 until 65536) {
                val upper = c.toChar().toString().uppercase(Locale.ROOT)
                table[c] = if (upper.length == 1 && upper[0].code <= 0xFFFF) upper[0].code else c
            }
            return table
        }
    }

    private class Node(
        val entry: VfsEntry,
        val units: IntArray,
        val children: MutableList<Node> = ArrayList()
    ) {
        var size: Long = if (entry.isDirectory) 0L else entry.size
        var cluster: Long = 0L
        var clusters: Long = 0L
        var payload: ByteArray? = null
    }

    private data class Chain(val start: Long, val length: Long)
    private data class Extent(val start: Long, val length: Long, val bytesPayload: ByteArray?, val node: Node?)
    private data class CachedHandle(val node: Node, val handle: VfsRandomAccess)

    private val upcaseMap: IntArray = buildUpcaseMap()
    private val nodes = ArrayList<Node>()
    private val root: Node
    private val clusterCount: Long
    private val bitmapLength: Int
    private val bitmapCluster: Long = 2L
    private val upcaseCluster: Long
    private val allocatedClusters: Long
    private val fatLengthSectors: Long
    private val heapSector: Long
    private val volumeSizeBytes: Long
    private val chains: List<Chain>
    private val extents: List<Extent>
    private val mbr: ByteArray
    private val bootRegion: ByteArray

    private val handles = object : LinkedHashMap<String, CachedHandle>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, CachedHandle>?): Boolean {
            if (size > MAX_OPEN_HANDLES && eldest != null) {
                try {
                    eldest.value.handle.close()
                } catch (_: Exception) {
                }
                return true
            }
            return false
        }
    }

    private val lock = Any()
    private var closed = false
    private var position = 0L
    private var requestEnd: Long? = null

    init {
        val rootEntry = vfs.stat("") ?: throw IOException("Virtual exFAT folder does not exist: ${vfs.displayPath}")
        if (!rootEntry.isDirectory) {
            throw IOException("Virtual exFAT requires a folder: ${vfs.displayPath}")
        }
        root = scanDir(rootEntry, 0)

        val encoded = ArrayList<Int>(4096)
        var c = 0
        while (c < 65536) {
            var end = c
            while (end < 65536 && upcaseMap[end] == end && end - c < 65535) {
                end++
            }
            if (end - c > 1 || c == 65535) {
                encoded.add(0xFFFF)
                encoded.add(end - c)
                c = end
            } else {
                encoded.add(upcaseMap[c])
                c++
            }
        }
        val upcaseBytes = ByteArray(encoded.size * 2)
        for (i in encoded.indices) {
            val v = encoded[i]
            upcaseBytes[i * 2] = (v and 0xFF).toByte()
            upcaseBytes[i * 2 + 1] = ((v ushr 8) and 0xFF).toByte()
        }
        val upcaseClusters = ceilDiv(upcaseBytes.size.toLong(), CLUSTER_SIZE.toLong())

        for (node in nodes) {
            if (node.entry.isDirectory) {
                var entrySlots = if (node === root) 3L else 0L
                for (child in node.children) {
                    entrySlots += 2L + ceilDiv(child.units.size.toLong(), 15L)
                }
                node.size = (entrySlots + 1L) * 32L
            }
            node.clusters = ceilDiv(node.size, CLUSTER_SIZE.toLong())
        }

        val payloadClusters = nodes.sumOf { it.clusters }
        var bmpClusters = 1L
        var totalClusters: Long
        while (true) {
            totalClusters = maxOf(64L, payloadClusters + bmpClusters + upcaseClusters + 16L)
            val needed = ceilDiv(ceilDiv(totalClusters, 8L), CLUSTER_SIZE.toLong())
            if (needed == bmpClusters) break
            bmpClusters = needed
        }

        clusterCount = totalClusters
        bitmapLength = ceilDiv(clusterCount, 8L).toInt()
        upcaseCluster = 2L + bmpClusters
        var nextCluster = upcaseCluster + upcaseClusters
        for (node in nodes) {
            if (node.clusters > 0L) {
                node.cluster = nextCluster
                nextCluster += node.clusters
            } else {
                node.cluster = 0L
            }
        }
        allocatedClusters = nextCluster - 2L
        fatLengthSectors = ceilDiv((clusterCount + 2L) * 4L, SECTOR_SIZE.toLong())
        heapSector = ceilDiv(24L + fatLengthSectors, 64L) * 64L
        val volumeSectors = heapSector + clusterCount * SECTORS_PER_CLUSTER.toLong()
        volumeSizeBytes = (PARTITION_OFFSET_SECTORS + volumeSectors) * SECTOR_SIZE.toLong()
        if ((volumeSizeBytes / SECTOR_SIZE) > 0xFFFFFFFFL) {
            throw IOException("Virtual disk exceeds UDPBD 32-bit sector capacity")
        }

        val chainList = ArrayList<Chain>()
        chainList.add(Chain(bitmapCluster, bmpClusters))
        chainList.add(Chain(upcaseCluster, upcaseClusters))
        for (node in nodes) {
            if (node.entry.isDirectory && node.clusters > 0L) {
                chainList.add(Chain(node.cluster, node.clusters))
            }
        }
        chainList.sortBy { it.start }
        chains = chainList

        val extentList = ArrayList<Extent>()
        if (upcaseBytes.isNotEmpty()) {
            extentList.add(Extent(heapOffset(upcaseCluster), upcaseBytes.size.toLong(), upcaseBytes, null))
        }
        for (node in nodes) {
            if (node.entry.isDirectory) {
                node.payload = directoryBytes(node, upcaseBytes)
            }
            if (node.size > 0L && node.cluster > 0L) {
                extentList.add(Extent(heapOffset(node.cluster), node.size, node.payload, node))
            }
        }
        extentList.sortBy { it.start }
        extents = extentList

        bootRegion = buildBootRegion(volumeSectors)
        mbr = ByteArray(SECTOR_SIZE).apply {
            val bb = ByteBuffer.wrap(this, 446, 16).order(ByteOrder.LITTLE_ENDIAN)
            bb.put(0.toByte())
            bb.put(byteArrayOf(0xFE.toByte(), 0xFF.toByte(), 0xFF.toByte()))
            bb.put(0x07.toByte())
            bb.put(byteArrayOf(0xFE.toByte(), 0xFF.toByte(), 0xFF.toByte()))
            bb.putInt(PARTITION_OFFSET_SECTORS.toInt())
            bb.putInt(volumeSectors.toInt())
            this[510] = 0x55.toByte()
            this[511] = 0xAA.toByte()
        }
    }

    private fun scanDir(entry: VfsEntry, depth: Int): Node {
        if (depth > MAX_DEPTH) {
            throw IOException("Virtual exFAT folder nesting exceeds $MAX_DEPTH levels")
        }
        val units = if (depth == 0) IntArray(0) else nameUnits(entry.name)
        val node = Node(entry, units)
        nodes.add(node)
        if (nodes.size > MAX_NODES) {
            throw IOException("Virtual exFAT supports at most $MAX_NODES entries")
        }
        if (entry.isDirectory) {
            val seen = HashSet<String>()
            val children = vfs.list(entry.relativePath)
            for (child in children) {
                val childUnits = nameUnits(child.name)
                val upperKey = buildString(childUnits.size) {
                    for (u in childUnits) {
                        append(upcaseMap[u].toChar())
                    }
                }
                if (!seen.add(upperKey)) {
                    throw IOException("Duplicate exFAT name in ${entry.relativePath}: ${child.name}")
                }
                if (child.isDirectory) {
                    node.children.add(scanDir(child, depth + 1))
                } else {
                    val fileNode = Node(child, childUnits)
                    nodes.add(fileNode)
                    if (nodes.size > MAX_NODES) {
                        throw IOException("Virtual exFAT supports at most $MAX_NODES entries")
                    }
                    node.children.add(fileNode)
                }
            }
        }
        return node
    }

    private fun heapOffset(cluster: Long): Long {
        return (PARTITION_OFFSET_SECTORS + heapSector) * SECTOR_SIZE + (cluster - 2L) * CLUSTER_SIZE
    }

    private fun directoryBytes(node: Node, upcaseBytes: ByteArray): ByteArray {
        val out = ArrayList<ByteArray>()
        if (node === root) {
            val bitmap = ByteArray(32)
            bitmap[0] = 0x81.toByte()
            ByteBuffer.wrap(bitmap, 20, 12).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(bitmapCluster.toInt())
                .putLong(bitmapLength.toLong())
            out.add(bitmap)

            val caseEntry = ByteArray(32)
            caseEntry[0] = 0x82.toByte()
            ByteBuffer.wrap(caseEntry, 4, 4).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(checksum(upcaseBytes).toInt())
            ByteBuffer.wrap(caseEntry, 20, 12).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(upcaseCluster.toInt())
                .putLong(upcaseBytes.size.toLong())
            out.add(caseEntry)

            val label = ByteArray(32)
            label[0] = 0x83.toByte()
            label[1] = 9.toByte()
            val labelBytes = "PS2SERVER".toByteArray(Charsets.UTF_16LE)
            System.arraycopy(labelBytes, 0, label, 2, labelBytes.size)
            out.add(label)
        }

        val groupChecksumSkip = setOf(2, 3)
        for (child in node.children) {
            val units = child.units
            val nameEntries = ceilDiv(units.size.toLong(), 15L).toInt()
            val group = ByteArray((2 + nameEntries) * 32)

            // Primary entry (0x85)
            group[0] = 0x85.toByte()
            group[1] = (1 + nameEntries).toByte()
            ByteBuffer.wrap(group, 4, 2).order(ByteOrder.LITTLE_ENDIAN)
                .putShort(if (child.entry.isDirectory) 0x0010 else 0x0020)
            // Deterministic timestamp: 1980-01-01 00:00:00
            val ts = ByteBuffer.wrap(group, 8, 12).order(ByteOrder.LITTLE_ENDIAN)
            ts.putInt(0x00210000)
            ts.putInt(0x00210000)
            ts.putInt(0x00210000)

            // Stream extension (0xC0)
            group[32] = 0xC0.toByte()
            group[33] = if (child.entry.isDirectory || child.clusters == 0L) 1.toByte() else 3.toByte()
            group[35] = units.size.toByte()
            val upperBytes = ByteArray(units.size * 2)
            for (i in units.indices) {
                val u = upcaseMap[units[i]]
                upperBytes[i * 2] = (u and 0xFF).toByte()
                upperBytes[i * 2 + 1] = ((u ushr 8) and 0xFF).toByte()
            }
            val nameHash = checksum(upperBytes, bits = 16).toInt()
            ByteBuffer.wrap(group, 36, 2).order(ByteOrder.LITTLE_ENDIAN).putShort(nameHash.toShort())
            val length = if (child.entry.isDirectory) child.clusters * CLUSTER_SIZE else child.size
            ByteBuffer.wrap(group, 40, 8).order(ByteOrder.LITTLE_ENDIAN).putLong(length)
            ByteBuffer.wrap(group, 52, 12).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(child.cluster.toInt())
                .putLong(length)

            // File name entries (0xC1)
            var slotOffset = 64
            var start = 0
            while (start < units.size) {
                group[slotOffset] = 0xC1.toByte()
                val end = minOf(start + 15, units.size)
                for (j in start until end) {
                    val u = units[j]
                    val pos = slotOffset + 2 + (j - start) * 2
                    group[pos] = (u and 0xFF).toByte()
                    group[pos + 1] = ((u ushr 8) and 0xFF).toByte()
                }
                start += 15
                slotOffset += 32
            }

            val groupSum = checksum(group, bits = 16, skipIndices = groupChecksumSkip).toInt()
            ByteBuffer.wrap(group, 2, 2).order(ByteOrder.LITTLE_ENDIAN).putShort(groupSum.toShort())
            out.add(group)
        }

        val totalSize = out.sumOf { it.size } + 32
        val result = ByteArray(totalSize)
        var pos = 0
        for (chunk in out) {
            System.arraycopy(chunk, 0, result, pos, chunk.size)
            pos += chunk.size
        }
        return result
    }

    private fun buildBootRegion(volumeSectors: Long): ByteArray {
        val boot = ByteArray(11 * SECTOR_SIZE)
        val sig = byteArrayOf(0xEB.toByte(), 0x76, 0x90.toByte()) + "EXFAT   ".toByteArray(Charsets.US_ASCII)
        System.arraycopy(sig, 0, boot, 0, sig.size)
        val percentInUse = ceilDiv(allocatedClusters * 100L, clusterCount).toInt().coerceIn(0, 100)
        val bb = ByteBuffer.wrap(boot, 64, 49).order(ByteOrder.LITTLE_ENDIAN)
        bb.putLong(PARTITION_OFFSET_SECTORS)
        bb.putLong(volumeSectors)
        bb.putInt(24)
        bb.putInt(fatLengthSectors.toInt())
        bb.putInt(heapSector.toInt())
        bb.putInt(clusterCount.toInt())
        bb.putInt(root.cluster.toInt())
        bb.putInt(VOLUME_SERIAL)
        bb.putShort(0x0100.toShort())
        bb.putShort(0.toShort())
        bb.put(9.toByte())
        bb.put(6.toByte())
        bb.put(1.toByte())
        bb.put(0x80.toByte())
        bb.put(percentInUse.toByte())
        boot[510] = 0x55.toByte()
        boot[511] = 0xAA.toByte()
        for (i in 1..8) {
            ByteBuffer.wrap(boot, (i + 1) * SECTOR_SIZE - 4, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putInt(0xAA550000.toInt())
        }
        val crc = checksum(boot, bits = 32, skipIndices = setOf(106, 107, 112)).toInt()
        val region = ByteArray(12 * SECTOR_SIZE)
        System.arraycopy(boot, 0, region, 0, boot.size)
        val crcBuf = ByteBuffer.wrap(region, boot.size, SECTOR_SIZE).order(ByteOrder.LITTLE_ENDIAN)
        for (i in 0 until (SECTOR_SIZE / 4)) {
            crcBuf.putInt(crc)
        }
        val doubled = ByteArray(region.size * 2)
        System.arraycopy(region, 0, doubled, 0, region.size)
        System.arraycopy(region, 0, doubled, region.size, region.size)
        return doubled
    }

    fun sectorCount(): Long = volumeSizeBytes / SECTOR_SIZE

    override fun size(): Long = volumeSizeBytes

    fun seek(sector: Long, requestedSectors: Long? = null) {
        synchronized(lock) {
            val pos = sector * SECTOR_SIZE
            val end = if (requestedSectors == null) volumeSizeBytes else pos + requestedSectors * SECTOR_SIZE
            if (pos < 0 || end < pos || end > volumeSizeBytes) {
                throw IOException("Virtual disk request out of bounds: sector=$sector count=$requestedSectors")
            }
            position = pos
            requestEnd = end
        }
    }

    fun readSequential(count: Int): ByteArray {
        synchronized(lock) {
            if (count < 0) throw IOException("Negative read size")
            val limit = requestEnd ?: volumeSizeBytes
            val toRead = minOf(count.toLong(), (limit - position).coerceAtLeast(0L)).toInt()
            val out = ByteArray(toRead)
            val got = readInternal(position, out, 0, toRead)
            position += got
            return if (got == toRead) out else out.copyOf(got)
        }
    }

    override fun readAt(offset: Long, buffer: ByteArray, bufferOffset: Int, length: Int): Int {
        synchronized(lock) {
            if (closed) throw IOException("VirtualExfatDevice closed")
            if (offset < 0 || offset >= volumeSizeBytes || length == 0) return if (offset >= volumeSizeBytes) -1 else 0
            val avail = minOf(length.toLong(), volumeSizeBytes - offset).toInt()
            return readInternal(offset, buffer, bufferOffset, avail)
        }
    }

    private fun readInternal(startOffset: Long, dst: ByteArray, dstOffset: Int, length: Int): Int {
        var pos = startOffset
        val end = startOffset + length
        var written = 0
        val bootStart = PARTITION_OFFSET_SECTORS * SECTOR_SIZE
        val fatStart = (PARTITION_OFFSET_SECTORS + 24L) * SECTOR_SIZE
        val fatEnd = fatStart + fatLengthSectors * SECTOR_SIZE
        val bitmapStart = heapOffset(bitmapCluster)
        val bitmapEnd = bitmapStart + bitmapLength

        while (pos < end) {
            when {
                pos < SECTOR_SIZE -> {
                    val off = pos.toInt()
                    val chunk = minOf((end - pos).toInt(), SECTOR_SIZE - off)
                    System.arraycopy(mbr, off, dst, dstOffset + written, chunk)
                    pos += chunk
                    written += chunk
                }
                pos in bootStart until (bootStart + bootRegion.size) -> {
                    val off = (pos - bootStart).toInt()
                    val chunk = minOf((end - pos).toInt(), bootRegion.size - off)
                    System.arraycopy(bootRegion, off, dst, dstOffset + written, chunk)
                    pos += chunk
                    written += chunk
                }
                pos in fatStart until fatEnd -> {
                    val chunk = minOf((end - pos).toInt(), (fatEnd - pos).toInt())
                    readFat((pos - fatStart).toInt(), chunk, dst, dstOffset + written)
                    pos += chunk
                    written += chunk
                }
                pos in bitmapStart until bitmapEnd -> {
                    val chunk = minOf((end - pos).toInt(), (bitmapEnd - pos).toInt())
                    val firstByte = (pos - bitmapStart).toInt()
                    val fullBytes = (allocatedClusters / 8L).toInt()
                    val remBits = (allocatedClusters % 8L).toInt()
                    for (i in 0 until chunk) {
                        val bIdx = firstByte + i
                        dst[dstOffset + written + i] = when {
                            bIdx < fullBytes -> 0xFF.toByte()
                            bIdx == fullBytes -> ((1 shl remBits) - 1).toByte()
                            else -> 0
                        }
                    }
                    pos += chunk
                    written += chunk
                }
                else -> {
                    val idx = findExtentIndex(pos)
                    val ext = if (idx >= 0) extents[idx] else null
                    if (ext != null && pos < ext.start + ext.length) {
                        val off = pos - ext.start
                        val chunk = minOf(end - pos, ext.length - off).toInt()
                        val bytes = ext.bytesPayload
                        if (bytes != null) {
                            System.arraycopy(bytes, off.toInt(), dst, dstOffset + written, chunk)
                        } else {
                            readFilePayload(ext.node!!, off, dst, dstOffset + written, chunk)
                        }
                        pos += chunk
                        written += chunk
                    } else {
                        var nextBoundary = end
                        if (bootStart in (pos + 1) until nextBoundary) nextBoundary = bootStart
                        if (fatStart in (pos + 1) until nextBoundary) nextBoundary = fatStart
                        if (bitmapStart in (pos + 1) until nextBoundary) nextBoundary = bitmapStart
                        val nextExtIdx = idx + 1
                        if (nextExtIdx in extents.indices) {
                            val nextExtStart = extents[nextExtIdx].start
                            if (nextExtStart in (pos + 1) until nextBoundary) nextBoundary = nextExtStart
                        }
                        val chunk = (nextBoundary - pos).toInt()
                        dst.fill(0, dstOffset + written, dstOffset + written + chunk)
                        pos += chunk
                        written += chunk
                    }
                }
            }
        }
        return written
    }

    private fun findExtentIndex(pos: Long): Int {
        var lo = 0
        var hi = extents.size - 1
        var ans = -1
        while (lo <= hi) {
            val mid = (lo + hi) ushr 1
            if (extents[mid].start <= pos) {
                ans = mid
                lo = mid + 1
            } else {
                hi = mid - 1
            }
        }
        return ans
    }

    private fun findChainForCluster(cluster: Long): Chain? {
        var lo = 0
        var hi = chains.size - 1
        while (lo <= hi) {
            val mid = (lo + hi) ushr 1
            val ch = chains[mid]
            if (cluster < ch.start) {
                hi = mid - 1
            } else if (cluster >= ch.start + ch.length) {
                lo = mid + 1
            } else {
                return ch
            }
        }
        return null
    }

    private fun readFat(offset: Int, count: Int, dst: ByteArray, dstOffset: Int) {
        val firstCluster = offset / 4
        val lastCluster = (offset + count + 3) / 4
        val raw = ByteArray((lastCluster - firstCluster) * 4)
        val bb = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN)
        for (i in firstCluster until lastCluster) {
            val v = when {
                i == 0 -> 0xFFFFFFF8.toInt()
                i == 1 -> 0xFFFFFFFF.toInt()
                else -> {
                    val ch = findChainForCluster(i.toLong())
                    if (ch != null) {
                        if (i.toLong() < ch.start + ch.length - 1L) i + 1 else 0xFFFFFFFF.toInt()
                    } else {
                        0
                    }
                }
            }
            bb.putInt(v)
        }
        System.arraycopy(raw, offset % 4, dst, dstOffset, count)
    }

    private fun readFilePayload(node: Node, offset: Long, dst: ByteArray, dstOffset: Int, length: Int) {
        val key = node.entry.relativePath
        var cached = handles[key]
        if (cached == null) {
            val latest = vfs.stat(key)
                ?: throw IOException("File missing after virtual disk scan: $key")
            if (latest.size != node.entry.size || latest.lastModifiedMs != node.entry.lastModifiedMs) {
                throw IOException("File changed since virtual disk scan: $key")
            }
            val access = vfs.openRead(key)
            cached = CachedHandle(node, access)
            handles[key] = cached
        }
        var total = 0
        while (total < length) {
            val n = cached.handle.readAt(offset + total, dst, dstOffset + total, length - total)
            if (n <= 0) {
                throw IOException("Short read on virtual exFAT file: $key")
            }
            total += n
        }
    }

    override fun writeAt(offset: Long, buffer: ByteArray, bufferOffset: Int, length: Int): Int {
        throw IOException("Virtual exFAT is read-only; VMC writes are unavailable")
    }

    override fun setLength(newLength: Long) {
        throw IOException("Virtual exFAT is read-only")
    }

    override fun flush() {}

    override fun close() {
        synchronized(lock) {
            closed = true
            for (item in handles.values) {
                try {
                    item.handle.close()
                } catch (_: Exception) {
                }
            }
            handles.clear()
        }
    }

    private fun ceilDiv(a: Long, b: Long): Long = if (a <= 0L) 0L else (a + b - 1L) / b
}
