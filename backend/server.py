#!/usr/bin/env python3
"""Tic Tac Toe 3D — authoritative multiplayer backend (stdlib only).
Run:  python backend/server.py [--port 8000]
API:  POST /rooms, POST /rooms/{code}/join, GET /rooms/{code}?token=...,
      POST /rooms/{code}/move, POST /rooms/{code}/rematch,
      POST /rooms/{code}/leave, POST /rooms/{code}/profile, GET /health
"""
import json, os, secrets, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WIN_LINES = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]]
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PRESENCE_WINDOW = 30.0
ROOM_TTL = 1800.0

ROOMS = {}
LOCK = threading.Lock()

def now(): return time.time()

def new_code():
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(6))
        if code not in ROOMS:
            return code

def win_line(cells):
    for line in WIN_LINES:
        a, b, c = line
        if cells[a] != 0 and cells[a] == cells[b] == cells[c]:
            return line
    return None

def make_room(code, name, avatar):
    tok = secrets.token_hex(16)
    return {
        "code": code,
        "status": "waiting",
        "round": 1, "starter": 1, "turn": 1,
        "winner": 0, "winLine": None,
        "cells": [0]*9,
        "scoreX": 0, "scoreO": 0,
        "version": 1,
        "tokens": {"X": tok, "O": None},
        "players": {
            "X": {"name": name[:12].upper(), "avatar": int(avatar), "lastSeen": now()},
            "O": None,
        },
        "rematch": {"X": False, "O": False},
        "createdAt": now(), "updatedAt": now(),
    }, tok

def pub_player(p):
    if p is None: return None
    return {"name": p["name"], "avatar": p["avatar"],
            "connected": (now() - p["lastSeen"]) < PRESENCE_WINDOW}

def state(room, my_role=None):
    return {
        "code": room["code"],
        "status": room["status"],
        "round": room["round"], "starter": room["starter"], "turn": room["turn"],
        "winner": room["winner"], "winLine": room["winLine"],
        "cells": list(room["cells"]),
        "scoreX": room["scoreX"], "scoreO": room["scoreO"],
        "version": room["version"],
        "players": {"X": pub_player(room["players"]["X"]),
                    "O": pub_player(room["players"]["O"])},
        "rematch": dict(room["rematch"]),
        "myRole": my_role,
        "updatedAt": room["updatedAt"],
    }

def role_for(room, token):
    for r in ("X", "O"):
        if token and token == room["tokens"].get(r):
            return r
    return None

def touch(room, role):
    room["players"][role]["lastSeen"] = now()
    room["updatedAt"] = now()

def bump(room):
    """Bump on real state changes only (join/move/rematch/leave/profile) —
    long-poll watchers wake on this, never on presence touches."""
    room["version"] += 1
    room["updatedAt"] = now()

def finish(room, winner):
    room["winner"] = winner
    room["winLine"] = win_line(room["cells"])
    room["status"] = "finished"
    room["rematch"] = {"X": False, "O": False}

