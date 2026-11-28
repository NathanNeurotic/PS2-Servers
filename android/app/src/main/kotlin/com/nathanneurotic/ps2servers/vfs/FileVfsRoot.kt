package com.nathanneurotic.ps2servers.vfs

import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.nio.ByteBuffer

/**
 * Local filesystem [VfsRoot] used for the fallback path and host JVM tests.
 * Uses Java 8 [FileChannel] positional reads and writes so concurrent protocol
 * threads never interfere with each other's file offsets.
 */
class FileVfsRoot(
    rootDir: File,
    override val isReadOnly: Boolean = false,
    private val strictWindowsNames: Boolean = false
) : VfsRoot {
    private val canonicalRoot: File = rootDir.canonicalFile
    private val canonicalPrefix: String = canonicalRoot.path.let {
        if (it.endsWith(File.separator)) it else it + File.separator
    }

    init {
        if (!canonicalRoot.isDirectory) {
            throw IOException("Games folder does not exist or is not a directory: ${rootDir.path}")
        }
    }

    override val displayPath: String
        get() = canonicalRoot.absolutePath

    fun resolveFile(relPath: String, followLeafSymlinkCheck: Boolean = true): File {
        val norm = PathGuard.normalizeRelative(relPath, strictWindowsNames)
        if (norm.isEmpty()) return canonicalRoot
        val candidate = File(canonicalRoot, norm.replace('/', File.separatorChar))
        val resolved = candidate.canonicalFile
        if (resolved != canonicalRoot && !resolved.path.startsWith(canonicalPrefix)) {
            throw VfsPathException("STATUS_ACCESS_DENIED", "Path escapes share root: $relPath")
        }
        // Check parent containment when creating a nonexistent leaf.
        val parent = candidate.parentFile?.canonicalFile
        if (followLeafSymlinkCheck && parent != null && parent != canonicalRoot &&
            !parent.path.startsWith(canonicalPrefix)
        ) {
            throw VfsPathException("STATUS_ACCESS_DENIED", "Parent escapes share root: $relPath")
        }
        return resolved
    }

    override fun stat(relPath: String): VfsEntry? {
        val f = resolveFile(relPath)
        if (!f.exists()) return null
        val norm = PathGuard.normalizeRelative(relPath, strictWindowsNames)
        return toEntry(f, norm)
    }

    override fun list(relDir: String): List<VfsEntry> {
        val dir = resolveFile(relDir)
        if (!dir.isDirectory) return emptyList()
        val normDir = PathGuard.normalizeRelative(relDir, strictWindowsNames)
        val children = dir.listFiles() ?: return emptyList()
        val out = ArrayList<VfsEntry>(children.size)
        for (child in children.sortedBy { it.name }) {
            val childCanon = try {
                child.canonicalFile
            } catch (_: IOException) {
                continue
            }
            if (childCanon != canonicalRoot && !childCanon.path.startsWith(canonicalPrefix)) {
                continue
            }
            if (strictWindowsNames) {
                try {
                    PathGuard.validateWindowsComponent(child.name)
                } catch (_: VfsPathException) {
                    continue
                }
            }
            val childRel = if (normDir.isEmpty()) child.name else "$normDir/${child.name}"
            out.add(toEntry(childCanon, childRel, displayName = child.name))
        }
        return out
    }

    override fun openRead(relPath: String): VfsRandomAccess {
        val f = resolveFile(relPath)
        if (!f.isFile) throw IOException("File not found: $relPath")
        return FileRandomAccess(f, writable = false)
    }

    override fun openWrite(relPath: String, create: Boolean, truncate: Boolean): VfsRandomAccess {
        if (isReadOnly) throw VfsPathException("STATUS_MEDIA_WRITE_PROTECTED", "Share is read-only")
        val norm = PathGuard.normalizeRelative(relPath, strictWindowsNames)
        val target = File(canonicalRoot, norm.replace('/', File.separatorChar))
        val parent = target.parentFile?.canonicalFile
            ?: throw IOException("Missing parent directory for $relPath")
        if (parent != canonicalRoot && !parent.path.startsWith(canonicalPrefix)) {
            throw VfsPathException("STATUS_ACCESS_DENIED", "Path escapes share root: $relPath")
        }
        val f = resolveFile(relPath)
        if (f.exists() && f.isDirectory) {
            throw VfsPathException("STATUS_FILE_IS_A_DIRECTORY", "Target is a directory: $relPath")
        }
        if (!f.exists()) {
            if (!create) throw VfsPathException("STATUS_OBJECT_NAME_NOT_FOUND", "File not found: $relPath")
            if (!parent.isDirectory && !parent.mkdirs()) {
                throw IOException("Cannot create parent directories for $relPath")
            }
            if (!f.createNewFile() && !f.exists()) {
                throw IOException("Cannot create file: $relPath")
            }
        }
        val access = FileRandomAccess(f, writable = true)
        if (truncate) {
            access.setLength(0L)
        }
        return access
    }

    override fun mkdir(relPath: String): Boolean {
        if (isReadOnly) throw VfsPathException("STATUS_MEDIA_WRITE_PROTECTED", "Share is read-only")
        val f = resolveFile(relPath)
        if (f.exists()) return false
        return f.mkdir()
    }

    override fun deleteFile(relPath: String): Boolean {
        if (isReadOnly) throw VfsPathException("STATUS_MEDIA_WRITE_PROTECTED", "Share is read-only")
        val f = resolveFile(relPath)
        if (!f.isFile) return false
        return f.delete()
    }

    override fun deleteDirectory(relPath: String): Boolean {
        if (isReadOnly) throw VfsPathException("STATUS_MEDIA_WRITE_PROTECTED", "Share is read-only")
        val f = resolveFile(relPath)
        if (f == canonicalRoot || !f.isDirectory) return false
        return f.delete()
    }

    override fun rename(oldRelPath: String, newRelPath: String, replaceIfExists: Boolean): Boolean {
        if (isReadOnly) throw VfsPathException("STATUS_MEDIA_WRITE_PROTECTED", "Share is read-only")
        val src = resolveFile(oldRelPath)
        val dst = resolveFile(newRelPath)
        if (src == canonicalRoot || dst == canonicalRoot || !src.exists()) return false
        if (dst.exists() && src.canonicalPath != dst.canonicalPath) {
            if (!replaceIfExists) return false
            if (!dst.delete()) return false
        }
        return src.renameTo(dst)
    }

    override fun statFs(): VfsFsStats {
        val total = canonicalRoot.totalSpace.coerceAtLeast(0L)
        val free = canonicalRoot.usableSpace.coerceAtLeast(0L)
        return VfsFsStats(totalBytes = total, freeBytes = free)
    }

    private fun toEntry(file: File, relPath: String, displayName: String = file.name): VfsEntry {
        val isDir = file.isDirectory
        return VfsEntry(
            name = displayName,
            relativePath = relPath,
            isDirectory = isDir,
            size = if (isDir) 0L else file.length().coerceAtLeast(0L),
            lastModifiedMs = file.lastModified().coerceAtLeast(0L)
        )
    }
}

