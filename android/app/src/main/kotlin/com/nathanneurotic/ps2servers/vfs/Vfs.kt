package com.nathanneurotic.ps2servers.vfs

import java.io.Closeable
import java.io.IOException

data class VfsEntry(
    val name: String,
    val relativePath: String,
    val isDirectory: Boolean,
    val size: Long,
    val lastModifiedMs: Long
)

data class VfsFsStats(
    val totalBytes: Long,
    val freeBytes: Long
)

interface VfsRandomAccess : Closeable {
    fun size(): Long
    fun readAt(offset: Long, buffer: ByteArray, bufferOffset: Int, length: Int): Int
    fun writeAt(offset: Long, data: ByteArray, dataOffset: Int, length: Int): Int
    fun setLength(newSize: Long)
    fun flush()
}

/**
 * Stateful cursor adapter over [VfsRandomAccess] for protocols like UDPFS that
 * issue sequential READ / WRITE / LSEEK calls on an open file handle.
 */
class VfsCursorHandle(
    val access: VfsRandomAccess,
    val writable: Boolean
) : Closeable {
    private var position: Long = 0L

    @Synchronized
    fun tell(): Long = position

    @Synchronized
    fun seek(offset: Long, whence: Int): Long {
        val next = when (whence) {
            0 -> offset // SEEK_SET
            1 -> position + offset // SEEK_CUR
            2 -> access.size() + offset // SEEK_END
            else -> throw IOException("Unsupported seek whence=$whence")
        }
        if (next < 0L) {
            throw IOException("Negative seek offset: $next")
        }
        position = next
        return position
    }

    @Synchronized
    fun read(length: Int): ByteArray {
        if (length <= 0) return ByteArray(0)
        val fileSize = access.size()
        if (position >= fileSize) return ByteArray(0)
        val toRead = minOf(length.toLong(), fileSize - position).toInt()
        val buf = ByteArray(toRead)
        var total = 0
        while (total < toRead) {
            val n = access.readAt(position + total, buf, total, toRead - total)
            if (n <= 0) break
            total += n
        }
        position += total
        return if (total == buf.size) buf else buf.copyOf(total)
    }

    @Synchronized
    fun write(data: ByteArray, offset: Int = 0, length: Int = data.size): Int {
        if (!writable) throw IOException("Handle is read-only")
        if (length <= 0) return 0
        val written = access.writeAt(position, data, offset, length)
        position += written
        return written
    }

    @Synchronized
    fun flush() {
        access.flush()
    }

    override fun close() {
        access.close()
    }
}

class VfsPathException(val code: String, message: String) : IOException(message)

object PathGuard {
    private val RESERVED_WINDOWS_NAMES = setOf(
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
    )
    private const val ILLEGAL_WINDOWS_CHARS = "<>:\"|?*"

    fun normalizeRelative(rawPath: String, strictWindowsNames: Boolean = false): String {
        if (rawPath.indexOf('\u0000') >= 0) {
            throw VfsPathException("STATUS_OBJECT_NAME_INVALID", "NUL byte in path")
        }
        val replaced = rawPath.replace('\\', '/')
        val trimmed = replaced.trim('/')
        if (trimmed.isEmpty() || trimmed == ".") return ""
        val parts = trimmed.split('/')
        val out = ArrayList<String>(parts.size)
        for (part in parts) {
            if (part.isEmpty() || part == ".") continue
            if (part == "..") {
                throw VfsPathException("STATUS_OBJECT_PATH_SYNTAX_BAD", "Parent traversal '..' is refused")
            }
            if (strictWindowsNames) {
                validateWindowsComponent(part)
            }
            out.add(part)
        }
        return out.joinToString("/")
    }

    fun validateWindowsComponent(name: String) {
        if (name.isEmpty()) {
            throw VfsPathException("STATUS_OBJECT_NAME_INVALID", "Empty path component")
        }
        for (ch in name) {
            if (ch.code < 0x20 || ch in ILLEGAL_WINDOWS_CHARS) {
                throw VfsPathException("STATUS_OBJECT_NAME_INVALID", "Illegal character in '$name'")
            }
        }
        if (name.endsWith('.') || name.endsWith(' ')) {
            throw VfsPathException("STATUS_OBJECT_NAME_INVALID", "Trailing dot or space in '$name'")
        }
        val base = name.substringBefore('.').uppercase()
        if (base in RESERVED_WINDOWS_NAMES) {
            throw VfsPathException("STATUS_OBJECT_NAME_INVALID", "Reserved device name '$name'")
        }
    }
}

interface VfsRoot {
    val displayPath: String
    val isReadOnly: Boolean

    fun stat(relPath: String): VfsEntry?
    fun list(relDir: String): List<VfsEntry>
    fun openRead(relPath: String): VfsRandomAccess
    fun openWrite(relPath: String, create: Boolean, truncate: Boolean): VfsRandomAccess
    fun mkdir(relPath: String): Boolean
    fun deleteFile(relPath: String): Boolean
    fun deleteDirectory(relPath: String): Boolean
    fun rename(oldRelPath: String, newRelPath: String, replaceIfExists: Boolean): Boolean
    fun statFs(): VfsFsStats
}
