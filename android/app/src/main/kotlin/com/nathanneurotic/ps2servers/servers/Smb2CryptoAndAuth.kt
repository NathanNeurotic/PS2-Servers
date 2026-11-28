package com.nathanneurotic.ps2servers.servers

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Locale
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Cryptographic primitives, NTLMv2 verification, SPNEGO/bare-NTLMSSP encoding,
 * and IPC$/srvsvc NetrShareEnum pipe for the SMB2/SMB3 server.
 */
object Smb2Crypto {
    private val rng = SecureRandom()

    fun randomBytes(n: Int): ByteArray = ByteArray(n).also { rng.nextBytes(it) }

    fun md4(input: ByteArray): ByteArray {
        val bitLen = input.size.toLong() * 8L
        val padMod = (56 - (input.size + 1) % 64 + 64) % 64
        val padded = ByteArray(input.size + 1 + padMod + 8)
        System.arraycopy(input, 0, padded, 0, input.size)
        padded[input.size] = 0x80.toByte()
        ByteBuffer.wrap(padded, padded.size - 8, 8).order(ByteOrder.LITTLE_ENDIAN).putLong(bitLen)

        var a = 0x67452301
        var b = 0xEFCDAB89.toInt()
        var c = 0x98BADCFE.toInt()
        var d = 0x10325476
        val x = IntArray(16)
        val bb = ByteBuffer.wrap(padded).order(ByteOrder.LITTLE_ENDIAN)

        for (off in padded.indices step 64) {
            for (i in 0 until 16) x[i] = bb.getInt(off + i * 4)
            val aa = a; val bbv = b; val cc = c; val dd = d

            for (i in 0 until 16 step 4) {
                a = rotl(a + ((b and c) or (b.inv() and d)) + x[i], 3)
                d = rotl(d + ((a and b) or (a.inv() and c)) + x[i + 1], 7)
                c = rotl(c + ((d and a) or (d.inv() and b)) + x[i + 2], 11)
                b = rotl(b + ((c and d) or (c.inv() and a)) + x[i + 3], 19)
            }
            for (i in 0 until 4) {
                a = rotl(a + ((b and c) or (b and d) or (c and d)) + x[i] + 0x5A827999, 3)
                d = rotl(d + ((a and b) or (a and c) or (b and c)) + x[i + 4] + 0x5A827999, 5)
                c = rotl(c + ((d and a) or (d and b) or (a and b)) + x[i + 8] + 0x5A827999, 9)
                b = rotl(b + ((c and d) or (c and a) or (d and a)) + x[i + 12] + 0x5A827999, 13)
            }
            for (i in intArrayOf(0, 2, 1, 3)) {
                a = rotl(a + (b xor c xor d) + x[i] + 0x6ED9EBA1, 3)
                d = rotl(d + (a xor b xor c) + x[i + 8] + 0x6ED9EBA1, 9)
                c = rotl(c + (d xor a xor b) + x[i + 4] + 0x6ED9EBA1, 11)
                b = rotl(b + (c xor d xor a) + x[i + 12] + 0x6ED9EBA1, 15)
            }

            a += aa; b += bbv; c += cc; d += dd
        }
        return ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(a).putInt(b).putInt(c).putInt(d).array()
    }

    private fun rotl(x: Int, n: Int): Int = (x shl n) or (x ushr (32 - n))

    fun hmacMd5(key: ByteArray, data: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacMD5")
        mac.init(SecretKeySpec(key, "HmacMD5"))
        return mac.doFinal(data)
    }

    fun hmacSha256(key: ByteArray, data: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        return mac.doFinal(data)
    }

    fun ntowfv2(password: String, user: String, domain: String): ByteArray {
        val ntHash = md4(password.toByteArray(Charsets.UTF_16LE))
        val identity = (user.uppercase(Locale.ROOT) + domain).toByteArray(Charsets.UTF_16LE)
        return hmacMd5(ntHash, identity)
    }

