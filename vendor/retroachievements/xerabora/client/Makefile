# xerabora PC client.
#
#   make            native build (Linux, macOS, MSYS2): needs libcurl
#   make windows    cross build for Windows with MinGW-w64: no external
#                   libraries, HTTPS goes through WinHTTP
#   make macos      one binary for both Mac architectures; libcurl comes
#                   with the system
#
# rcheevos is compiled from the submodule, unmodified.
# Notification sounds: tools/make-sounds.py regenerates sounds/*.wav and
# src/sounds_data.c.

RC      := ../third_party/rcheevos
PROTO   := ../protocol
# Version: the v* tag of the checked-out commit, else the default in
# src/version.h. Override with VERSION=1.2.3.
VERSION ?= $(shell git describe --tags --exact-match --match 'v*' 2>/dev/null | sed 's/^v//')

CFLAGS  ?= -O2
CFLAGS  += -Wall -Wextra -std=gnu99 \
           -I$(RC)/include -I$(RC)/src -I$(RC)/src/rcheevos -I$(RC)/src/rapi -I$(PROTO)
ifneq ($(VERSION),)
CFLAGS  += -DXERABORA_VERSION=\"$(VERSION)\"
endif

HDRS := $(wildcard src/*.h) $(wildcard $(PROTO)/*.h)

RC_SRC := \
	$(RC)/src/rcheevos/alloc.c \
	$(RC)/src/rcheevos/condition.c \
	$(RC)/src/rcheevos/condset.c \
	$(RC)/src/rcheevos/consoleinfo.c \
	$(RC)/src/rcheevos/format.c \
	$(RC)/src/rcheevos/lboard.c \
	$(RC)/src/rcheevos/memref.c \
	$(RC)/src/rcheevos/operand.c \
	$(RC)/src/rcheevos/richpresence.c \
	$(RC)/src/rcheevos/runtime.c \
	$(RC)/src/rcheevos/runtime_progress.c \
	$(RC)/src/rcheevos/trigger.c \
	$(RC)/src/rcheevos/value.c \
	$(RC)/src/rapi/rc_api_common.c \
	$(RC)/src/rapi/rc_api_editor.c \
	$(RC)/src/rapi/rc_api_info.c \
	$(RC)/src/rapi/rc_api_runtime.c \
	$(RC)/src/rapi/rc_api_user.c \
	$(RC)/src/rhash/md5.c \
	$(RC)/src/rc_client.c \
	$(RC)/src/rc_compat.c \
	$(RC)/src/rc_util.c \
	$(RC)/src/rc_version.c

SRC := src/main.c src/badge_data.c src/config.c src/console.c src/discord.c src/follow.c src/log.c \
       src/platform.c src/ra.c src/raweb.c src/snapshot.c src/sound.c src/sounds_data.c \
       src/ui_page.c src/watchlist.c src/webui.c

all: xerabora

xerabora: $(SRC) src/http_curl.c $(RC_SRC) $(HDRS)
	$(CC) $(CFLAGS) -o $@ $(filter %.c,$^) -lcurl -lm

# ---- The window build ---------------------------------------------------
# raylib is not vendored: tools/build-raylib.sh fetches a pinned release
# and leaves a static library in third_party/raylib-build/<target>/.
# Both window targets link that. Without it, the plain targets above
# still build the terminal client, which is the one the hardware work
# uses.
RAYLIB_WIN := ../third_party/raylib-build/windows
RAYLIB_LIN := ../third_party/raylib-build/linux

GUI_SRC := $(SRC) src/ui.c

windows-gui: $(GUI_SRC) src/http_winhttp.c $(RC_SRC) $(HDRS)
	$(WINCC) $(CFLAGS) -DXERABORA_GUI -I$(RAYLIB_WIN) -D_WIN32_WINNT=0x0601 -static \
		-o xerabora.exe $(filter %.c,$^) $(RAYLIB_WIN)/libraylib.a \
		-lws2_32 -lwinhttp -lwinmm -lshell32 -lgdi32 -lopengl32 -lm -lpthread

linux-gui: $(GUI_SRC) src/http_curl.c $(RC_SRC) $(HDRS)
	$(CC) $(CFLAGS) -DXERABORA_GUI -I$(RAYLIB_LIN) -o xerabora $(filter %.c,$^) \
		$(RAYLIB_LIN)/libraylib.a -lcurl -lm -lpthread -ldl -lX11

WINCC ?= x86_64-w64-mingw32-gcc
WINDRES ?= x86_64-w64-mingw32-windres

windows: xerabora.exe

# The icon and the file properties Explorer shows.
xerabora_res.o: xerabora.rc xerabora.ico
	$(WINDRES) -O coff -o $@ $<

# -mwindows: a double click opens the page and no console window; the
# program attaches to a terminal when started from one, and --console
# opens a console on demand.
xerabora.exe: $(SRC) src/http_winhttp.c $(RC_SRC) $(HDRS) xerabora_res.o
	$(WINCC) $(CFLAGS) -D_WIN32_WINNT=0x0601 -static -mwindows -o $@ $(filter %.c,$^) xerabora_res.o -lws2_32 -lwinhttp -lwinmm -lshell32 -lm -lpthread

# ---- macOS --------------------------------------------------------------
# Apple Silicon and Intel in one file: the SDK stubs carry both
# architectures, so an Apple Silicon machine builds the Intel half too.

macos: xerabora-macos

xerabora-macos: $(SRC) src/http_curl.c $(RC_SRC) $(HDRS)
	$(CC) $(CFLAGS) -arch arm64 -arch x86_64 -o $@ $(filter %.c,$^) -lcurl -lm

clean:
	rm -f xerabora xerabora.exe xerabora_res.o xerabora-macos

.PHONY: all windows macos clean
