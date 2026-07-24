package logging

import (
	"encoding/json"
	"fmt"
	"io"
	"sync"
	"time"
)

type Logger struct {
	mu      sync.Mutex
	out     io.Writer
	JSON    bool
	Quiet   bool
	Verbose bool
}

func New(out io.Writer, format string, quiet, verbose bool) *Logger {
	return &Logger{out: out, JSON: format == "json", Quiet: quiet, Verbose: verbose}
}
func (l *Logger) emit(level, message string, fields map[string]any) {
	if l.Quiet && level == "info" {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.JSON {
		row := map[string]any{"time": time.Now().UTC().Format(time.RFC3339Nano), "level": level, "message": message}
		for k, v := range fields {
			row[k] = v
		}
		b, _ := json.Marshal(row)
		fmt.Fprintln(l.out, string(b))
		return
	}
	if len(fields) == 0 {
		fmt.Fprintf(l.out, "[%s] %s\n", level, message)
		return
	}
	fmt.Fprintf(l.out, "[%s] %s", level, message)
	for k, v := range fields {
		fmt.Fprintf(l.out, " %s=%v", k, v)
	}
	fmt.Fprintln(l.out)
}
func (l *Logger) Info(m string, f map[string]any)  { l.emit("info", m, f) }
func (l *Logger) Warn(m string, f map[string]any)  { l.emit("warn", m, f) }
func (l *Logger) Error(m string, f map[string]any) { l.emit("error", m, f) }
func (l *Logger) Debug(m string, f map[string]any) {
	if l.Verbose {
		l.emit("debug", m, f)
	}
}
