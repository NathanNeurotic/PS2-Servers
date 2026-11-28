package com.nathanneurotic.ps2servers.vfs

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.os.StatFs
import android.provider.DocumentsContract
import android.system.ErrnoException
import android.system.Os
import androidx.documentfile.provider.DocumentFile
import java.io.FileNotFoundException
import java.io.IOException

/**
 * Storage Access Framework [VfsRoot] backed by a persisted tree URI.
 *
 * Uses [DocumentsContract.queryChildDocuments] for fast single-IPC directory
 * scans and API 21+ [Os.pread]/[Os.pwrite] over [ParcelFileDescriptor] for
 * thread-safe positional reads and writes without holding seek state.
 */
class SafVfsRoot(
    context: Context,
    private val treeUri: Uri,
    override val isReadOnly: Boolean = false,
    private val strictWindowsNames: Boolean = false
) : VfsRoot {
    private val appContext: Context = context.applicationContext
    private val resolver: ContentResolver = appContext.contentResolver
    private val rootDoc: DocumentFile = DocumentFile.fromTreeUri(appContext, treeUri)
        ?: throw IOException("Cannot open SAF tree URI: $treeUri")

    private data class CachedChildren(val timestampMs: Long, val entries: List<SafNode>)
    private data class SafNode(
        val entry: VfsEntry,
        val documentId: String,
        val uri: Uri
    )

    private val cacheLock = Any()
    private val dirCache = HashMap<String, CachedChildren>()
    private val cacheTtlMs = 2000L

    override val displayPath: String
        get() = "SAF: ${rootDoc.name ?: treeUri.lastPathSegment ?: treeUri.toString()}"

    private fun rootDocId(): String = DocumentsContract.getTreeDocumentId(treeUri)

    private fun invalidateCache(relDir: String? = null) {
        synchronized(cacheLock) {
            if (relDir == null) {
                dirCache.clear()
            } else {
                dirCache.remove(relDir)
            }
        }
    }

    private fun queryChildren(parentDocId: String, relDir: String): List<SafNode> {
        val now = System.currentTimeMillis()
        synchronized(cacheLock) {
            val cached = dirCache[relDir]
            if (cached != null && now - cached.timestampMs < cacheTtlMs) {
                return cached.entries
            }
        }
        val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, parentDocId)
        val projection = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_SIZE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED
        )
        val out = ArrayList<SafNode>()
        try {
            resolver.query(childrenUri, projection, null, null, null)?.use { cursor ->
                val idIdx = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
                val nameIdx = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
                val mimeIdx = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_MIME_TYPE)
                val sizeIdx = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_SIZE)
                val modIdx = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_LAST_MODIFIED)
                while (cursor.moveToNext()) {
                    val docId = if (idIdx >= 0) cursor.getString(idIdx) else null
                    val name = if (nameIdx >= 0) cursor.getString(nameIdx) else null
                    if (docId.isNullOrEmpty() || name.isNullOrEmpty()) continue
                    val mime = if (mimeIdx >= 0) cursor.getString(mimeIdx) else ""
                    val isDir = mime == DocumentsContract.Document.MIME_TYPE_DIR
                    val size = if (isDir || sizeIdx < 0 || cursor.isNull(sizeIdx)) 0L else cursor.getLong(sizeIdx)
                    val mod = if (modIdx < 0 || cursor.isNull(modIdx)) 0L else cursor.getLong(modIdx)
                    val childRel = if (relDir.isEmpty()) name else "$relDir/$name"
                    val uri = DocumentsContract.buildDocumentUriUsingTree(treeUri, docId)
                    out.add(
                        SafNode(
                            entry = VfsEntry(name, childRel, isDir, size.coerceAtLeast(0L), mod.coerceAtLeast(0L)),
                            documentId = docId,
                            uri = uri
                        )
                    )
                }
            }
        } catch (_: Exception) {
            // Fall back to DocumentFile traversal when a custom provider rejects projection.
            val parentDoc = DocumentFile.fromTreeUri(
                appContext,
                DocumentsContract.buildDocumentUriUsingTree(treeUri, parentDocId)
            )
            if (parentDoc != null) {
                for (df in parentDoc.listFiles()) {
                    val name = df.name ?: continue
                    val isDir = df.isDirectory
                    val docId = DocumentsContract.getDocumentId(df.uri)
                    val childRel = if (relDir.isEmpty()) name else "$relDir/$name"
                    out.add(
                        SafNode(
                            entry = VfsEntry(name, childRel, isDir, if (isDir) 0L else df.length(), df.lastModified()),
                            documentId = docId,
                            uri = df.uri
                        )
                    )
                }
            }
        }
        val sorted = out.sortedBy { it.entry.name }
        synchronized(cacheLock) {
            dirCache[relDir] = CachedChildren(now, sorted)
        }
        return sorted
    }

    private fun resolveNode(relPath: String): SafNode? {
        val norm = PathGuard.normalizeRelative(relPath, strictWindowsNames)
        if (norm.isEmpty()) {
            val rootId = rootDocId()
            return SafNode(
                entry = VfsEntry("", "", true, 0L, rootDoc.lastModified().coerceAtLeast(0L)),
                documentId = rootId,
                uri = DocumentsContract.buildDocumentUriUsingTree(treeUri, rootId)
            )
        }
        val segments = norm.split('/')
        var currentId = rootDocId()
        var currentRel = ""
        var currentNode: SafNode? = null
        for (i in segments.indices) {
            val seg = segments[i]
            val children = queryChildren(currentId, currentRel)
            val match = children.firstOrNull { it.entry.name == seg }
                ?: children.firstOrNull { it.entry.name.equals(seg, ignoreCase = true) }
                ?: return null
            if (i < segments.lastIndex && !match.entry.isDirectory) {
                return null
            }
            currentNode = match
            currentId = match.documentId
            currentRel = match.entry.relativePath
        }
        return currentNode
    }

    override fun stat(relPath: String): VfsEntry? = resolveNode(relPath)?.entry

    override fun list(relDir: String): List<VfsEntry> {
        val norm = PathGuard.normalizeRelative(relDir, strictWindowsNames)
        val node = resolveNode(norm) ?: return emptyList()
        if (!node.entry.isDirectory) return emptyList()
        return queryChildren(node.documentId, norm).mapNotNull { child ->
            if (strictWindowsNames) {
                try {
                    PathGuard.validateWindowsComponent(child.entry.name)
                } catch (_: VfsPathException) {
                    return@mapNotNull null
                }
            }
            child.entry
        }
    }

    override fun openRead(relPath: String): VfsRandomAccess {
        val node = resolveNode(relPath) ?: throw FileNotFoundException("File not found: $relPath")
        if (node.entry.isDirectory) throw IOException("Cannot open directory for read: $relPath")
        return SafRandomAccess(resolver, node.uri, writable = false, reportedSize = node.entry.size)
    }

    override fun openWrite(relPath: String, create: Boolean, truncate: Boolean): VfsRandomAccess {
        if (isReadOnly) throw VfsPathException("STATUS_MEDIA_WRITE_PROTECTED", "Share is read-only")
        val norm = PathGuard.normalizeRelative(relPath, strictWindowsNames)
        if (norm.isEmpty()) throw IOException("Cannot open share root as file")
        val existing = resolveNode(norm)
        val targetUri: Uri
        if (existing != null) {
            if (existing.entry.isDirectory) {
                throw VfsPathException("STATUS_FILE_IS_A_DIRECTORY", "Target is a directory: $relPath")
            }
            targetUri = existing.uri
        } else {
            if (!create) throw VfsPathException("STATUS_OBJECT_NAME_NOT_FOUND", "File not found: $relPath")
            val parentRel = norm.substringBeforeLast('/', "")
            val leafName = norm.substringAfterLast('/')
            val parentNode = resolveNode(parentRel)
                ?: throw VfsPathException("STATUS_OBJECT_PATH_NOT_FOUND", "Parent directory missing: $parentRel")
            if (!parentNode.entry.isDirectory) {
                throw VfsPathException("STATUS_NOT_A_DIRECTORY", "Parent is not a directory: $parentRel")
            }
            targetUri = DocumentsContract.createDocument(
                resolver,
                parentNode.uri,
                "application/octet-stream",
                leafName
            ) ?: throw IOException("Failed to create SAF document: $relPath")
            invalidateCache(parentRel)
        }
        val mode = if (truncate) "rwt" else "rw"
        return try {
            SafRandomAccess(resolver, targetUri, writable = true, openMode = mode)
        } catch (e: Exception) {
            if (truncate) {
                SafRandomAccess(resolver, targetUri, writable = true, openMode = "rw").also {
                    it.setLength(0L)
                }
            } else {
                throw e
            }
        }
    }

    override fun mkdir(relPath: String): Boolean {
        if (isReadOnly) throw VfsPathException("STATUS_MEDIA_WRITE_PROTECTED", "Share is read-only")
        val norm = PathGuard.normalizeRelative(relPath, strictWindowsNames)
        if (norm.isEmpty() || resolveNode(norm) != null) return false
        val parentRel = norm.substringBeforeLast('/', "")
        val leafName = norm.substringAfterLast('/')
        val parentNode = resolveNode(parentRel) ?: return false
        if (!parentNode.entry.isDirectory) return false
        val created = DocumentsContract.createDocument(
            resolver,
            parentNode.uri,
            DocumentsContract.Document.MIME_TYPE_DIR,
            leafName
        )
        invalidateCache(parentRel)
        return created != null
    }

    override fun deleteFile(relPath: String): Boolean {
        if (isReadOnly) throw VfsPathException("STATUS_MEDIA_WRITE_PROTECTED", "Share is read-only")
        val norm = PathGuard.normalizeRelative(relPath, strictWindowsNames)
        val node = resolveNode(norm) ?: return false
        if (node.entry.isDirectory) return false
        val ok = try {
            DocumentsContract.deleteDocument(resolver, node.uri)
        } catch (_: Exception) {
            false
        }
        invalidateCache(norm.substringBeforeLast('/', ""))
        return ok
    }

    override fun deleteDirectory(relPath: String): Boolean {
        if (isReadOnly) throw VfsPathException("STATUS_MEDIA_WRITE_PROTECTED", "Share is read-only")
        val norm = PathGuard.normalizeRelative(relPath, strictWindowsNames)
        if (norm.isEmpty()) return false
        val node = resolveNode(norm) ?: return false
        if (!node.entry.isDirectory) return false
        if (queryChildren(node.documentId, norm).isNotEmpty()) return false
        val ok = try {
            DocumentsContract.deleteDocument(resolver, node.uri)
        } catch (_: Exception) {
            false
        }
        invalidateCache(norm.substringBeforeLast('/', ""))
        invalidateCache(norm)
        return ok
    }

    override fun rename(oldRelPath: String, newRelPath: String, replaceIfExists: Boolean): Boolean {
        if (isReadOnly) throw VfsPathException("STATUS_MEDIA_WRITE_PROTECTED", "Share is read-only")
        val oldNorm = PathGuard.normalizeRelative(oldRelPath, strictWindowsNames)
        val newNorm = PathGuard.normalizeRelative(newRelPath, strictWindowsNames)
        if (oldNorm.isEmpty() || newNorm.isEmpty()) return false
        val oldParent = oldNorm.substringBeforeLast('/', "")
        val newParent = newNorm.substringBeforeLast('/', "")
        val newName = newNorm.substringAfterLast('/')
        val srcNode = resolveNode(oldNorm) ?: return false
        val existingDst = resolveNode(newNorm)
        if (existingDst != null && existingDst.documentId != srcNode.documentId) {
            if (!replaceIfExists) return false
            try {
                DocumentsContract.deleteDocument(resolver, existingDst.uri)
            } catch (_: Exception) {
                return false
            }
        }
        val renamedUri = try {
            if (oldParent == newParent) {
                DocumentsContract.renameDocument(resolver, srcNode.uri, newName)
            } else {
                val srcParentNode = resolveNode(oldParent) ?: return false
                val dstParentNode = resolveNode(newParent) ?: return false
                val moved = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.N) {
                    DocumentsContract.moveDocument(resolver, srcNode.uri, srcParentNode.uri, dstParentNode.uri)
                } else {
                    null
                }
                if (moved != null && newName != oldNorm.substringAfterLast('/')) {
                    DocumentsContract.renameDocument(resolver, moved, newName)
                } else {
                    moved
                }
            }
        } catch (_: Exception) {
            null
        }
        invalidateCache()
        return renamedUri != null
    }

    override fun statFs(): VfsFsStats {
        // Attempt statvfs on the root PFD when available; otherwise fall back to safe defaults.
        return try {
            resolver.openFileDescriptor(
                DocumentsContract.buildDocumentUriUsingTree(treeUri, rootDocId()),
                "r"
            )?.use { pfd ->
                val st = Os.fstatvfs(pfd.fileDescriptor)
                val total = st.f_blocks * st.f_frsize
                val free = st.f_bavail * st.f_frsize
                VfsFsStats(total, free)
            } ?: VfsFsStats(0L, 0L)
        } catch (_: Exception) {
            try {
                val sf = StatFs("/storage/emulated/0")
                VfsFsStats(sf.totalBytes, sf.availableBytes)
            } catch (_: Exception) {
                VfsFsStats(64L * 1024 * 1024 * 1024, 16L * 1024 * 1024 * 1024)
            }
        }
    }
}