    fun rc4(key: ByteArray, data: ByteArray): ByteArray {
        val s = IntArray(256) { it }
        var j = 0
        for (i in 0 until 256) {
            j = (j + s[i] + (key[i % key.size].toInt() and 0xFF)) and 0xFF
            val t = s[i]; s[i] = s[j]; s[j] = t
        }
        val out = ByteArray(data.size)
        var i = 0; j = 0
        for (k in data.indices) {
            i = (i + 1) and 0xFF
            j = (j + s[i]) and 0xFF
            val t = s[i]; s[i] = s[j]; s[j] = t
            out[k] = (data[k].toInt() xor s[(s[i] + s[j]) and 0xFF]).toByte()
        }
        return out
    }

    fun aesCmac(key16: ByteArray, message: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/ECB/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key16, "AES"))
        val zero = ByteArray(16)
        val lBlock = cipher.doFinal(zero)
        val k1 = shiftLeftOne(lBlock)
        val k2 = shiftLeftOne(k1)

        val n = maxOf(1, (message.size + 15) / 16)
        val complete = message.isNotEmpty() && message.size % 16 == 0
        val last = ByteArray(16)
        val lastStart = (n - 1) * 16
        val rem = message.size - lastStart
        if (complete) {
            for (i in 0 until 16) last[i] = (message[lastStart + i].toInt() xor k1[i].toInt()).toByte()
        } else {
            System.arraycopy(message, lastStart, last, 0, rem)
            last[rem] = 0x80.toByte()
            for (i in 0 until 16) last[i] = (last[i].toInt() xor k2[i].toInt()).toByte()
        }

        var x = ByteArray(16)
        val buf = ByteArray(16)
        for (b in 0 until n - 1) {
            val base = b * 16
            for (i in 0 until 16) buf[i] = (x[i].toInt() xor message[base + i].toInt()).toByte()
            x = cipher.doFinal(buf)
        }
        for (i in 0 until 16) buf[i] = (x[i].toInt() xor last[i].toInt()).toByte()
        return cipher.doFinal(buf)
    }

    private fun shiftLeftOne(block: ByteArray): ByteArray {
        val out = ByteArray(16)
        var carry = 0
        for (i in 15 downTo 0) {
            val v = block[i].toInt() and 0xFF
            out[i] = (((v shl 1) or carry) and 0xFF).toByte()
            carry = (v ushr 7) and 1
        }
        if ((block[0].toInt() and 0x80) != 0) {
            out[15] = (out[15].toInt() xor 0x87).toByte()
        }
        return out
    }

    fun kdf(key: ByteArray, label: String, context: String, length: Int = 16): ByteArray {
        val lb = label.toByteArray(Charsets.US_ASCII)
        val ctx = context.toByteArray(Charsets.US_ASCII)
        val out = ByteArray(length)
        var pos = 0
        var counter = 1
        while (pos < length) {
            val inp = ByteBuffer.allocate(4 + lb.size + 2 + ctx.size + 1 + 4).order(ByteOrder.BIG_ENDIAN)
                .putInt(counter)
                .put(lb).put(0).put(0)
                .put(ctx).put(0)
                .putInt(length * 8)
                .array()
            val dig = hmacSha256(key, inp)
            val copy = minOf(dig.size, length - pos)
            System.arraycopy(dig, 0, out, pos, copy)
            pos += copy
            counter++
        }
        return out
    }

    fun signingKey(sessionKey: ByteArray, dialect: Int): ByteArray {
        val key = if (sessionKey.size >= 16) sessionKey.copyOf(16) else sessionKey
        return if (dialect >= 0x0300) kdf(key, "SMB2AESCMAC", "SmbSign", 16) else key
    }

    fun sign(signingKeyBytes: ByteArray, dialect: Int, message: ByteArray): ByteArray {
        val zeroed = message.copyOf()
        for (i in 48 until minOf(64, zeroed.size)) zeroed[i] = 0
        return if (dialect >= 0x0300) {
            aesCmac(signingKeyBytes, zeroed)
        } else {
            hmacSha256(signingKeyBytes, zeroed).copyOf(16)
        }
    }

    fun applySignature(signingKeyBytes: ByteArray, dialect: Int, message: ByteArray): ByteArray {
        val mac = sign(signingKeyBytes, dialect, message)
        val out = message.copyOf()
        System.arraycopy(mac, 0, out, 48, 16)
        return out
    }

    fun verifySignature(signingKeyBytes: ByteArray, dialect: Int, message: ByteArray): Boolean {
        if (message.size < 64) return false
        val expected = sign(signingKeyBytes, dialect, message)
        val actual = message.copyOfRange(48, 64)
        return MessageDigest.isEqual(expected, actual)
    }
}

