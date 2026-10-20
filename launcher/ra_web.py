"""Native PS2-Servers RetroAchievements HTTP dashboard and live status service.

Serves the standalone RetroAchievements status interface on the engine's loopback
account port, providing /state and /events endpoints without vendoring or running
the legacy xeRAbora desktop client page.
"""
import http.server
import json
import socket
import threading
import time
import urllib.parse


class RAState:
    """Thread-safe status model for the PS2-Servers owned RetroAchievements runtime."""

    def __init__(self, version="PS2-Servers-native"):
        self.version = version
        self._lock = threading.Lock()
        self._dirty = threading.Event()
        self.user = ""
        self.logged_in = False
        self.console_ip = ""
        self.console_connected = False
        self.console_status = "idle"
        self.packets = 0
        self.frames = 0
        self.watch_addresses = 0
        self.started_at = 0.0
        self.game_id = 0
        self.game_title = ""
        self.game_serial = ""
        self.game_hash = ""
        self.achievements = []
        self.unlocks = []

    def set_account(self, username, logged_in):
        with self._lock:
            self.user = str(username) if username else ""
            self.logged_in = bool(logged_in)
            self._dirty.set()

    def logout(self):
        with self._lock:
            self.user, self.logged_in = "", False
            self.game_id, self.game_title = 0, ""
            self.game_serial, self.game_hash = "", ""
            self.achievements, self.unlocks = [], []
            self.console_status = "signed out"
            self._dirty.set()

    def update_console(self, connected, ip=None, status=None, packets=None, frames=None, watch_addresses=None):
        with self._lock:
            if connected and not self.console_connected:
                self.started_at = time.time()
            elif not connected and self.console_connected:
                self.started_at = 0.0
            self.console_connected = bool(connected)
            if ip is not None:
                self.console_ip = str(ip)
            if status is not None:
                self.console_status = str(status)
            if packets is not None:
                self.packets = int(packets)
            if frames is not None:
                self.frames = int(frames)
            if watch_addresses is not None:
                self.watch_addresses = int(watch_addresses)
            self._dirty.set()

    def set_game(self, game_id, title, serial="", image_hash="", achievements=None):
        with self._lock:
            self.game_id = int(game_id)
            self.game_title = str(title) if title else ""
            self.game_serial = str(serial) if serial else ""
            self.game_hash = str(image_hash) if image_hash else ""
            if achievements is not None:
                self.achievements = list(achievements)
            self._dirty.set()

    def note_unlock(self, achievement_id, title="", points=0, badge=""):
        with self._lock:
            aid = int(achievement_id)
            now = int(time.time())
            for item in self.achievements:
                if item.get("id") == aid:
                    item["unlocked"] = True
                    if not title and item.get("title"):
                        title = item["title"]
                    if not points and item.get("points"):
                        points = item["points"]
                    break
            unlock_entry = {
                "id": aid,
                "title": str(title) if title else f"Achievement {aid}",
                "badge": str(badge) if badge else "",
                "points": int(points),
                "at": now,
            }
            self.unlocks.insert(0, unlock_entry)
            if len(self.unlocks) > 16:
                self.unlocks.pop()
            self._dirty.set()

    def to_dict(self):
        with self._lock:
            seconds = int(time.time() - self.started_at) if self.console_connected and self.started_at > 0 else 0
            return {
                "version": self.version,
                "login": {
                    "ok": self.logged_in,
                    "webapi": self.logged_in,
                    "user": self.user,
                },
                "console": {
                    "connected": self.console_connected,
                    "ip": self.console_ip,
                    "status": self.console_status,
                    "packets": self.packets,
                    "frames": self.frames,
                    "gaps": 0,
                    "dupes": 0,
                    "torn": 0,
                    "watch": {
                        "parts": 1 if self.watch_addresses > 0 else 0,
                        "bytes": self.watch_addresses * 4,
                        "addresses": self.watch_addresses,
                    },
                    "seconds": seconds,
                },
                "game": {
                    "id": self.game_id,
                    "title": self.game_title,
                    "serial": self.game_serial,
                    "hash": self.game_hash,
                    "achievements": [dict(a) for a in self.achievements],
                },
                "unlocks": [dict(u) for u in self.unlocks],
            }

    def wait_dirty(self, timeout=1.0):
        flag = self._dirty.wait(timeout)
        self._dirty.clear()
        return flag


