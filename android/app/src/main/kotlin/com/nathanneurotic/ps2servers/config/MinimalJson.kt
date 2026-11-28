package com.nathanneurotic.ps2servers.config

/**
 * Dependency-free RFC 8259 JSON parser and formatter.
 *
 * Keeps configuration serialization identical across Android API 21+ and host
 * JVM unit tests without relying on Android's stubbed org.json in unit tests.
 */
object MinimalJson {
    fun parseObject(text: String): MutableMap<String, Any?> {
        val parser = Parser(text)
        parser.skipWhitespace()
        val value = parser.readValue()
        parser.skipWhitespace()
        if (!parser.isAtEnd()) {
            throw IllegalArgumentException("Unexpected trailing JSON content at index ${parser.pos}")
        }
        @Suppress("UNCHECKED_CAST")
        return value as? MutableMap<String, Any?>
            ?: throw IllegalArgumentException("Root JSON value must be an object")
    }

    fun stringify(value: Any?, indent: Int = 2): String {
        val sb = StringBuilder()
        writeValue(sb, value, indent, 0)
        sb.append('\n')
        return sb.toString()
    }

    private fun writeValue(sb: StringBuilder, value: Any?, indent: Int, depth: Int) {
        when (value) {
            null -> sb.append("null")
            is Boolean -> sb.append(if (value) "true" else "false")
            is Byte, is Short, is Int, is Long -> sb.append(value.toString())
            is Number -> {
                val d = value.toDouble()
                if (d.isNaN() || d.isInfinite()) {
                    sb.append("null")
                } else {
                    val asLong = d.toLong()
                    if (asLong.toDouble() == d) sb.append(asLong) else sb.append(d)
                }
            }
            is String -> writeString(sb, value)
            is Map<*, *> -> {
                if (value.isEmpty()) {
                    sb.append("{}")
                    return
                }
                sb.append("{\n")
                val entries = value.entries.toList()
                for (i in entries.indices) {
                    val entry = entries[i]
                    appendIndent(sb, indent * (depth + 1))
                    writeString(sb, entry.key?.toString() ?: "null")
                    sb.append(": ")
                    writeValue(sb, entry.value, indent, depth + 1)
                    if (i + 1 < entries.size) sb.append(',')
                    sb.append('\n')
                }
                appendIndent(sb, indent * depth)
                sb.append('}')
            }
            is Iterable<*> -> {
                val list = value.toList()
                if (list.isEmpty()) {
                    sb.append("[]")
                    return
                }
                sb.append("[\n")
                for (i in list.indices) {
                    appendIndent(sb, indent * (depth + 1))
                    writeValue(sb, list[i], indent, depth + 1)
                    if (i + 1 < list.size) sb.append(',')
                    sb.append('\n')
                }
                appendIndent(sb, indent * depth)
                sb.append(']')
            }
            else -> writeString(sb, value.toString())
        }
    }

    private fun appendIndent(sb: StringBuilder, spaces: Int) {
        for (i in 0 until spaces) sb.append(' ')
    }