object Smb2Auth {
    private val NTLMV2_HEADER = byteArrayOf(0x01, 0x01, 0, 0, 0, 0, 0, 0)
    val NTLMSSP_SIG = "NTLMSSP\u0000".toByteArray(Charsets.US_ASCII)
    val SPNEGO_OID = byteArrayOf(0x06, 0x06, 0x2B, 0x06, 0x01, 0x05, 0x05, 0x02)
    val NTLMSSP_OID = byteArrayOf(0x06, 0x0A, 0x2B, 0x06, 0x01, 0x04, 0x01, 0x82.toByte(), 0x37, 0x02, 0x02, 0x0A)

    const val NTLM_NEGOTIATE = 1
    const val NTLM_CHALLENGE = 2
    const val NTLM_AUTHENTICATE = 3

    const val NEGOTIATE_SIGN = 0x00000010
    const val NEGOTIATE_KEY_EXCH = 0x40000000
    const val NEGOTIATE_VERSION = 0x02000000
    const val CHALLENGE_BASE_FLAGS = 0x00000001 or 0x00000004 or 0x00000200 or 0x00008000 or 0x00080000 or 0x00800000 or 0x20000000 or 0x80000000.toInt()
    const val ECHOED_FLAGS = NEGOTIATE_SIGN or NEGOTIATE_KEY_EXCH or NEGOTIATE_VERSION

    class Credentials(
        val user: String,
        val domain: String,
        val ntResponse: ByteArray,
        val lmResponse: ByteArray,
        val flags: Int,
        val encryptedSessionKey: ByteArray,
        val mic: ByteArray?,
        val raw: ByteArray
    ) {
        val isAnonymous: Boolean get() = user.isEmpty() && ntResponse.isEmpty()
    }

    class AuthFailure(val status: Int, message: String) : Exception(message)

    private fun derLen(n: Int): ByteArray {
        if (n < 0x80) return byteArrayOf(n.toByte())
        val bytes = ArrayList<Byte>()
        var v = n
        while (v > 0) {
            bytes.add(0, (v and 0xFF).toByte())
            v = v ushr 8
        }
        return byteArrayOf((0x80 or bytes.size).toByte()) + bytes.toByteArray()
    }

    private fun tlv(tag: Int, value: ByteArray): ByteArray = byteArrayOf(tag.toByte()) + derLen(value.size) + value

    fun negTokenInit(): ByteArray {
        val mechList = tlv(0x30, NTLMSSP_OID)
        val token = tlv(0x30, tlv(0xA0, mechList))
        return tlv(0x60, SPNEGO_OID + tlv(0xA0, token))
    }

    fun negTokenResp(state: Int, responseToken: ByteArray? = null, withMech: Boolean = false): ByteArray {
        var body = tlv(0xA0, tlv(0x0A, byteArrayOf(state.toByte())))
        if (withMech) body += tlv(0xA1, NTLMSSP_OID)
        if (responseToken != null) body += tlv(0xA2, tlv(0x04, responseToken))
        return tlv(0xA1, tlv(0x30, body))
    }

    fun isRawNtlmssp(blob: ByteArray): Boolean =
        blob.size >= NTLMSSP_SIG.size && blob.copyOfRange(0, NTLMSSP_SIG.size).contentEquals(NTLMSSP_SIG)

