"""Where the log is kept: a local JSON file (for trying it out) or a Google Sheet (for real use)."""
from __future__ import annotations

import json
import os
import threading
from typing import Dict, List

import logic as L


def _from_dict(d: dict) -> L.Data:
    data = L.Data()
    if "events" in d and isinstance(d["events"], list):
        data.events = [{"id": e["id"], "name": e["name"]} for e in d["events"]]
    data.roster = {k: v for k, v in (d.get("roster") or {}).items()}
    data.attendance = {tuple(t) for t in d.get("attendance") or []}
    data.might = {(w, p): int(v) for w, p, v in d.get("might") or []}
    return data


class LocalStore:
    def __init__(self, path: str = "data.json"):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> L.Data:
        with self._lock:
            if not os.path.exists(self.path):
                return L.Data()
            with open(self.path, encoding="utf-8") as f:
                return _from_dict(json.load(f))

    def save(self, data: L.Data) -> None:
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(L.data_to_dict(data), f, ensure_ascii=False)
            os.replace(tmp, self.path)


class SheetsStore:
    """Keeps the log in four tabs of one Google Sheet: Events, Roster, Attendance, Might."""

    TABLES = {
        "Events": ["id", "name"],
        "Roster": ["pid", "name"],
        "Attendance": ["week", "event", "pid"],
        "Might": ["week", "pid", "might"],
    }

    def __init__(self, info: dict, sheet_id: str):
        import gspread

        self._gs = gspread
        self._sh = gspread.service_account_from_dict(info).open_by_key(sheet_id)
        self._lock = threading.Lock()
        self._ws: Dict[str, object] = {}
        self._last: Dict[str, List[List[str]]] = {}
        for title in self.TABLES:
            self._worksheet(title)

    def _worksheet(self, title: str):
        if title in self._ws:
            return self._ws[title]
        header = self.TABLES[title]
        try:
            ws = self._sh.worksheet(title)
        except self._gs.WorksheetNotFound:
            ws = self._sh.add_worksheet(title=title, rows=200, cols=max(4, len(header)))
            rows = [header]
            if title == "Events":
                rows += [[i, n] for i, n in L.DEFAULT_EVENTS]
            ws.update(range_name="A1", values=rows, value_input_option="RAW")
        self._ws[title] = ws
        return ws

    @staticmethod
    def _norm(values: List[List[str]], width: int) -> List[List[str]]:
        rows = []
        for r in values[1:]:
            r = [str(c).strip() for c in r][:width] + [""] * max(0, width - len(r))
            if any(r):
                rows.append(r)
        return rows

    def _read_all(self) -> Dict[str, List[List[str]]]:
        titles = list(self.TABLES)
        try:
            res = self._sh.values_batch_get(titles)
            return {t: vr.get("values", []) for t, vr in zip(titles, res["valueRanges"])}
        except Exception:
            return {t: self._worksheet(t).get_all_values() for t in titles}

    def _tables(self, data: L.Data) -> Dict[str, List[List[str]]]:
        return {
            "Events": [[e["id"], e["name"]] for e in data.events],
            "Roster": sorted([p, n] for p, n in data.roster.items()),
            "Attendance": sorted([w, e, p] for w, e, p in data.attendance),
            "Might": sorted([w, p, str(v)] for (w, p), v in data.might.items()),
        }

    def load(self) -> L.Data:
        with self._lock:
            raw = self._read_all()
            tables = {t: self._norm(raw[t], len(self.TABLES[t])) for t in self.TABLES}
            data = L.Data()
            data.events = [{"id": r[0], "name": r[1]} for r in tables["Events"] if r[0] and r[1]]
            data.roster = {r[0]: r[1] for r in tables["Roster"] if r[0] and r[1]}
            data.attendance = {(r[0], r[1], r[2]) for r in tables["Attendance"] if all(r)}
            for r in tables["Might"]:
                try:
                    data.might[(r[0], r[1])] = int(float(r[2]))
                except ValueError:
                    continue
            self._last = self._tables(data)
            return data

    def save(self, data: L.Data) -> None:
        with self._lock:
            for title, rows in self._tables(data).items():
                if rows == self._last.get(title):
                    continue
                ws = self._worksheet(title)
                values = [self.TABLES[title]] + rows
                ws.clear()
                need = max(len(values) + 50, 100)
                if ws.row_count < need:
                    ws.resize(rows=need)
                ws.update(range_name="A1", values=values, value_input_option="RAW")
                self._last[title] = rows
