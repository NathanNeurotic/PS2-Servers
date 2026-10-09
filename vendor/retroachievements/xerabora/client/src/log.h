/*
  Timestamped console logging. Everything goes to stdout so that a
  redirect captures the whole session.
*/
#ifndef XERABORA_LOG_H
#define XERABORA_LOG_H

/* Also write everything to this file, appending. The window hides the
   console, and a program whose console is hidden needs somewhere to
   say what went wrong. */
void log_to_file(const char *path);

void log_set_trace(int on);
int log_trace_enabled(void);

/* Into the log file only. For the detail behind a line the startup
   summary already says in plain words: the file keeps everything, the
   terminal stays readable. */
void log_detail(const char *fmt, ...);

void log_info(const char *fmt, ...);
void log_warn(const char *fmt, ...);
void log_error(const char *fmt, ...);
void log_trace(const char *fmt, ...); /* only with --trace */

#endif