    fun extractNtlm(blob: ByteArray): ByteArray? {
        for (i in 0..blob.size - NTLMSSP_SIG.size) {
            var match = true
            for (j in NTLMSSP_SIG.indices) {
                if (blob[i + j] != NTLMSSP_SIG[j]) {
                    match = false
                    break
                }
            }
            if (match) return blob.copyOfRange(i, blob.size)
        }
        return null
    }

    fun messageType(token: ByteArray): Int? {
        if (token.size < 12) return null
        return ByteBuffer.wrap(token, 8, 4).order(ByteOrder.LITTLE_ENDIAN).int
    }

    fun trimNtlm(token: ByteArray): ByteArray {
        val kind = messageType(token) ?: return token
        val fieldOffsets: IntArray
        val fixed: Int
        val flagsAt: Int
        when (kind) {
            NTLM_NEGOTIATE -> {
                fieldOffsets = intArrayOf(16, 24); fixed = 32; flagsAt = 12
            }
            NTLM_AUTHENTICATE -> {
                fieldOffsets = intArrayOf(12, 20, 28, 36, 44, 52); fixed = 64; flagsAt = 60
            }
            else -> return token
        }
        if (token.size < fixed) return token
        val bb = ByteBuffer.wrap(token).order(ByteOrder.LITTLE_ENDIAN)
        var end = fixed
        if ((bb.getInt(flagsAt) and NEGOTIATE_VERSION) != 0) end += 8
        for (off in fieldOffsets) {
            val len = bb.getShort(off).toInt() and 0xFFFF
            val at = bb.getInt(off + 4)
            if (len > 0) end = maxOf(end, at + len)
        }
        return token.copyOf(minOf(end, token.size))
    }

    private fun avPair(kind: Int, value: ByteArray): ByteArray {
        val out = ByteArray(4 + value.size)
        ByteBuffer.wrap(out, 0, 4).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(kind.toShort()).putShort(value.size.toShort())
        System.arraycopy(value, 0, out, 4, value.size)
        return out
    }

    fun buildChallenge(
        challenge: ByteArray,
        targetName: String,
        computer: String,
        compact: Boolean,
        clientFlags: Int
    ): ByteArray {
        val flags = CHALLENGE_BASE_FLAGS or (clientFlags and ECHOED_FLAGS)
        val target = targetName.toByteArray(Charsets.UTF_16LE)
        val comp = computer.toByteArray(Charsets.UTF_16LE)
        var info = avPair(1, target) + avPair(2, comp)
        if (!compact) {
            info += avPair(3, target) + avPair(4, comp)
        }
        info += avPair(0, ByteArray(0))
        val targetAt = 56
        val infoAt = targetAt + target.size
        val out = ByteArray(56 + target.size + info.size)
        System.arraycopy(NTLMSSP_SIG, 0, out, 0, 8)
        val bb = ByteBuffer.wrap(out, 8, 48).order(ByteOrder.LITTLE_ENDIAN)
        bb.putInt(NTLM_CHALLENGE)
        bb.putShort(target.size.toShort()).putShort(target.size.toShort()).putInt(targetAt)
        bb.putInt(flags)
        bb.put(challenge)
        bb.putLong(0L)
        bb.putShort(info.size.toShort()).putShort(info.size.toShort()).putInt(infoAt)
        bb.put(6).put(1).putShort(7601).put(0).put(0).put(0).put(15)
        System.arraycopy(target, 0, out, targetAt, target.size)
        System.arraycopy(info, 0, out, infoAt, info.size)
        return out
    }

    private fun readField(bb: ByteBuffer, token: ByteArray, offset: Int): ByteArray {
        val len = bb.getShort(offset).toInt() and 0xFFFF
        val at = bb.getInt(offset + 4)
        if (len == 0) return ByteArray(0)
        if (at < 0 || at + len > token.size) {
            throw AuthFailure(0xC000000D.toInt(), "NTLMSSP field out of bounds")
        }
        return token.copyOfRange(at, at + len)
    }