class H(BaseHTTPRequestHandler):
    server_version = "TicTacToeOnline/1.0"

    def log_message(self, *a): pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0: return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-Player-Token")
        self.end_headers()

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        if u.path == "/health":
            with LOCK:
                return self._json(200, {"ok": True, "rooms": len(ROOMS)})
        parts = u.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "rooms":
            code = parts[1].upper()
            q = parse_qs(u.query)
            token = (q.get("token") or [None])[0] or self.headers.get("X-Player-Token")
            wait = (q.get("wait") or ["0"])[0] in ("1", "true")
            try: since = int((q.get("since") or ["0"])[0])
            except ValueError: since = 0
            # Long-poll: hold the request (max 20s) until the room version changes.
            # Turns arrive on the device the instant the opponent moves.
            deadline = now() + 20.0
            while True:
                with LOCK:
                    room = ROOMS.get(code)
                    if not room: return self._json(404, {"error": "room not found"})
                    role = role_for(room, token)
                    if not role: return self._json(403, {"error": "bad token"})
                    touch(room, role)
                    if not wait or room["version"] != since or now() >= deadline:
                        return self._json(200, state(room, role))
                time.sleep(0.25)
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        from urllib.parse import urlparse
        u = urlparse(self.path)
        parts = u.path.strip("/").split("/")
        data = self._body()
        if parts == ["rooms"]:
            name = str(data.get("name") or "PLAYER")[:12].upper() or "PLAYER"
            try: avatar = int(data.get("avatar") or 0)
            except ValueError: avatar = 0
            avatar = max(0, min(5, avatar))
            with LOCK:
                code = new_code()
                room, tok = make_room(code, name, avatar)
                ROOMS[code] = room
                return self._json(201, {"token": tok, "role": "X", "state": state(room, "X")})
        if len(parts) == 3 and parts[0] == "rooms":
            code = parts[1].upper(); action = parts[2]
            token = data.get("token") or self.headers.get("X-Player-Token")
            with LOCK:
                room = ROOMS.get(code)
                if not room: return self._json(404, {"error": "room not found"})
                if action == "join":
                    if room["players"]["O"] is not None:
                        # allow reconnect: if same token slot? no — slot taken
                        return self._json(409, {"error": "room full"})
                    name = str(data.get("name") or "PLAYER")[:12].upper() or "PLAYER"
                    try: avatar = int(data.get("avatar") or 0)
                    except ValueError: avatar = 0
                    avatar = max(0, min(5, avatar))
                    tok = secrets.token_hex(16)
                    room["tokens"]["O"] = tok
                    room["players"]["O"] = {"name": name, "avatar": avatar, "lastSeen": now()}
                    room["status"] = "playing"
                    room["turn"] = room["starter"]
                    bump(room)
                    return self._json(200, {"token": tok, "role": "O", "state": state(room, "O")})
                role = role_for(room, token)
                if not role: return self._json(403, {"error": "bad token"})
                touch(room, role)
                if action == "profile":
                    name = str(data.get("name") or room["players"][role]["name"])[:12].upper() or room["players"][role]["name"]
                    try: avatar = int(data.get("avatar", room["players"][role]["avatar"]))
                    except ValueError: avatar = room["players"][role]["avatar"]
                    room["players"][role]["name"] = name
                    room["players"][role]["avatar"] = max(0, min(5, avatar))
                    bump(room)
                    return self._json(200, state(room, role))
                if action == "move":
                    if room["status"] != "playing":
                        return self._json(409, {"error": "not playing"})
                    want = 1 if role == "X" else 2
                    if room["turn"] != want:
                        return self._json(403, {"error": "not your turn"})
                    try: cell = int(data.get("cell"))
                    except (TypeError, ValueError): return self._json(400, {"error": "bad cell"})
                    if cell < 0 or cell > 8: return self._json(400, {"error": "bad cell"})
                    if room["cells"][cell] != 0: return self._json(409, {"error": "occupied"})
                    room["cells"][cell] = want
                    line = win_line(room["cells"])
                    if line:
                        room["winLine"] = line; room["winner"] = want
                        room["status"] = "finished"; room["rematch"] = {"X": False, "O": False}
                        if want == 1: room["scoreX"] += 1
                        else: room["scoreO"] += 1
                    elif 0 not in room["cells"]:
                        room["winner"] = 3; room["winLine"] = None
                        room["status"] = "finished"; room["rematch"] = {"X": False, "O": False}
                    else:
                        room["turn"] = 3 - want
                    bump(room)
                    return self._json(200, state(room, role))
                if action == "rematch":
                    if room["status"] != "finished":
                        return self._json(409, {"error": "not finished"})
                    room["rematch"][role] = True
                    if room["rematch"]["X"] and room["rematch"]["O"]:
                        room["cells"] = [0]*9; room["round"] += 1
                        room["starter"] = 3 - room["starter"]
                        room["turn"] = room["starter"]
                        room["winner"] = 0; room["winLine"] = None
                        room["status"] = "playing"
                        room["rematch"] = {"X": False, "O": False}
                    bump(room)
                    return self._json(200, state(room, role))
                if action == "leave":
                    me = 1 if role == "X" else 2
                    opp = 3 - me
                    other_role = "O" if role == "X" else "X"
                    was_playing = room["status"] == "playing"
                    room["players"][role] = None
                    room["tokens"][role] = None
                    if was_playing:
                        room["winner"] = opp
                        if opp == 1: room["scoreX"] += 1
                        else: room["scoreO"] += 1
                        room["status"] = "finished"
                        room["rematch"] = {"X": False, "O": False}
                        bump(room)
                        return self._json(200, {"ok": True, "state": state(room, None)})
                    if room["players"]["X"] is None and room["players"]["O"] is None:
                        del ROOMS[code]
                    else:
                        room["status"] = "waiting"
                        room["cells"] = [0]*9; room["winner"] = 0; room["winLine"] = None
                        room["round"] = 1; room["scoreX"] = 0; room["scoreO"] = 0
                        room["starter"] = 1; room["turn"] = 1
                        room["rematch"] = {"X": False, "O": False}
                        bump(room)
                    return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

def cleanup():
    while True:
        time.sleep(60)
        with LOCK:
            dead = [c for c, r in ROOMS.items() if now() - r["updatedAt"] > ROOM_TTL]
            for c in dead: ROOMS.pop(c, None)

def main():
    port = int(os.environ.get("PORT", "8000"))
    threading.Thread(target=cleanup, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"online backend on 0.0.0.0:{port} — create: POST /rooms")
    srv.serve_forever()

if __name__ == "__main__":
    main()
