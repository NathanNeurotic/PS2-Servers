package webui

import (
	"bytes"
	"sync"
)

type LogBuffer struct {
	mu          sync.RWMutex
	lines       []string
	capacity    int
	head        int
	count       int
	subscribers map[chan string]struct{}
}

func NewLogBuffer(capacity int) *LogBuffer {
	if capacity <= 0 {
		capacity = 100
	}
	return &LogBuffer{
		lines:       make([]string, capacity),
		capacity:    capacity,
		subscribers: make(map[chan string]struct{}),
	}
}

func (b *LogBuffer) Write(p []byte) (n int, err error) {
	lines := bytes.Split(p, []byte{'\n'})

	b.mu.Lock()
	defer b.mu.Unlock()

	for i, line := range lines {
		// Ignore the last empty string if p ends with '\n'
		if i == len(lines)-1 && len(line) == 0 {
			continue
		}
		s := string(line)
		b.lines[b.head] = s
		b.head = (b.head + 1) % b.capacity
		if b.count < b.capacity {
			b.count++
		}

		for sub := range b.subscribers {
			select {
			case sub <- s:
			default:
				// Drop subscriber if it falls behind without panic/double-close
				delete(b.subscribers, sub)
				close(sub)
			}
		}
	}
	return len(p), nil
}

func (b *LogBuffer) Lines() []string {
	b.mu.RLock()
	defer b.mu.RUnlock()

	res := make([]string, 0, b.count)
	if b.count == 0 {
		return res
	}

	start := 0
	if b.count == b.capacity {
		start = b.head
	}

	for i := 0; i < b.count; i++ {
		idx := (start + i) % b.capacity
		res = append(res, b.lines[idx])
	}
	return res
}

func (b *LogBuffer) Subscribe() (<-chan string, func()) {
	ch := make(chan string, 100)

	b.mu.Lock()
	b.subscribers[ch] = struct{}{}
	b.mu.Unlock()

	unsub := func() {
		b.mu.Lock()
		defer b.mu.Unlock()
		if _, ok := b.subscribers[ch]; ok {
			delete(b.subscribers, ch)
			close(ch)
		}
	}

	return ch, unsub
}