    fun parseAuthenticate(rawToken: ByteArray): Credentials {
        val token = trimNtlm(rawToken)
        if (token.size < 64) throw AuthFailure(0xC000000D.toInt(), "Short AUTHENTICATE")
        val bb = ByteBuffer.wrap(token).order(ByteOrder.LITTLE_ENDIAN)
        val lm = readField(bb, token, 12)
        val nt = readField(bb, token, 20)
        val domain = String(readField(bb, token, 28), Charsets.UTF_16LE)
        val user = String(readField(bb, token, 36), Charsets.UTF_16LE)
        val encKey = readField(bb, token, 52)
        val flags = bb.getInt(60)

        var mic: ByteArray? = null
        var at = 16 + 28
        var avFlags = 0
        while (at + 4 <= nt.size) {
            val k = (nt[at].toInt() and 0xFF) or ((nt[at + 1].toInt() and 0xFF) shl 8)
            val l = (nt[at + 2].toInt() and 0xFF) or ((nt[at + 3].toInt() and 0xFF) shl 8)
            at += 4
            if (k == 0) break
            if (k == 6 && l >= 4 && at + 4 <= nt.size) {
                avFlags = ByteBuffer.wrap(nt, at, 4).order(ByteOrder.LITTLE_ENDIAN).int
            }
            at += l
        }
        if ((avFlags and 0x02) != 0) {
            var payloadStart = token.size
            for (off in intArrayOf(12, 20, 28, 36, 44, 52)) {
                if ((bb.getShort(off).toInt() and 0xFFFF) > 0) {
                    payloadStart = minOf(payloadStart, bb.getInt(off + 4))
                }
            }
            if (token.size < 88 || payloadStart < 88) {
                throw AuthFailure(0xC000000D.toInt(), "Invalid AUTHENTICATE MIC layout")
            }
            mic = token.copyOfRange(72, 88)
        }
        return Credentials(user, domain, nt, lm, flags, encKey, mic, token)
    }

    fun verifyCredentials(
        expectedUser: String,
        expectedPassword: String,
        openShare: Boolean,
        creds: Credentials,
        challenge: ByteArray
    ): ByteArray? {
        if (openShare) return null
        if (creds.isAnonymous) throw AuthFailure(0xC000006D.toInt(), "Anonymous logon refused")
        if (creds.ntResponse.size < 24) throw AuthFailure(0xC000006D.toInt(), "NTLMv2 response too short")
        val proof = creds.ntResponse.copyOfRange(0, 16)
        val blob = creds.ntResponse.copyOfRange(16, creds.ntResponse.size)
        if (!blob.copyOfRange(0, 8).contentEquals(NTLMV2_HEADER)) {
            throw AuthFailure(0xC000006D.toInt(), "Only NTLMv2 is supported")
        }
        val ntowf = Smb2Crypto.ntowfv2(expectedPassword, creds.user, creds.domain)
        val expectedProof = Smb2Crypto.hmacMd5(ntowf, challenge + blob)
        if (!creds.user.equals(expectedUser, ignoreCase = true) || !MessageDigest.isEqual(expectedProof, proof)) {
            throw AuthFailure(0xC000006D.toInt(), "Invalid credentials")
        }
        val baseKey = Smb2Crypto.hmacMd5(ntowf, proof)
        return if ((creds.flags and NEGOTIATE_KEY_EXCH) != 0 && creds.encryptedSessionKey.size == 16) {
            Smb2Crypto.rc4(baseKey, creds.encryptedSessionKey)
        } else {
            baseKey
        }
    }

    fun verifyMic(exportedKey: ByteArray, negotiate: ByteArray?, challenge: ByteArray?, creds: Credentials): Boolean {
        val mic = creds.mic ?: return true
        if (negotiate == null || challenge == null) return false
        val authZero = creds.raw.copyOf()
        for (i in 72 until 88) authZero[i] = 0
        val expected = Smb2Crypto.hmacMd5(exportedKey, negotiate + challenge + authZero)
        return MessageDigest.isEqual(expected, mic)
    }
}

class SrvsvcPipe(private val shares: List<ShareDesc>) {
    data class ShareDesc(val name: String, val type: Int, val remark: String)

