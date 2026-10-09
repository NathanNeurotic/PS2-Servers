"""Local-only unlock notification classifier for the managed RA engine.

Use verified console sessions from SessionTracker and the engine's bounded
/state unlock ring. Never infer an unlock from remote Web API progression,
a catalog query, or a previously checked achievement set.
"""


def _event(value):
    if not isinstance(value, dict):
        return None
    achievement_id = value.get("id")
    seconds_ago = value.get("ago")
    points = value.get("points")
    title = value.get("title")
    if (type(achievement_id) is not int or achievement_id <= 0
            or type(seconds_ago) is not int or seconds_ago < 0
            or type(points) is not int or not 0 <= points <= 10000
            or not isinstance(title, str)):
        return None
    title = " ".join(title.split())[:96]
    if not title or any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
        return None
    return {"id": achievement_id, "title": title, "points": points, "ago": seconds_ago}


class UnlockTracker:
    """Treat engine history as a baseline, never as a desktop notification.

    A game switch, offline server, lost telemetry, or reconnect resets the
    baseline. The first observation cannot emit an unlock. Subsequent *new*
    IDs in the ring are shown only for a freshly verified playing session.
    """

    def __init__(self, max_age=8):
        self.max_age = max_age
        self.session = None
        self.seen = set()

    def reset(self):
        self.session = None
        self.seen.clear()

    def observe(self, state, verified):
        if (not isinstance(state, dict) or not isinstance(verified, dict)
                or verified.get("state") != "playing"
                or type(verified.get("session")) is not int
                or verified["session"] <= 0):
            self.reset()
            return []
        game = state.get("game")
        if (not isinstance(game, dict) or not game.get("serial")
                or game.get("title") != verified.get("title")):
            self.reset()
            return []
        ring = state.get("unlocks")
        if not isinstance(ring, list):
            self.reset()
            return []
        parsed = [item for raw in ring[:16] if (item := _event(raw)) is not None]
        session = verified["session"]
        if session != self.session:
            self.session = session
            self.seen = {item["id"] for item in parsed}
            return []
        fresh = []
        # Ring is newest-first; preserve chronological display for burst unlocks.
        for item in reversed(parsed):
            if item["id"] in self.seen:
                continue
            self.seen.add(item["id"])
            if item["ago"] <= self.max_age:
                fresh.append({"title": item["title"], "points": item["points"]})
        # Bound memory even in long-running modded sets. The API's ring is 16
        # entries; resetting the cache here cannot replay any ring member.
        if len(self.seen) > 4096:
            self.seen = {item["id"] for item in parsed}
        return fresh[-4:]
