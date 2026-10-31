"""Resource limits for opt-in sparse metadata inspection of untrusted images.

Serving retains its existing format compatibility. Inspection uses compact
indexes, bounded input/output, and a small cache rather than trusting headers.
"""
from array import array
import os
import struct
import sys
import zlib

MAX_DISC = 16 * 1024**3
MAX_BLOCK = 16 * 1024**2
MAX_TABLE = 32 * 1024**2
MAX_DECODE = 64 * 1024**2


class InspectionLimit(ValueError):
    pass


def indexed_header(reader, header, *, zso=False):
    magic, header_size, size, block, version, align, _ = struct.unpack('<4sIQIBBH', header)
    if not 17 * 2048 <= size <= MAX_DISC or not 2048 <= block <= MAX_BLOCK:
        raise InspectionLimit('Compressed disc/block size exceeds inspection limits.')
    if block % 2048 or version != 1:
        raise InspectionLimit('Only sector-aligned version 1 compressed images are inspected.')
    if align > 6:
        raise InspectionLimit('Compressed block alignment exceeds inspection limits.')
    original = magic == b'ZSO\0'
    if not zso:
        if header_size not in (0, 24):
            raise InspectionLimit('Unsupported CSO header size.')
        header_size = 24
    if not 24 <= header_size <= 1024**2:
        raise ValueError('Invalid compressed index location.')
    reader._physical_size = os.fstat(reader.file.fileno()).st_size
    reader.uncompressed_size, reader.block_size = size, block
    reader.align = 0 if original else align
    reader._num_blocks = (size + block - 1) // block
    count = reader._num_blocks + (not original)
    if count * 4 > MAX_TABLE:
        raise InspectionLimit('Compressed index exceeds inspection limits.')
    reader._table_end = header_size + count * 4
    if reader._table_end > reader._physical_size:
        raise ValueError('Truncated compressed index.')
    reader.file.seek(header_size)
    data = reader.file.read(count * 4)
    if len(data) != count * 4:
        raise ValueError('Truncated compressed index.')
    reader.block_offsets = array('I')
    reader.block_offsets.frombytes(data)
    if reader.block_offsets.itemsize != 4:
        raise InspectionLimit('This platform has no 32-bit array index representation.')
    if sys.byteorder != 'little':
        reader.block_offsets.byteswap()


def indexed_block(reader, index, *, lz4_block=None):
    if not 0 <= index < reader._num_blocks:
        raise ValueError('Compressed block index is out of bounds.')
    raw = reader.block_offsets[index]
    start = (raw & 0x7fffffff) << reader.align
    end = ((reader.block_offsets[index + 1] & 0x7fffffff) << reader.align
           if index + 1 < len(reader.block_offsets) else reader._physical_size)
    if not reader._table_end <= start < end <= reader._physical_size:
        raise ValueError('Compressed block offsets are invalid or truncated.')
    expected = min(reader.block_size, reader.uncompressed_size - index * reader.block_size)
    if end - start > reader.block_size + 65536:
        raise InspectionLimit('Compressed block input exceeds inspection limits.')
    length = expected if raw & 0x80000000 else end - start
    if length > end - start:
        raise ValueError('Truncated raw compressed-image block.')
    reader.file.seek(start)
    data = reader.file.read(length)
    if len(data) != length:
        raise ValueError('Truncated compressed-image block.')
    if raw & 0x80000000:
        return data
    if lz4_block is not None:
        # Alignment permits at most 63 padding bytes. Never retry once per
        # byte of an attacker-controlled, potentially megabyte-sized span.
        for padding in range(min((1 << reader.align) - 1, len(data) - 1) + 1):
            try:
                result = lz4_block.decompress(data[:len(data) - padding],
                                              uncompressed_size=reader.block_size)
                if expected <= len(result) <= reader.block_size:
                    return result
            except lz4_block.LZ4BlockError:
                pass
        raise ValueError('Invalid LZ4 block or output length.')
    for window in (-zlib.MAX_WBITS, zlib.MAX_WBITS):
        try:
            decoder = zlib.decompressobj(window)
            result = decoder.decompress(data, reader.block_size + 1)
            if decoder.eof and expected <= len(result) <= reader.block_size:
                return result
        except zlib.error:
            pass
    raise ValueError('Invalid deflate block or output exceeds its declared size.')


def chd_header(header):
    if len(header) < 124 or header[:8] != b'MComprHD':
        raise ValueError('Truncated or invalid CHD header.')
    length, version = struct.unpack_from('>II', header, 8)
    logical = struct.unpack_from('>Q', header, 32)[0]
    hunk, unit = struct.unpack_from('>II', header, 56)
    if length != 124 or version != 5:
        raise InspectionLimit('Only CHD v5 is inspected.')
    if (not 17 * 2048 <= logical <= MAX_DISC or not 2048 <= hunk <= MAX_BLOCK
            or unit not in (2048, 2448) or hunk % unit):
        raise InspectionLimit('CHD geometry exceeds inspection limits.')
    # libchdr allocates a native map per hunk in addition to its decode buffer.
    if (logical + hunk - 1) // hunk > 1024**2:
        raise InspectionLimit('CHD hunk map exceeds inspection limits.')
