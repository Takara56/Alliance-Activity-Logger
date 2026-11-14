"""Pure logic for the KS Alliance Activity Logger. No Streamlit imports, so it can be tested on its own."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

DEFAULT_EVENTS = [
    ("swordland-showdown", "Swordland Showdown"),
    ("alliance-mobilization", "Alliance Mobilization"),
    ("alliance-championship", "Alliance Championship"),
    ("vikings-vengeance", "Viking's Vengeance"),
    ("tri-alliance-clash", "Tri-Alliance Clash"),
    ("sanctuary-battle", "Sanctuary Battle"),
    ("kvk", "KvK"),
]

MODE_ATTENDANCE = "attendance"
MODE_MIGHT = "might"
MODE_ROSTER = "roster"


@dataclass
class Data:
    events: List[dict] = field(default_factory=lambda: [{"id": i, "name": n} for i, n in DEFAULT_EVENTS])
    roster: Dict[str, str] = field(default_factory=dict)  # pid -> display name
    attendance: Set[Tuple[str, str, str]] = field(default_factory=set)  # (week, event_id, pid)
    might: Dict[Tuple[str, str], int] = field(default_factory=dict)  # (week, pid) -> might
    aliases: Dict[str, str] = field(default_factory=dict)  # norm_key of a misread spelling -> pid it was corrected to

    def copy(self) -> "Data":
        return copy.deepcopy(self)


# ---------------------------------------------------------------- weeks

def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_label(d: date) -> str:
    e = d + timedelta(days=6)
    return f"{d:%b} {d.day} – {e:%b} {e.day}, {e.year}"


# ---------------------------------------------------------------- names

_TAG_RE = re.compile(r"^\s*[\[\(\{<【][^\]\)\}>】]{1,6}[\]\)\}>】]\s*")


def clean_name(s) -> str:
    if s is None:
        return ""
    s = re.sub(r"[​-‍﻿]", "", str(s))
    s = re.sub(r"\s+", " ", s).strip().strip("\"'“”‘’").strip()
    return _TAG_RE.sub("", s).strip()


def norm_key(name: str) -> str:
    k = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", str(name)).lower())
    return k or str(name).strip().lower()


def make_pid(name: str) -> str:
    return "p" + hashlib.sha1(norm_key(name).encode("utf-8")).hexdigest()[:12]


def fold(key: str) -> str:
    """Collapse look-alike characters that OCR confuses (I l 1 | and O 0)."""
    return re.sub(r"[o0]", "o", re.sub(r"[il1|]", "l", key))


def lev(a: str, b: str, max_d: int) -> int:
    if abs(len(a) - len(b)) > max_d:
        return max_d + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1))
            cur.append(v)
            row_min = min(row_min, v)
        if row_min > max_d:
            return max_d + 1
        prev = cur
    return prev[-1]


def classify(name: str, roster: Dict[str, str], aliases: Optional[Dict[str, str]] = None) -> Tuple[str, str, Optional[str]]:
    """Return (status, pid, match_pid). status is 'roster', 'close' or 'new'."""
    pid = make_pid(name)
    if pid in roster:
        return "roster", pid, pid
    learned = (aliases or {}).get(norm_key(name))
    if learned in roster:  # a spelling the officers corrected before
        return "roster", learned, learned
    f = fold(norm_key(name))
    # look-alike letters already fold to the same key; real edits are only allowed on longer names so that
    # two different short names (Wolf and Wolg) are never merged by mistake
    max_d = 2 if len(f) >= 12 else (1 if len(f) >= 6 else 0)
    best, best_d = None, 99
    for rpid, rname in roster.items():
        d = lev(f, fold(norm_key(rname)), max_d)  # max_d 0 still catches look-alike letters (W0lf = Wolf)
        if d <= max_d and d < best_d:
            best, best_d = rpid, d
    if best:
        return "close", pid, best
    return "new", pid, None


def status_text(name: str, roster: Dict[str, str], aliases: Optional[Dict[str, str]] = None) -> str:
    return suggest(name, roster, aliases)[1]


def suggest(name: str, roster: Dict[str, str], aliases: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """Return (name to show, status text). A read that matches a roster player is shown under the roster spelling."""
    status, pid, match = classify(name, roster, aliases)
    if status == "roster":
        shown = roster[match]
        return shown, ("On roster" if make_pid(name) == match else f"Matched to {shown}")
    if status == "close":
        return roster[match], f"Matched to {roster[match]} (close spelling)"
    return name, "New player"


# ---------------------------------------------------------------- might

_MIGHT_RE = re.compile(r"^(\d+(?:\.\d+)?)([kKmMbB])?$")


def parse_might(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if v != v or v < 0 or v in (float("inf"),):
            return None
        return int(round(v))
    t = re.sub(r"[,\s_]", "", str(v).strip())
    m = _MIGHT_RE.match(t)
    if not m:
        return None
    n = float(m.group(1))
    u = (m.group(2) or "").lower()
    n *= {"k": 1e3, "m": 1e6, "b": 1e9}.get(u, 1)
    return int(round(n))


def might_as_of(data: Data, week: str) -> Dict[str, Tuple[int, str]]:
    best: Dict[str, Tuple[int, str]] = {}
    for (w, pid), v in data.might.items():
        if w <= week and (pid not in best or w > best[pid][1]):
            best[pid] = (v, w)
    return best


# ---------------------------------------------------------------- pasted text and tables

def parse_paste(text: str, might_mode: bool) -> List[dict]:
    out: List[dict] = []
    if might_mode:
        for line in text.splitlines():
            s = re.sub(r"^\s*\d{1,3}[.)]\s+", "", line).strip()
            if not s:
                continue
            m = re.match(r"^(.*?)[\s,;\t]+(\d[\d.,]*\s*[kKmMbB]?)\s*$", s)
            if m and parse_might(m.group(2)) is not None:
                out.append({"name": m.group(1), "might": parse_might(m.group(2))})
            else:
                out.append({"name": s, "might": None})
        return out
    parts = text.splitlines() if "\n" in text else re.split(r"[,;]", text)
    for p in parts:
        s = re.sub(r"^\s*\d{1,3}[.)]\s+", "", p).strip()
        if s:
            out.append({"name": s, "might": None})
    return out


_NAME_HDR = re.compile(r"name|player|member|governor|user|nick|ign", re.I)
_POWER_HDR = re.compile(r"might|power", re.I)


def read_tables(raw: bytes, filename: str) -> Dict[str, pd.DataFrame]:
    lower = filename.lower()
    if lower.endswith((".csv", ".txt")):
        text = raw.decode("utf-8-sig", errors="replace")
        df = pd.read_csv(io.StringIO(text), header=None, dtype=str, keep_default_na=False, sep=None, engine="python")
        return {"Sheet 1": df.fillna("")}
    sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, header=None, dtype=str, engine="openpyxl")
    return {k: v.fillna("") for k, v in sheets.items()}


def detect_header(df: pd.DataFrame) -> bool:
    if len(df) == 0:
        return False
    r0 = [str(x).strip() for x in df.iloc[0]]
    if any(_NAME_HDR.search(c) or _POWER_HDR.search(c) for c in r0):
        return True
    if len(df) > 1:
        r1 = [str(x).strip() for x in df.iloc[1]]
        return all(c == "" or parse_might(c) is None for c in r0) and any(parse_might(c) is not None for c in r1)
    return False


def guess_columns(df: pd.DataFrame, header: bool) -> Tuple[int, Optional[int]]:
    head = [str(x) for x in df.iloc[0]] if header and len(df) else []
    body = df.iloc[1:] if header else df
    best, best_score, by_header = 0, -1, None
    for c in range(df.shape[1]):
        vals = [str(v).strip() for v in body.iloc[:, c]]
        score = sum(1 for v in vals if len(v) >= 2 and parse_might(v) is None)
        if score > best_score:
            best, best_score = c, score
        if header and by_header is None and _NAME_HDR.search(head[c]):
            by_header = c
    name_col = by_header if by_header is not None else best
    m_hdr, m_best, m_score = None, None, 0
    for c in range(df.shape[1]):
        if c == name_col:
            continue
        if header and m_hdr is None and _POWER_HDR.search(head[c]):
            m_hdr = c
        score = sum(1 for v in body.iloc[:, c] if (parse_might(v) or 0) >= 1000)
        if score > m_score:
            m_best, m_score = c, score
    return name_col, (m_hdr if m_hdr is not None else m_best)


def extract_rows(df: pd.DataFrame, header: bool, name_col: int, might_col: Optional[int]) -> List[dict]:
    body = df.iloc[1:] if header else df
    out = []
    for _, r in body.iterrows():
        name = clean_name(r.iloc[name_col])
        if not name or parse_might(name) is not None or re.fullmatch(r"(?i)name|player|rank|total|alliance", name):
            continue
        out.append({"name": name, "might": parse_might(r.iloc[might_col]) if might_col is not None else None})
    return out


# ---------------------------------------------------------------- OCR rows

@dataclass
class OcrItem:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = 1.0


_NUM_RE = re.compile(r"^\d[\d.,\s]*[kKmMbB]?$")
_MISSIONS_RE = re.compile(r"^\W*(missions?\b|\w{0,4}ns?:)|\d+\s*/\s*\d+\s*$", re.I)
_RANK_RE = re.compile(r"^(no\.?|#|№)\s*\d+$", re.I)
IGNORE_WORDS = {
    "rank", "ranking", "rankings", "name", "player", "power", "might", "score", "kills", "damage", "total",
    "alliance", "member", "members", "reward", "rewards", "claim", "close", "back", "participation",
    "individual", "points", "contribution", "level", "lv", "lv.", "online", "offline", "r1", "r2", "r3", "r4", "r5",
}


def _center(it: OcrItem) -> float:
    return (it.y0 + it.y1) / 2


def group_rows(items: List[OcrItem]) -> List[List[OcrItem]]:
    if not items:
        return []
    tol = 0.6 * median([max(it.y1 - it.y0, 1) for it in items])
    items = sorted(items, key=_center)
    rows, cur, cy = [], [items[0]], _center(items[0])
    for it in items[1:]:
        c = _center(it)
        if abs(c - cy) <= tol:
            cur.append(it)
            cy = sum(_center(x) for x in cur) / len(cur)
        else:
            rows.append(cur)
            cur, cy = [it], c
    rows.append(cur)
    return rows


def _segments(row: List[OcrItem]) -> List[dict]:
    segs: List[dict] = []
    for it in sorted(row, key=lambda i: i.x0):
        h = max(it.y1 - it.y0, 1)
        t = it.text.strip()
        if not t:
            continue
        if segs and not _NUM_RE.match(t) and not _NUM_RE.match(segs[-1]["text"]) and it.x0 - segs[-1]["x1"] < 0.8 * h:
            segs[-1]["text"] += " " + t
            segs[-1]["x1"] = it.x1
            segs[-1]["conf"] = min(segs[-1]["conf"], it.conf)
        else:
            segs.append({"text": t, "x0": it.x0, "x1": it.x1, "conf": it.conf})
    return segs


def rows_from_items(items: List[OcrItem], mode: str = MODE_ATTENDANCE, pick: str = "largest", ignore=()) -> List[dict]:
    ignore_set = {w.strip().lower() for w in ignore if w.strip()} | IGNORE_WORDS
    out: List[dict] = []
    for row in group_rows(items):
        names, nums = [], []
        # drop header words and rank labels first so they are never joined onto a player's name
        row = [it for it in row if it.text.strip().lower() not in ignore_set and not _RANK_RE.match(it.text.strip())
               and not _MISSIONS_RE.match(it.text.strip())]
        for s in _segments(row):
            t = s["text"]
            if _NUM_RE.match(t):
                n = parse_might(t)
                if n is not None:
                    nums.append((n, s["x0"]))
                continue
            if t.lower() in ignore_set or not re.search(r"\w", t):
                continue
            names.append((t, s["conf"]))
        if not names:
            continue
        name = clean_name(names[0][0])
        if not name:
            continue
        might = None
        big = [(n, x) for n, x in nums if n >= 1000]
        if mode == MODE_MIGHT and big:
            if pick == "leftmost":
                might = min(big, key=lambda p: p[1])[0]
            elif pick == "rightmost":
                might = max(big, key=lambda p: p[1])[0]
            else:
                might = max(big)[0]
        out.append({"name": name, "might": might, "unsure": names[0][1] < 0.85})
    return out


def dedupe_rows(rows: List[dict]) -> List[dict]:
    seen: Dict[str, int] = {}
    out: List[dict] = []
    for r in rows:
        k = fold(norm_key(r["name"]))
        if k in seen:
            prev = out[seen[k]]
            if prev.get("might") is None and r.get("might") is not None:
                prev["might"] = r["might"]
            if prev.get("unsure") and not r.get("unsure"):
                prev["name"], prev["unsure"] = r["name"], False
            continue
        seen[k] = len(out)
        out.append(dict(r))
    return out


# ---------------------------------------------------------------- committing and undo

def commit_rows(data: Data, rows: List[dict], mode: str, event_id: Optional[str], week: str,
                fresh: bool = False, merge_close: bool = True) -> dict:
    new_players, learned, resolved = 0, 0, []
    for r in rows:
        name = clean_name(r.get("name"))
        if not name:
            continue
        status, pid, match = classify(name, data.roster, data.aliases)
        if status == "roster":
            use = pid
        elif status == "close" and merge_close:
            use = match
        else:
            use = pid
            data.roster[use] = name
            new_players += 1
        resolved.append((use, parse_might(r.get("might"))))
        # remember a correction (read as X, confirmed as Y) so the same misread is fixed automatically next time
        orig = clean_name(r.get("orig"))
        if orig and norm_key(orig) != norm_key(data.roster[use]) and make_pid(orig) not in data.roster \
                and data.aliases.get(norm_key(orig)) != use:
            data.aliases[norm_key(orig)] = use
            learned += 1
    marked = might_set = 0
    if mode == MODE_ATTENDANCE and event_id:
        if fresh:
            data.attendance = {t for t in data.attendance if not (t[0] == week and t[1] == event_id)}
        for pid, _ in resolved:
            data.attendance.add((week, event_id, pid))
        marked = len({pid for pid, _ in resolved})
    elif mode == MODE_MIGHT:
        for pid, m in resolved:
            if m is not None:
                data.might[(week, pid)] = m
                might_set += 1
    return {"new_players": new_players, "marked": marked, "might_set": might_set, "learned": learned}


def diff_data(before: Data, after: Data) -> dict:
    might = {}
    for k in set(before.might) | set(after.might):
        if before.might.get(k) != after.might.get(k):
            might[k] = (before.might.get(k), after.might.get(k))
    return {
        "alias_add": {k: v for k, v in after.aliases.items() if before.aliases.get(k) != v},
        "alias_old": {k: v for k, v in before.aliases.items() if after.aliases.get(k) != v},
        "att_add": after.attendance - before.attendance,
        "att_del": before.attendance - after.attendance,
        "roster_add": {k: v for k, v in after.roster.items() if k not in before.roster},
        "roster_del": {k: v for k, v in before.roster.items() if k not in after.roster},
        "might": might,
    }


def diff_is_empty(d: dict) -> bool:
    return not (d["att_add"] or d["att_del"] or d["roster_add"] or d["roster_del"] or d["might"]
                or d["alias_add"] or d["alias_old"])


def undo_diff(data: Data, d: dict) -> None:
    data.attendance -= d["att_add"]
    data.attendance |= d["att_del"]
    for pid in d["roster_add"]:
        data.roster.pop(pid, None)
    data.roster.update(d["roster_del"])
    for k in d["alias_add"]:
        data.aliases.pop(k, None)
    data.aliases.update(d["alias_old"])
    for key, (old, _new) in d["might"].items():
        if old is None:
            data.might.pop(key, None)
        else:
            data.might[key] = old


def clear_attendance(data: Data, week: str, event_id: str) -> int:
    n = len([t for t in data.attendance if t[0] == week and t[1] == event_id])
    data.attendance = {t for t in data.attendance if not (t[0] == week and t[1] == event_id)}
    return n


def clear_might(data: Data, week: str) -> int:
    keys = [k for k in data.might if k[0] == week]
    for k in keys:
        del data.might[k]
    return len(keys)


def remove_players(data: Data, pids) -> None:
    pids = set(pids)
    for p in pids:
        data.roster.pop(p, None)
    data.attendance = {t for t in data.attendance if t[2] not in pids}
    data.might = {k: v for k, v in data.might.items() if k[1] not in pids}
    data.aliases = {k: v for k, v in data.aliases.items() if v not in pids}


def clear_all_players(data: Data) -> int:
    """Remove every player together with all of their marks, might and remembered spellings. Events stay."""
    n = len(data.roster)
    data.roster = {}
    data.attendance = set()
    data.might = {}
    data.aliases = {}
    return n


def merge_players(data: Data, from_pid: str, into_pid: str) -> None:
    """Fold one player into another (a rename that was logged as a new player). The kept player's name stays;
    marks are combined, might is kept for weeks the kept player has none, and the old name is remembered."""
    if from_pid == into_pid or from_pid not in data.roster or into_pid not in data.roster:
        return
    old_name = data.roster.pop(from_pid)
    data.attendance = {(w, e, into_pid if p == from_pid else p) for (w, e, p) in data.attendance}
    for (w, p), v in list(data.might.items()):
        if p == from_pid:
            del data.might[(w, p)]
            data.might.setdefault((w, into_pid), v)
    data.aliases = {k: (into_pid if v == from_pid else v) for k, v in data.aliases.items()}
    if norm_key(old_name) != norm_key(data.roster[into_pid]):
        data.aliases[norm_key(old_name)] = into_pid


def rename_player(data: Data, pid: str, new_name: str) -> bool:
    new_name = clean_name(new_name)
    if pid not in data.roster or not new_name or data.roster[pid] == new_name:
        return False
    old = data.roster[pid]
    if norm_key(old) != norm_key(new_name):
        data.aliases[norm_key(old)] = pid
    data.roster[pid] = new_name
    return True


def sync_roster(data: Data, members: List[dict], week: Optional[str] = None) -> dict:
    """Key players by game (governor) ID. members: [{"gid","name","might"}] from MightPulse.
    Known ID -> its name is updated (a rename). Unknown ID -> linked to the roster player with the same name
    (keeping all history), otherwise added as new. Players that stay unlinked are listed for the officer."""
    rep = {"renamed": [], "linked": 0, "added": 0, "might_set": 0, "unlinked": []}
    for m in members:
        gid, name = str(m["gid"]).strip(), clean_name(m["name"])
        if not gid or not name:
            continue
        gp = "g" + gid
        if gp in data.roster:
            prev = data.roster[gp]
            if rename_player(data, gp, name):
                rep["renamed"].append((prev, name))
        else:
            existing = next((p for p, n in data.roster.items() if not p.startswith("g") and norm_key(n) == norm_key(name)), None)
            if existing is None:
                a = data.aliases.get(norm_key(name))
                existing = a if a in data.roster and not a.startswith("g") else None
            data.roster[gp] = name
            if existing:
                merge_players(data, existing, gp)
                data.roster[gp] = name
                rep["linked"] += 1
            else:
                rep["added"] += 1
        if week and m.get("might"):
            data.might[(week, gp)] = int(m["might"])
            rep["might_set"] += 1
    rep["unlinked"] = sorted(n for p, n in data.roster.items() if not p.startswith("g"))
    return rep


def add_event(data: Data, name: str) -> Optional[str]:
    name = name.strip()
    if not name:
        return "Type an event name first."
    if any(e["name"].lower() == name.lower() for e in data.events):
        return f"{name} is already in the list."
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "event"
    eid, n = base, 2
    while any(e["id"] == eid for e in data.events):
        eid, n = f"{base}-{n}", n + 1
    data.events.append({"id": eid, "name": name})
    return None


def remove_event(data: Data, event_id: str) -> None:
    data.events = [e for e in data.events if e["id"] != event_id]


# ---------------------------------------------------------------- ledger tables

def _sorted(df: pd.DataFrame, sort: str, total_col: str = "Total") -> pd.DataFrame:
    if df.empty:
        return df.reset_index(drop=True)
    df = df.copy()
    df["_k"] = df["Player"].str.casefold()
    if sort == "most":
        df = df.sort_values([total_col, "_k"], ascending=[False, True])
    elif sort == "least":
        df = df.sort_values([total_col, "_k"], ascending=[True, True])
    elif sort == "might_desc":
        df = df.sort_values(["Might", "_k"], ascending=[False, True], na_position="last")
    elif sort == "might_asc":
        df = df.sort_values(["Might", "_k"], ascending=[True, True], na_position="last")
    else:
        df = df.sort_values("_k")
    return df.drop(columns="_k").reset_index(drop=True)


def build_ledger(data: Data, week: str, sort: str = "name", query: str = "") -> pd.DataFrame:
    asof = might_as_of(data, week)
    att: Dict[str, Set[str]] = defaultdict(set)
    for w, e, p in data.attendance:
        if w == week:
            att[p].add(e)
    rows = []
    for pid, name in data.roster.items():
        if query and query.lower() not in name.lower():
            continue
        m = asof.get(pid)
        row = {"pid": pid, "Player": name, "Might": float(m[0]) if m else float("nan"),
               "Might as of": "" if (not m or m[1] == week) else m[1]}
        for e in data.events:
            row[e["name"]] = e["id"] in att[pid]
        row["Total"] = sum(1 for e in data.events if e["id"] in att[pid])
        rows.append(row)
    cols = ["pid", "Player", "Might", "Might as of"] + [e["name"] for e in data.events] + ["Total"]
    return _sorted(pd.DataFrame(rows, columns=cols), sort)


def build_history_table(data: Data, sort: str = "name", query: str = "") -> pd.DataFrame:
    counts: Dict[Tuple[str, str], int] = defaultdict(int)
    weeks_by_pid: Dict[str, Set[str]] = defaultdict(set)
    for w, e, p in data.attendance:
        counts[(p, e)] += 1
        weeks_by_pid[p].add(w)
    latest = might_as_of(data, "9999-12-31")
    rows = []
    for pid, name in data.roster.items():
        if query and query.lower() not in name.lower():
            continue
        m = latest.get(pid)
        row = {"Player": name, "Might": float(m[0]) if m else float("nan")}
        for e in data.events:
            row[e["name"]] = counts[(pid, e["id"])]
        row["Total"] = sum(counts[(pid, e["id"])] for e in data.events)
        row["Weeks active"] = len(weeks_by_pid[pid])
        rows.append(row)
    cols = ["Player", "Might"] + [e["name"] for e in data.events] + ["Total", "Weeks active"]
    return _sorted(pd.DataFrame(rows, columns=cols), sort)


def apply_ledger_edits(data: Data, original: pd.DataFrame, edited: pd.DataFrame, week: str) -> int:
    changes = 0
    for i in range(min(len(original), len(edited))):
        o, e = original.iloc[i], edited.iloc[i]
        pid = o["pid"]
        if pid not in data.roster:
            continue
        for ev in data.events:
            col = ev["name"]
            if col not in original.columns:
                continue
            if bool(o[col]) != bool(e[col]):
                t = (week, ev["id"], pid)
                if bool(e[col]):
                    data.attendance.add(t)
                else:
                    data.attendance.discard(t)
                changes += 1
        om, nm = o["Might"], e["Might"]
        if pd.isna(om) and pd.isna(nm):
            continue
        if pd.isna(om) != pd.isna(nm) or int(om) != int(nm):
            if pd.isna(nm):
                data.might.pop((week, pid), None)
            else:
                data.might[(week, pid)] = int(nm)
            changes += 1
    return changes


# ---------------------------------------------------------------- export, backup, restore

def _csv(rows: List[list]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    return buf.getvalue()


def week_csv(data: Data, week: str) -> str:
    df = build_ledger(data, week)
    rows = [["Player", "Might"] + [e["name"] for e in data.events] + ["Total"]]
    for _, r in df.iterrows():
        rows.append([r["Player"], "" if pd.isna(r["Might"]) else int(r["Might"])]
                    + ["Yes" if r[e["name"]] else "" for e in data.events] + [int(r["Total"])])
    return _csv(rows)


def history_csv(data: Data) -> str:
    ev_ids = [e["id"] for e in data.events]
    names = {e["id"]: e["name"] for e in data.events}
    extra = sorted({t[1] for t in data.attendance if t[1] not in names})
    cols = ev_ids + extra
    rows = [["Week starting", "Player", "Might"] + [names.get(c, f"{c} (removed)") for c in cols] + ["Total"]]
    by_week: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    for w, e, p in data.attendance:
        by_week[w][p].add(e)
    for w in sorted(by_week):
        asof = might_as_of(data, w)
        for pid in sorted(by_week[w], key=lambda p: data.roster.get(p, p).casefold()):
            evs = by_week[w][pid]
            rows.append([w, data.roster.get(pid, pid), asof[pid][0] if pid in asof else ""]
                        + ["Yes" if c in evs else "" for c in cols] + [len(evs)])
    return _csv(rows)


def data_to_dict(data: Data) -> dict:
    return {
        "app": "ks-alliance-activity-logger",
        "version": 2,
        "events": data.events,
        "roster": data.roster,
        "attendance": sorted([list(t) for t in data.attendance]),
        "might": sorted([[w, p, v] for (w, p), v in data.might.items()]),
        "aliases": dict(data.aliases),
    }


_WEEK_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EID_RE = re.compile(r"^[A-Za-z0-9_.~:@+-]{1,100}$")


def merge_backup(data: Data, text: str) -> Tuple[Optional[Data], str]:
    """Merge a backup into data. Accepts this app's backup and the earlier web-page backup. Nothing is deleted
    and existing might values are kept."""
    try:
        d = json.loads(text)
    except Exception:
        return None, "That file is not a valid backup."
    if not isinstance(d, dict) or d.get("app") != "ks-alliance-activity-logger" or not isinstance(d.get("roster"), dict):
        return None, "That file is not a backup from this log."
    out = data.copy()
    remap: Dict[str, str] = {}
    added_players = 0
    for old, name in d["roster"].items():
        name = clean_name(name)[:60] if isinstance(name, str) else ""
        if not name:
            continue
        pid = make_pid(name)
        remap[old] = pid
        if pid not in out.roster:
            out.roster[pid] = name
            added_players += 1
    for e in d.get("events") or []:
        if isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("name"), str) \
                and _EID_RE.match(e["id"]) and e["name"].strip() and not any(x["id"] == e["id"] for x in out.events):
            out.events.append({"id": e["id"], "name": e["name"].strip()[:60]})
    att_before = len(out.attendance)
    att = d.get("attendance")
    triples: List[Tuple[str, str, str]] = []
    if isinstance(att, dict):  # earlier web-page format: {doc: {week, event, players: {pid: true}}}
        for doc in att.values():
            if isinstance(doc, dict) and isinstance(doc.get("players"), dict):
                for p, on in doc["players"].items():
                    if on:
                        triples.append((doc.get("week"), doc.get("event"), p))
    elif isinstance(att, list):
        triples = [tuple(t) for t in att if isinstance(t, (list, tuple)) and len(t) == 3]
    for w, e, p in triples:
        if isinstance(w, str) and _WEEK_RE.match(w) and isinstance(e, str) and _EID_RE.match(e) and p in remap:
            out.attendance.add((w, e, remap[p]))
    mg = d.get("might")
    entries: List[Tuple[str, str, object]] = []
    if isinstance(mg, dict):
        for w, players in mg.items():
            if isinstance(players, dict):
                entries += [(w, p, v) for p, v in players.items()]
    elif isinstance(mg, list):
        entries = [tuple(t) for t in mg if isinstance(t, (list, tuple)) and len(t) == 3]
    might_added = 0
    for w, p, v in entries:
        if isinstance(w, str) and _WEEK_RE.match(w) and p in remap and isinstance(v, (int, float)) and v >= 0:
            key = (w, remap[p])
            if key not in out.might:
                out.might[key] = int(v)
                might_added += 1
    if isinstance(d.get("aliases"), dict):
        for k, v in d["aliases"].items():
            if isinstance(k, str) and v in remap and k not in out.aliases:
                out.aliases[k] = remap[v]
    return out, (f"Added {added_players} player(s), {len(out.attendance) - att_before} attendance mark(s) "
                 f"and {might_added} might value(s).")
