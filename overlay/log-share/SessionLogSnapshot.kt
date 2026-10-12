package com.droiddeck.launcher.session

import com.droiddeck.launcher.core.LogRedactor
import java.io.File
import java.io.FileInputStream
import java.io.FilterInputStream
import java.io.IOException
import java.io.InputStream

/** A per-file, bounded prefix snapshot. Source files and their writers are never modified. */
internal object SessionLogSnapshot {
    private const val MAX_LINE_CHARS = 64 * 1024

    private class Prefix(input: InputStream, private var remaining: Long) : FilterInputStream(input) {
        override fun read(): Int {
            if (remaining == 0L) return -1
            val value = super.read()
            if (value >= 0) remaining--
            return value
        }
        override fun read(bytes: ByteArray, offset: Int, length: Int): Int {
            if (length == 0) return 0
            if (remaining == 0L) return -1
            val count = `in`.read(bytes, offset, minOf(length.toLong(), remaining).toInt())
            if (count > 0) remaining -= count
            return count
        }
    }

    /** Open before measuring: rename by a collector cannot change the inode being read. */
    fun redact(source: File, destination: File, redactLine: (String) -> String = LogRedactor::redact) {
        FileInputStream(source).use { input ->
            val prefix = Prefix(input, input.channel.size()).buffered()
            prefix.mark(4096)
            val probe = ByteArray(4096)
            val count = prefix.read(probe)
            prefix.reset()
            val text = count <= 0 || (0 until count).none { probe[it] == 0.toByte() }
            if (!text) {
                destination.outputStream().buffered().use { prefix.copyTo(it) }
                return
            }
            destination.bufferedWriter().use { out ->
                prefix.reader(Charsets.UTF_8).buffered().use { reader ->
                    val line = StringBuilder()
                    fun emit() {
                        out.write(redactLine(line.toString().removeSuffix("\r")))
                        out.write("\n")
                        line.setLength(0)
                    }
                    while (true) {
                        val ch = reader.read()
                        if (ch < 0) break
                        if (ch == '\n'.code) emit() else {
                            if (line.length == MAX_LINE_CHARS) throw IOException("log line exceeds redaction limit")
                            line.append(ch.toChar())
                        }
                    }
                    // A JSONL append may be halfway through its last record at snapshot time.
                    if (line.isNotEmpty() && source.extension != "jsonl") emit()
                }
            }
        }
    }
}