    companion object {
        private val DREP_LE = byteArrayOf(0x10, 0x00, 0x00, 0x00)
        private val SRVSVC_SYNTAX = byteArrayOf(
            0xC8.toByte(), 0x4F, 0x32, 0x4B, 0x70, 0x16, 0xD3.toByte(), 0x01,
            0x12, 0x78, 0x5A, 0x47, 0xBF.toByte(), 0x6E, 0xE1.toByte(), 0x88.toByte(),
            3, 0, 0, 0
        )
        private val NDR32_SYNTAX = byteArrayOf(
            0x04, 0x5D, 0x88.toByte(), 0x8A.toByte(), 0xEB.toByte(), 0x1C, 0xC9.toByte(), 0x11,
            0x9F.toByte(), 0xE8.toByte(), 0x08, 0x00, 0x2B, 0x10, 0x48, 0x60,
            2, 0, 0, 0
        )
    }

    private var bound = false
    private var contextId = 0
    private var maxFrag = 4280
    var outbox = ByteArray(0)

    fun write(data: ByteArray): Int {
        val resp = transact(data)
        outbox = outbox + resp
        return data.size
    }

    fun read(length: Int): Pair<ByteArray, Boolean> {
        val take = minOf(length, outbox.size)
        val res = outbox.copyOf(take)
        outbox = outbox.copyOfRange(take, outbox.size)
        return res to outbox.isNotEmpty()
    }

    fun transact(data: ByteArray): ByteArray {
        if (data.size < 16) throw IllegalArgumentException("Short RPC PDU")
        val bb = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
        val ptype = data[2].toInt() and 0xFF
        val flags = data[3].toInt() and 0xFF
        val fragLen = bb.getShort(8).toInt() and 0xFFFF
        val authLen = bb.getShort(10).toInt() and 0xFFFF
        val callId = bb.getInt(12)
        val bodyEnd = if (authLen > 0) fragLen - authLen else fragLen
        val body = data.copyOfRange(16, minOf(data.size, bodyEnd))
        return when (ptype) {
            11, 14 -> handleBind(ptype, callId, body)
            0 -> {
                if ((flags and 0x03) != 0x03) return fault(callId, 0x1C000009)
                handleRequest(callId, body)
            }
            else -> fault(callId, 0x1C01000B)
        }
    }

    private fun header(ptype: Int, flags: Int, callId: Int, bodyLen: Int): ByteArray {
        val out = ByteArray(16)
        out[0] = 5; out[1] = 0; out[2] = ptype.toByte(); out[3] = flags.toByte()
        System.arraycopy(DREP_LE, 0, out, 4, 4)
        ByteBuffer.wrap(out, 8, 8).order(ByteOrder.LITTLE_ENDIAN)
            .putShort((16 + bodyLen).toShort()).putShort(0).putInt(callId)
        return out
    }

    private fun pad4(b: ByteArray): ByteArray {
        val pad = (4 - b.size % 4) % 4
        return if (pad == 0) b else b + ByteArray(pad)
    }

