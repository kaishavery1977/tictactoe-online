# Online backend — Tic Tac Toe 3D

Authoritative Python server (stdlib only, no `pip install`).

## Run locally (USB test)

```bash
python backend/server.py            # PORT=8000 by default
adb reverse tcp:8000 tcp:8000       # phone -> PC (debug only)
```

Then in the game: MULTIPLAYER → CREATE ROOM → share the 6-letter code.
A second phone (or `python` below) joins with that code.

```python
import urllib.request, json
base="http://127.0.0.1:8000"
def call(path, body=None):
    d=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(base+path, data=d,
        headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(r).read())
j=call("/rooms/join", {"name":"BOT","avatar":2})  # wrong path — see below
```

Correct join:

```python
call("/rooms/ABC123/join", {"name":"BOT","avatar":2})
```

## Deploy (any Python host)

Set `PORT` (Render/Railway/Fly inject it). Example Render: Build `pip install -r backend/requirements.txt` (empty), Start `python backend/server.py`.
Copy the public URL into the game — simplest is `SettingsStore` default, or edit
`DEFAULT_ONLINE_URL` in code and rebuild. `requirements.txt` is intentionally empty.
