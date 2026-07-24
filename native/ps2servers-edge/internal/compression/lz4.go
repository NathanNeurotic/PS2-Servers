package compression

import "fmt"

// decodeLZ4Block decodes the raw LZ4 block format used by ZSO images.
// It is deliberately bounded by dstSize: malformed input cannot grow memory.
func decodeLZ4Block(src []byte, dstSize int) ([]byte, error) {
	if dstSize < 0 {
		return nil, fmt.Errorf("negative output size")
	}
	dst := make([]byte, 0, dstSize)
	i := 0
	readLen := func(base int) (int, error) {
		n := base
		if base != 15 {
			return n, nil
		}
		for {
			if i >= len(src) {
				return 0, fmt.Errorf("truncated length")
			}
			v := int(src[i])
			i++
			n += v
			if v != 255 {
				return n, nil
			}
		}
	}
	for i < len(src) {
		token := int(src[i])
		i++
		lit, err := readLen(token >> 4)
		if err != nil {
			return nil, err
		}
		if lit > len(src)-i || lit > dstSize-len(dst) {
			return nil, fmt.Errorf("invalid literal length")
		}
		dst = append(dst, src[i:i+lit]...)
		i += lit
		if i == len(src) {
			break
		}
		if i+2 > len(src) {
			return nil, fmt.Errorf("truncated match offset")
		}
		off := int(src[i]) | int(src[i+1])<<8
		i += 2
		if off <= 0 || off > len(dst) {
			return nil, fmt.Errorf("invalid match offset")
		}
		match, err := readLen(token & 0xF)
		if err != nil {
			return nil, err
		}
		match += 4
		if match > dstSize-len(dst) {
			return nil, fmt.Errorf("match exceeds output")
		}
		start := len(dst) - off
		for j := 0; j < match; j++ {
			dst = append(dst, dst[start+j])
		}
	}
	if len(dst) != dstSize {
		return nil, fmt.Errorf("decoded %d bytes, expected %d", len(dst), dstSize)
	}
	return dst, nil
}