    private fun ndrString(text: String): ByteArray {
        val raw = (text + "\u0000").toByteArray(Charsets.UTF_16LE)
        val count = raw.size / 2
        val hdr = ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN).putInt(count).putInt(0).putInt(count).array()
        return pad4(hdr + raw)
    }

    private fun fault(callId: Int, status: Int): ByteArray {
        val body = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(32).putShort(contextId.toShort()).put(0).put(0).putInt(status).putInt(0).array()
        return header(3, 0x23, callId, body.size) + body
    }

    private fun handleBind(ptype: Int, callId: Int, body: ByteArray): ByteArray {
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val maxRecv = bb.getShort(2).toInt() and 0xFFFF
        val assoc = bb.getInt(4)
        val nCtx = body[8].toInt() and 0xFF
        var at = 12
        var results = ByteArray(0)
        var acceptedAny = false
        for (c in 0 until nCtx) {
            if (at + 24 > body.size) break
            val ctxId = bb.getShort(at).toInt() and 0xFFFF
            val nSyn = body[at + 2].toInt() and 0xFF
            val abs = body.copyOfRange(at + 4, at + 24)
            var matched = false
            for (s in 0 until nSyn) {
                val syn = body.copyOfRange(at + 24 + 20 * s, at + 44 + 20 * s)
                if (abs.contentEquals(SRVSVC_SYNTAX) && syn.contentEquals(NDR32_SYNTAX) && !acceptedAny) {
                    matched = true
                    acceptedAny = true
                    bound = true
                    contextId = ctxId
                    results += ByteArray(4) + NDR32_SYNTAX
                    break
                }
            }
            if (!matched) {
                results += byteArrayOf(2, 0, 2, 0) + ByteArray(20)
            }
            at += 24 + 20 * nSyn
        }
        maxFrag = maxOf(1024, minOf(4280, if (maxRecv == 0) 4280 else maxRecv))
        val addrStr = if (ptype == 11) "\\PIPE\\srvsvc\u0000".toByteArray(Charsets.US_ASCII) else ByteArray(0)
        val prefix = ByteBuffer.allocate(10 + addrStr.size).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(maxFrag.toShort()).putShort(maxFrag.toShort())
            .putInt(if (assoc != 0) assoc else 0x53F0)
            .putShort(addrStr.size.toShort()).put(addrStr).array()
        val padded = pad4(prefix) + byteArrayOf(nCtx.toByte(), 0, 0, 0) + results
        return header(if (ptype == 11) 12 else 15, 0x03, callId, padded.size) + padded
    }

    private fun handleRequest(callId: Int, body: ByteArray): ByteArray {
        val bb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val ctxId = bb.getShort(4).toInt() and 0xFFFF
        val opnum = bb.getShort(6).toInt() and 0xFFFF
        if (!bound || ctxId != contextId) return fault(callId, 0x1C01000B)
        if (opnum != 15) return fault(callId, 0x1C010002)

        val stub = body.copyOfRange(8, body.size)
        val r = ByteBuffer.wrap(stub).order(ByteOrder.LITTLE_ENDIAN)
        var off = 0
        if (r.getInt(off) != 0) {
            off += 4
            val actual = r.getInt(off + 8)
            off += 12 + actual * 2
            off = (off + 3) and 3.inv()
        } else {
            off += 4
        }
        val level = r.getInt(off)
        if (level !in intArrayOf(0, 1, 2)) {
            val err = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(level).putInt(level).putInt(0).putInt(0).putInt(0).putInt(124).array()
            return response(callId, err)
        }
        var ref = 0x00020000
        fun ptr(): ByteArray {
            ref += 4
            return ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(ref).array()
        }
        var fixed = ByteArray(0)
        var deferred = ByteArray(0)
        for (sh in shares) {
            fixed += ptr()
            deferred += ndrString(sh.name)
            if (level >= 1) {
                fixed += ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(sh.type).array() + ptr()
                deferred += ndrString(sh.remark)
            }
            if (level == 2) {
                fixed += ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN).putInt(0).putInt(-1).putInt(0).array()
                fixed += ptr()
                deferred += ndrString("")
                fixed += ByteArray(4)
            }
        }
        var respStub = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putInt(level).putInt(level).array()
        respStub += ptr()
        respStub += ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(shares.size).array()
        respStub += ptr() + ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(shares.size).array() + fixed + deferred
        respStub += ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(shares.size).array()
        respStub += ptr() + ByteArray(8)
        return response(callId, respStub)
    }

    private fun response(callId: Int, stub: ByteArray): ByteArray {
        val room = maxFrag - 24
        var out = ByteArray(0)
        var at = 0
        var first = true
        while (true) {
            val take = minOf(room, stub.size - at)
            val chunk = stub.copyOfRange(at, at + take)
            at += take
            val last = at >= stub.size
            val flags = (if (first) 1 else 0) or (if (last) 2 else 0)
            val pHdr = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(stub.size - at + take).putShort(contextId.toShort()).put(0).put(0).array()
            val body = pHdr + chunk
            out += header(2, flags, callId, body.size) + body
            first = false
            if (last) return out
        }
    }
}