    private fun writeString(sb: StringBuilder, s: String) {
        sb.append('"')
        for (ch in s) {
            when (ch) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\b' -> sb.append("\\b")
                '\u000C' -> sb.append("\\f")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> if (ch.code < 0x20) {
                    sb.append(String.format("\\u%04x", ch.code))
                } else {
                    sb.append(ch)
                }
            }
        }
        sb.append('"')
    }

    private class Parser(private val text: String) {
        var pos = 0

        fun isAtEnd(): Boolean = pos >= text.length

        fun skipWhitespace() {
            while (pos < text.length && text[pos] in charArrayOf(' ', '\t', '\r', '\n')) {
                pos++
            }
        }

        fun readValue(): Any? {
            skipWhitespace()
            if (pos >= text.length) throw IllegalArgumentException("Unexpected end of JSON")
            return when (val ch = text[pos]) {
                '{' -> readObject()
                '[' -> readArray()
                '"' -> readString()
                't', 'f' -> readBoolean()
                'n' -> readNull()
                '-', in '0'..'9' -> readNumber()
                else -> throw IllegalArgumentException("Unexpected JSON char '$ch' at $pos")
            }
        }

        private fun readObject(): MutableMap<String, Any?> {
            pos++ // '{'
            val map = LinkedHashMap<String, Any?>()
            skipWhitespace()
            if (pos < text.length && text[pos] == '}') {
                pos++
                return map
            }
            while (true) {
                skipWhitespace()
                if (pos >= text.length || text[pos] != '"') {
                    throw IllegalArgumentException("Expected object key at $pos")
                }
                val key = readString()
                skipWhitespace()
                if (pos >= text.length || text[pos] != ':') {
                    throw IllegalArgumentException("Expected ':' at $pos")
                }
                pos++
                val value = readValue()
                map[key] = value
                skipWhitespace()
                if (pos >= text.length) throw IllegalArgumentException("Unterminated object")
                when (text[pos]) {
                    ',' -> pos++
                    '}' -> {
                        pos++
                        return map
                    }
                    else -> throw IllegalArgumentException("Expected ',' or '}' at $pos")
                }
            }
        }

        private fun readArray(): MutableList<Any?> {
            pos++ // '['
            val list = ArrayList<Any?>()
            skipWhitespace()
            if (pos < text.length && text[pos] == ']') {
                pos++
                return list
            }
            while (true) {
                list.add(readValue())
                skipWhitespace()
                if (pos >= text.length) throw IllegalArgumentException("Unterminated array")
                when (text[pos]) {
                    ',' -> pos++
                    ']' -> {
                        pos++
                        return list
                    }
                    else -> throw IllegalArgumentException("Expected ',' or ']' at $pos")
                }
            }
        }

        private fun readString(): String {
            pos++ // '"'
            val sb = StringBuilder()
            while (pos < text.length) {
                val ch = text[pos++]
                when (ch) {
                    '"' -> return sb.toString()
                    '\\' -> {
                        if (pos >= text.length) throw IllegalArgumentException("Unterminated escape")
                        when (val esc = text[pos++]) {
                            '"' -> sb.append('"')
                            '\\' -> sb.append('\\')
                            '/' -> sb.append('/')
                            'b' -> sb.append('\b')
                            'f' -> sb.append('\u000C')
                            'n' -> sb.append('\n')
                            'r' -> sb.append('\r')
                            't' -> sb.append('\t')
                            'u' -> {
                                if (pos + 4 > text.length) {
                                    throw IllegalArgumentException("Short unicode escape at $pos")
                                }
                                val hex = text.substring(pos, pos + 4)
                                pos += 4
                                sb.append(hex.toInt(16).toChar())
                            }
                            else -> throw IllegalArgumentException("Bad JSON escape '\\$esc'")
                        }
                    }
                    else -> sb.append(ch)
                }
            }
            throw IllegalArgumentException("Unterminated JSON string")
        }

        private fun readBoolean(): Boolean {
            return if (text.startsWith("true", pos)) {
                pos += 4
                true
            } else if (text.startsWith("false", pos)) {
                pos += 5
                false
            } else {
                throw IllegalArgumentException("Invalid boolean at $pos")
            }
        }

        private fun readNull(): Any? {
            if (text.startsWith("null", pos)) {
                pos += 4
                return null
            }
            throw IllegalArgumentException("Invalid null at $pos")
        }

        private fun readNumber(): Number {
            val start = pos
            if (text[pos] == '-') pos++
            while (pos < text.length && text[pos] in '0'..'9') pos++
            var isFloat = false
            if (pos < text.length && text[pos] == '.') {
                isFloat = true
                pos++
                while (pos < text.length && text[pos] in '0'..'9') pos++
            }
            if (pos < text.length && (text[pos] == 'e' || text[pos] == 'E')) {
                isFloat = true
                pos++
                if (pos < text.length && (text[pos] == '+' || text[pos] == '-')) pos++
                while (pos < text.length && text[pos] in '0'..'9') pos++
            }
            val raw = text.substring(start, pos)
            return if (isFloat) raw.toDouble() else raw.toLong()
        }
    }
}