NATIVE_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PS2-Servers RetroAchievements</title>
<style>
:root {
  --bg: #0d1117;
  --surface: #161b22;
  --surface-border: #30363d;
  --text: #c9d1d9;
  --text-bright: #f0f6fc;
  --text-muted: #8b949e;
  --accent: #238636;
  --accent-blue: #1f6feb;
  --accent-gold: #d29922;
  --accent-red: #da3633;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background-color: var(--bg);
  color: var(--text);
  line-height: 1.5;
  padding: 24px;
}
.container { max-width: 960px; margin: 0 auto; }
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--surface-border);
}
h1 { font-size: 1.5rem; color: var(--text-bright); display: flex; align-items: center; gap: 10px; }
.badge {
  font-size: 0.75rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 12px;
  background-color: var(--accent-blue);
  color: #fff;
}
.badge-softcore { background-color: var(--accent-gold); color: #000; }
.account-badge {
  font-size: 0.9rem;
  padding: 4px 12px;
  border-radius: 6px;
  background-color: var(--surface);
  border: 1px solid var(--surface-border);
  color: var(--text-bright);
}
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }
.card {
  background-color: var(--surface);
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  padding: 16px;
}
.card h2 { font-size: 1rem; color: var(--text-muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
.card-value { font-size: 1.25rem; font-weight: 600; color: var(--text-bright); }
.status-indicator {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 6px;
}
.status-connected { background-color: var(--accent); box-shadow: 0 0 8px var(--accent); }
.status-disconnected { background-color: var(--accent-red); }
.progress-bar-bg {
  background-color: var(--bg);
  height: 8px;
  border-radius: 4px;
  overflow: hidden;
  margin-top: 8px;
}
.progress-bar-fill {
  background-color: var(--accent);
  height: 100%;
  width: 0%;
  transition: width 0.3s ease;
}
.section-title { font-size: 1.2rem; color: var(--text-bright); margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
.achievements-list { display: flex; flex-direction: column; gap: 8px; }
.achievement-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background-color: var(--surface);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
}
.achievement-item.unlocked { border-left: 4px solid var(--accent); }
.achievement-info h3 { font-size: 0.95rem; color: var(--text-bright); }
.achievement-info p { font-size: 0.85rem; color: var(--text-muted); }
.achievement-points {
  font-weight: 600;
  font-size: 0.9rem;
  color: var(--accent-gold);
  white-space: nowrap;
  padding: 2px 8px;
  border-radius: 4px;
  background-color: rgba(210, 153, 34, 0.1);
}
.unlock-banner {
  background-color: rgba(35, 134, 54, 0.15);
  border: 1px solid var(--accent);
  border-radius: 6px;
  padding: 12px 16px;
  margin-bottom: 20px;
  display: none;
}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>
      PS2-Servers RetroAchievements
      <span class="badge">Owned Runtime</span>
      <span class="badge badge-softcore">Softcore Only</span>
    </h1>
    <div id="account-status" class="account-badge">Checking account...</div>
  </header>

  <div id="unlock-banner" class="unlock-banner">
    <strong id="unlock-text">Recent unlock: None</strong>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Console Connection</h2>
      <div class="card-value" id="console-status">
        <span class="status-indicator status-disconnected" id="status-dot"></span>
        <span id="console-text">Searching...</span>
      </div>
      <p id="console-sub" style="font-size: 0.85rem; color: var(--text-muted); margin-top: 6px;">0 packets, 0 frames</p>
    </div>

    <div class="card">
      <h2>Active Game</h2>
      <div class="card-value" id="game-title">No game active</div>
      <p id="game-sub" style="font-size: 0.85rem; color: var(--text-muted); margin-top: 6px;">-</p>
    </div>

    <div class="card">
      <h2>Progress</h2>
      <div class="card-value" id="progress-text">0 / 0 Unlocked</div>
      <div class="progress-bar-bg">
        <div id="progress-fill" class="progress-bar-fill"></div>
      </div>
    </div>
  </div>

  <div>
    <div class="section-title">
      <span>Achievements</span>
      <span id="score-text" style="font-size: 0.95rem; color: var(--accent-gold);">0 Points</span>
    </div>
    <div id="achievements-container" class="achievements-list">
      <div class="achievement-item" style="color: var(--text-muted);">
        Waiting for game identification...
      </div>
    </div>
  </div>
</div>

<script>
function render(state) {
  const accountEl = document.getElementById('account-status');
  if (state.login && state.login.ok) {
    accountEl.textContent = 'Signed in: ' + state.login.user;
  } else {
    accountEl.textContent = 'Signed Out';
  }

  const dot = document.getElementById('status-dot');
  const consoleText = document.getElementById('console-text');
  const consoleSub = document.getElementById('console-sub');
  if (state.console && state.console.connected) {
    dot.className = 'status-indicator status-connected';
    consoleText.textContent = state.console.ip || 'Connected';
    consoleSub.textContent = (state.console.packets || 0) + ' packets, ' + (state.console.frames || 0) + ' frames (' + (state.console.watch ? state.console.watch.addresses : 0) + ' watches)';
  } else {
    dot.className = 'status-indicator status-disconnected';
    consoleText.textContent = 'Searching...';
    consoleSub.textContent = 'Waiting for console telemetry';
  }

  const gameTitle = document.getElementById('game-title');
  const gameSub = document.getElementById('game-sub');
  if (state.game && state.game.title) {
    gameTitle.textContent = state.game.title;
    gameSub.textContent = (state.game.serial || 'No serial') + ' | ' + (state.game.hash ? state.game.hash.substring(0, 8) + '...' : '');
  } else {
    gameTitle.textContent = 'No game active';
    gameSub.textContent = '-';
  }

  const achievements = (state.game && state.game.achievements) || [];
  let unlockedCount = 0;
  let totalScore = 0;
  let earnedScore = 0;

  achievements.forEach(a => {
    totalScore += (a.points || 0);
    if (a.unlocked) {
      unlockedCount++;
      earnedScore += (a.points || 0);
    }
  });

  document.getElementById('progress-text').textContent = unlockedCount + ' / ' + achievements.length + ' Unlocked';
  const pct = achievements.length > 0 ? (unlockedCount / achievements.length * 100) : 0;
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('score-text').textContent = earnedScore + ' / ' + totalScore + ' Points';

  if (state.unlocks && state.unlocks.length > 0) {
    const banner = document.getElementById('unlock-banner');
    const u = state.unlocks[0];
    document.getElementById('unlock-text').textContent = 'Recent unlock: ' + u.title + ' (' + u.points + ' points)';
    banner.style.display = 'block';
  }

  const container = document.getElementById('achievements-container');
  if (achievements.length === 0) {
    container.innerHTML = '<div class="achievement-item" style="color: var(--text-muted);">Waiting for game identification...</div>';
  } else {
    container.innerHTML = achievements.map(a => `
      <div class="achievement-item ${a.unlocked ? 'unlocked' : ''}">
        <div class="achievement-info">
          <h3>${a.unlocked ? '&#x2714; ' : ''}${a.title || 'Achievement ' + a.id}</h3>
          <p>${a.description || ''}</p>
        </div>
        <div class="achievement-points">${a.points || 0} pts</div>
      </div>
    `).join('');
  }
}

function initSSE() {
  const source = new EventSource('/events');
  source.onmessage = function(event) {
    try {
      const state = JSON.parse(event.data);
      render(state);
    } catch (e) {}
  };
  source.onerror = function() {
    source.close();
    setTimeout(initSSE, 3000);
  };
}

fetch('/state').then(r => r.json()).then(render).catch(() => {});
initSSE();
</script>
</body>
</html>
"""


class RAWebServer:
    """Loopback HTTP server for RetroAchievements status and Server-Sent Events."""

    def __init__(self, port, state, host="127.0.0.1", *, logout=None):
        self.port = port
        self.state = state
        self.host = host
        self.stopped = threading.Event()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    pass

            def log_message(self, format, *args):
                pass

            def _send_json(self, data, status=200):
                body = json.dumps(data, indent=2).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                target = urllib.parse.urlsplit(self.path)
                path = target.path
                if path in ("/", "/index.html"):
                    body = NATIVE_UI_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/state":
                    self._send_json(owner.state.to_dict())
                elif path == "/profile":
                    st = owner.state.to_dict()
                    self._send_json(st["login"])
                elif path == "/game":
                    st = owner.state.to_dict()
                    self._send_json(st["game"])
                elif path == "/library":
                    st = owner.state.to_dict()
                    self._send_json({"games": [st["game"]] if st["game"]["title"] else []})
                elif path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    initial = f"data: {json.dumps(owner.state.to_dict())}\n\n"
                    try:
                        self.wfile.write(initial.encode("utf-8"))
                        self.wfile.flush()
                        while not owner.stopped.is_set():
                            owner.state.wait_dirty(timeout=1.0)
                            if owner.stopped.is_set():
                                break
                            msg = f"data: {json.dumps(owner.state.to_dict())}\n\n"
                            self.wfile.write(msg.encode("utf-8"))
                            self.wfile.flush()
                    except (OSError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                        pass
                else:
                    self.send_error(404, "Not Found")

            def do_POST(self):
                peer_ip = self.client_address[0]
                if peer_ip not in ("127.0.0.1", "::1", "localhost"):
                    self.send_error(403, "Forbidden")
                    return
                target = urllib.parse.urlsplit(self.path)
                if target.path == "/logout":
                    host_header = self.headers.get("Host")
                    origin = self.headers.get("Origin")
                    if host_header not in (f"127.0.0.1:{owner.server_port}",
                                           f"localhost:{owner.server_port}") or (
                            origin and origin != f"http://{host_header}"):
                        self.send_error(403, "Forbidden")
                        return
                    if logout is None:
                        self._send_json({"ok": False}, 503)
                        return
                    try:
                        logout()
                    except Exception:
                        self._send_json({"ok": False}, 503)
                        return
                    owner.state.logout()
                    self._send_json({"ok": True})
                else:
                    self.send_error(404, "Not Found")

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False
            allow_reuse_address = False

            def handle_error(self, request, client_address):
                pass

            def server_bind(self):
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()

        self.server = Server((host, port), Handler)
        self.server_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="RA native webui")

    def start(self):
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.state._dirty.set()
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(timeout=3)
        self.server.server_close()