class FileRandomAccess(
    file: File,
    private val writable: Boolean
) : VfsRandomAccess {
    private val raf = RandomAccessFile(file, if (writable) "rw" else "r")
    private val channel = raf.channel

    override fun size(): Long = channel.size()

    override fun readAt(offset: Long, buffer: ByteArray, bufferOffset: Int, length: Int): Int {
        if (offset < 0 || length < 0 || bufferOffset < 0 || bufferOffset + length > buffer.size) {
            throw IndexOutOfBoundsException("Invalid read bounds")
        }
        if (length == 0) return 0
        val bb = ByteBuffer.wrap(buffer, bufferOffset, length)
        var total = 0
        while (bb.hasRemaining()) {
            val n = channel.read(bb, offset + total)
            if (n <= 0) break
            total += n
        }
        return total
    }

    override fun writeAt(offset: Long, data: ByteArray, dataOffset: Int, length: Int): Int {
        if (!writable) throw IOException("File is opened read-only")
        if (offset < 0 || length < 0 || dataOffset < 0 || dataOffset + length > data.size) {
            throw IndexOutOfBoundsException("Invalid write bounds")
        }
        if (length == 0) return 0
        val bb = ByteBuffer.wrap(data, dataOffset, length)
        var total = 0
        while (bb.hasRemaining()) {
            total += channel.write(bb, offset + total)
        }
        return total
    }

    override fun setLength(newSize: Long) {
        if (!writable) throw IOException("File is opened read-only")
        raf.setLength(newSize)
    }

    override fun flush() {
        if (writable) {
            channel.force(false)
        }
    }

    override fun close() {
        raf.close()
    }
}
