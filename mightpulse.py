"""Reads an alliance roster (governor IDs, current names, power) from the MightPulse API.

Documented at https://api.mightpulse.com/ . A free key comes from signing in with Discord on that site.
Only the standard library is used, so nothing extra has to be installed.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import List

BASE = "https://api.mightpulse.com/v1"


class MightPulseError(Exception):
    pass


def find_members(obj) -> List[dict]:
    """Find the list of member records anywhere in the response (the wrapper name is not documented)."""
    best: List[dict] = []

    def walk(o):
        nonlocal best
        if isinstance(o, list):
            rows = [x for x in o if isinstance(x, dict) and (x.get("governor_id") or x.get("fid")) and x.get("nick_name")]
            if len(rows) > len(best):
                best = rows
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)

    walk(obj)
    out, seen = [], set()
    for m in best:
        gid = str(m.get("governor_id") or m.get("fid")).strip()
        if gid in seen:
            continue
        seen.add(gid)
        try:
            power = int(m["power"]) if m.get("power") not in (None, "") else None
        except (TypeError, ValueError):
            power = None
        out.append({"gid": gid, "name": str(m["nick_name"]), "might": power})
    return out


def fetch_alliance_members(key: str, kingdom, tag: str, timeout: int = 40) -> List[dict]:
    if not key:
        raise MightPulseError("No MightPulse API key is set.")
    url = f"{BASE}/alliances/{urllib.parse.quote(str(kingdom).strip())}/{urllib.parse.quote(tag.strip())}?include=roster&wait=20"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key.strip()}", "Accept": "application/json",
                                               "User-Agent": "ks-alliance-logger/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        hints = {401: "The API key was not accepted.", 403: "This key is not allowed to read that.",
                 404: "No alliance found. The tag is case-sensitive (KOR is not kor).",
                 429: "Too many requests. Wait a minute and try again."}
        raise MightPulseError(hints.get(e.code, f"MightPulse answered with an error ({e.code}).")) from None
    except Exception as e:  # network, timeout, bad JSON
        raise MightPulseError(f"Could not reach MightPulse: {str(e)[:100]}") from None
    members = find_members(body)
    if not members:
        raise MightPulseError("MightPulse answered, but no members were found in the reply.")
    return members