/**
 * Positional random-access wrapper over an Android SAF [ParcelFileDescriptor].
 */
class SafRandomAccess(
    resolver: ContentResolver,
    uri: Uri,
    private val writable: Boolean,
    openMode: String = if (writable) "rw" else "r",
    private val reportedSize: Long = -1L
) : VfsRandomAccess {
    private val pfd: ParcelFileDescriptor = resolver.openFileDescriptor(uri, openMode)
        ?: throw IOException("Cannot open SAF file descriptor for $uri")

    override fun size(): Long {
        val statSize = pfd.statSize
        if (statSize >= 0L) return statSize
        return try {
            Os.fstat(pfd.fileDescriptor).st_size
        } catch (_: Exception) {
            if (reportedSize >= 0L) reportedSize else 0L
        }
    }

    override fun readAt(offset: Long, buffer: ByteArray, bufferOffset: Int, length: Int): Int {
        if (offset < 0 || length < 0 || bufferOffset < 0 || bufferOffset + length > buffer.size) {
            throw IndexOutOfBoundsException("Invalid read bounds")
        }
        if (length == 0) return 0
        var total = 0
        while (total < length) {
            val n = try {
                Os.pread(pfd.fileDescriptor, buffer, bufferOffset + total, length - total, offset + total)
            } catch (e: ErrnoException) {
                throw IOException("pread failed: ${e.message}", e)
            }
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
        var total = 0
        while (total < length) {
            val n = try {
                Os.pwrite(pfd.fileDescriptor, data, dataOffset + total, length - total, offset + total)
            } catch (e: ErrnoException) {
                throw IOException("pwrite failed: ${e.message}", e)
            }
            if (n <= 0) break
            total += n
        }
        return total
    }

    override fun setLength(newSize: Long) {
        if (!writable) throw IOException("File is opened read-only")
        try {
            Os.ftruncate(pfd.fileDescriptor, newSize)
        } catch (e: ErrnoException) {
            throw IOException("ftruncate failed: ${e.message}", e)
        }
    }

    override fun flush() {
        if (writable) {
            try {
                Os.fsync(pfd.fileDescriptor)
            } catch (_: Exception) {
                // Some virtual SAF providers do not support fsync; ignore cleanly.
            }
        }
    }

    override fun close() {
        pfd.close()
    }
}
