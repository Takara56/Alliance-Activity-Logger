"""KS Alliance Activity Logger: weekly event attendance and might for a Kingshot alliance."""
from __future__ import annotations

import hmac
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import logic as L
import ocr
from store import LocalStore, SheetsStore

st.set_page_config(page_title="KS Alliance Activity Logger", layout="wide")

MIGHT_OPT = "__might__"
ROSTER_OPT = "__roster__"
SORTS = {
    "Player name, A to Z": "name",
    "Attendance, most first": "most",
    "Attendance, fewest first": "least",
    "Might, highest first": "might_desc",
    "Might, lowest first": "might_asc",
}


def secret(name, default=None):
    try:
        return st.secrets[name]
    except Exception:
        return default


def gate():
    pw = secret("app_password")
    if not pw or st.session_state.get("authed"):
        return
    st.title("KS Alliance Activity Logger")
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(str(entered), str(pw)):
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("That password is not right.")
    st.stop()


@st.cache_resource
def get_store():
    info, sheet_id = secret("gcp_service_account"), secret("sheet_id")
    if info and sheet_id:
        return SheetsStore(dict(info), str(sheet_id)), "sheets"
    return LocalStore("data.json"), "local"


@st.cache_resource(show_spinner="Loading the text reader (first time only)...")
def get_engine():
    return ocr.load_engine()


gate()
store, store_kind = get_store()
ss = st.session_state
ss.setdefault("stamp", 0)
ss.setdefault("week", L.monday_of(date.today()))
ss.setdefault("staged", None)
ss.setdefault("nonce", 0)
ss.setdefault("undo", None)
ss.setdefault("flash", None)


@st.cache_data(ttl=20, show_spinner=False)
def cached_load(_store, stamp):
    return _store.load()


def apply(mutator, undoable=True):
    """Reload the latest log, change it, save it. Reloading first keeps two officers from overwriting each other."""
    fresh = store.load()
    before = fresh.copy()
    result = mutator(fresh)
    store.save(fresh)
    ss.stamp += 1
    if undoable:
        d = L.diff_data(before, fresh)
        ss.undo = None if L.diff_is_empty(d) else d
    return result


def shift_week(days):
    ss.week = ss.week + timedelta(days=days)


def jump_week():
    ss.week = L.monday_of(ss.jump)


data = cached_load(store, ss.stamp)
week = ss.week
wk = week.isoformat()

# ------------------------------------------------------------------ header
st.title("KS Alliance Activity Logger")
if store_kind == "local":
    st.warning("The log is being saved to a file on this server, which is erased when the app restarts. "
               "Connect a Google Sheet (see the README) before real use.")
if ss.flash:
    st.success(ss.flash)
    ss.flash = None

nav = st.columns([1, 2, 1, 2])
nav[0].button("Previous week", on_click=shift_week, args=(-7,))
nav[1].subheader(L.week_label(week))
nav[2].button("Next week", on_click=shift_week, args=(7,))
nav[3].date_input("Jump to the week containing", value=week, key="jump", on_change=jump_week)

# ------------------------------------------------------------------ log attendance
with st.container(border=True):
    st.header("Log attendance")
    names = {e["id"]: e["name"] for e in data.events}
    names[MIGHT_OPT] = "Update might (power list)"
    names[ROSTER_OPT] = "Add to roster only (no check marks)"
    c1, c2 = st.columns(2)
    choice = c1.selectbox("Event", list(names), format_func=lambda o: names[o])
    source = c2.radio("What are you uploading?", ["Screenshots", "Excel / CSV", "Paste names"], horizontal=True)
    mode = L.MODE_MIGHT if choice == MIGHT_OPT else L.MODE_ROSTER if choice == ROSTER_OPT else L.MODE_ATTENDANCE
    if mode == L.MODE_ATTENDANCE:
        st.caption(f"Marks go to {names[choice]} for the week of {L.week_label(week)}.")
    elif mode == L.MODE_MIGHT:
        st.caption(f"Might is saved for the week of {L.week_label(week)}.")

    rows = None

    if source == "Screenshots":
        files = st.file_uploader("Screenshots", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True)
        o1, o2, o3 = st.columns(3)
        crop = o1.slider("Area to read, as % of image width", 0, 100, (0, 100),
                         help="Narrow this to just the name (and might) columns to avoid reading other text.")
        pick = "largest"
        if mode == L.MODE_MIGHT:
            pick = {"Largest number": "largest", "Left-most big number": "leftmost",
                    "Right-most big number": "rightmost"}[o2.selectbox("Which number is the might?",
                                                                      ["Largest number", "Left-most big number",
                                                                       "Right-most big number"])]
        ignore = [w for w in o3.text_input("Words to ignore, separated by commas").split(",")]
        if files:
            first = ocr.crop_width(ocr.load_image(files[0]), crop[0], crop[1])
            st.image(first, caption="Area that will be read (first screenshot)", width=320)
        if st.button("Read names", type="primary", disabled=not files):
            engine = get_engine()
            if engine[0] is None:
                st.error("No text reader is installed on this server. See the README.")
            else:
                rows, bar = [], st.progress(0.0, text="Reading screenshots...")
                for i, f in enumerate(files):
                    rows += ocr.read_image(engine, ocr.load_image(f), crop, mode, pick, ignore)
                    bar.progress((i + 1) / len(files), text=f"Read {i + 1} of {len(files)} screenshots")
                bar.empty()
                rows = L.dedupe_rows(rows)

    elif source == "Excel / CSV":
        f = st.file_uploader("Excel or CSV file", type=["xlsx", "csv"])
        xl_rows = None
        if f:
            try:
                tables = L.read_tables(f.getvalue(), f.name)
            except Exception:
                st.error("That file could not be opened. Check that it is a valid .xlsx or .csv file.")
                tables = {}
            if tables:
                sheet = st.selectbox("Sheet", list(tables)) if len(tables) > 1 else next(iter(tables))
                df = tables[sheet]
                kp = f"{f.name}-{sheet}"
                header = st.checkbox("First row is a header", value=L.detect_header(df), key=f"hdr-{kp}")
                gn, gm = L.guess_columns(df, header)
                sample_row = 1 if header and len(df) > 1 else 0
                labels = [f"Column {i + 1}: {str(df.iloc[sample_row, i])[:18]}" for i in range(df.shape[1])]
                x1, x2 = st.columns(2)
                nc = x1.selectbox("Player name column", range(df.shape[1]), index=gn,
                                  format_func=lambda i: labels[i], key=f"nc-{kp}-{header}")
                mc = None
                if mode == L.MODE_MIGHT:
                    opts = [None] + list(range(df.shape[1]))
                    mc = x2.selectbox("Might column", opts, index=(0 if gm is None else gm + 1),
                                      format_func=lambda i: "None" if i is None else labels[i], key=f"mc-{kp}-{header}")
                xl_rows = L.extract_rows(df, header, nc, mc)
        if st.button("Read names", type="primary", disabled=xl_rows is None):
            rows = xl_rows

    else:
        text = st.text_area("Names, one per line" if mode != L.MODE_MIGHT else "Names and might, one player per line",
                            placeholder="Wolf, 12345678\nIronFist 9,876,543" if mode == L.MODE_MIGHT
                            else "Paste the player names here", height=160)
        if st.button("Read names", type="primary", disabled=not text.strip()):
            rows = L.parse_paste(text, mode == L.MODE_MIGHT)

    if rows is not None:
        recs, seen = [], set()
        for r in rows:
            name = L.clean_name(r["name"])
            key = L.norm_key(name) if name else None
            if not name or key in seen:
                continue
            seen.add(key)
            stat = L.status_text(name, data.roster) + (" · check spelling" if r.get("unsure") else "")
            recs.append({"Include": True, "Name": name,
                         "Might": float(r["might"]) if r.get("might") is not None else float("nan"),
                         "Status": stat})
        ss.nonce += 1
        ss.staged = pd.DataFrame(recs, columns=["Include", "Name", "Might", "Status"]) if recs else None
        if not recs:
            st.warning("No player names were found. Check the file or narrow the area to read.")

    if ss.staged is not None:
        st.subheader("Check the names")
        st.caption("Fix any misread name, untick anyone who should not be counted, then confirm. "
                   "Add a row at the bottom for a name that was missed.")
        cfg = {
            "Include": st.column_config.CheckboxColumn("Include", default=True),
            "Name": st.column_config.TextColumn("Name", required=True),
            "Might": st.column_config.NumberColumn("Might", min_value=0, step=1, format="%d") if mode == L.MODE_MIGHT else None,
            "Status": st.column_config.TextColumn("Status"),
        }
        edited = st.data_editor(ss.staged, key=f"review-{ss.nonce}", hide_index=True, num_rows="dynamic",
                                column_config=cfg, disabled=["Status"])
        o1, o2 = st.columns(2)
        merge = o1.checkbox("Treat near matches as the existing player", value=True,
                            help="Fixes OCR slips like I versus l. Untick to add them as new players.")
        fresh = o2.checkbox("Start fresh: replace this event's marks for the week", value=False,
                            disabled=mode != L.MODE_ATTENDANCE)
        chosen = []
        for _, r in edited.iterrows():
            inc = True if pd.isna(r["Include"]) else bool(r["Include"])
            name = "" if pd.isna(r["Name"]) else str(r["Name"]).strip()
            if inc and name:
                chosen.append({"name": name, "might": None if pd.isna(r["Might"]) else int(r["Might"])})
        label = {L.MODE_ATTENDANCE: f"Mark {len(chosen)} for {names[choice]}",
                 L.MODE_MIGHT: f"Save might for {sum(1 for c in chosen if c['might'] is not None)} players",
                 L.MODE_ROSTER: f"Add {len(chosen)} to the roster"}[mode]
        b1, b2, _ = st.columns([1, 1, 3])
        if b1.button(label, type="primary", disabled=not chosen):
            res = apply(lambda d: L.commit_rows(d, chosen, mode, choice if mode == L.MODE_ATTENDANCE else None,
                                                wk, fresh, merge))
            ss.staged = None
            ss.flash = {L.MODE_ATTENDANCE: f"Marked {res['marked']} players for {names[choice]}.",
                        L.MODE_MIGHT: f"Saved might for {res['might_set']} players.",
                        L.MODE_ROSTER: "Roster updated."}[mode] + (
                f" {res['new_players']} new on the roster." if res["new_players"] else "")
            st.rerun()
        if b2.button("Discard"):
            ss.staged = None
            st.rerun()

    if ss.undo is not None and st.button("Undo the last change"):
        apply(lambda d: L.undo_diff(d, ss.undo), undoable=False)
        ss.undo = None
        ss.flash = "The last change was undone."
        st.rerun()

# ------------------------------------------------------------------ ledger
st.header("Week ledger")
t1, t2, t3 = st.columns([1, 2, 2])
view = t1.radio("View", ["This week", "All weeks"], horizontal=True, label_visibility="collapsed")
query = t2.text_input("Find a player", placeholder="Find a player", label_visibility="collapsed")
sort = SORTS[t3.selectbox("Sort players", list(SORTS), label_visibility="collapsed")]

if not data.roster:
    st.info("No players yet. Choose an event, upload a screenshot or an Excel file, and the names you confirm "
            "become the roster. To start from a full member list, pick “Add to roster only”, "
            "or “Update might” to load names and might together.")
elif view == "This week":
    df = L.build_ledger(data, wk, sort, query)
    ev_names = [e["name"] for e in data.events]
    cfg = {"pid": None,
           "Might": st.column_config.NumberColumn("Might", min_value=0, step=1, format="%d",
                                                  help="Type a value to set this player's might for this week."),
           "Might as of": st.column_config.TextColumn("Might as of", help="Shown when this week has no entry. "
                                                      "It is the week the value was last recorded.")}
    cfg.update({n: st.column_config.CheckboxColumn(n) for n in ev_names})
    edited = st.data_editor(df, key=f"ledger-{wk}-{sort}-{query}-{ss.stamp}", hide_index=True,
                            column_config=cfg,
                            disabled=["pid", "Player", "Might as of", "Total"],
                            height=min(35 * (len(df) + 1) + 3, 640))
    st.caption(f"{len(df)} players. Tick a box or type a might, then save. "
               "Weeks run Monday to Sunday.")
    if st.button("Save ledger changes"):
        n = apply(lambda d: L.apply_ledger_edits(d, df, edited, wk))
        ss.flash = f"Saved {n} change{'s' if n != 1 else ''}." if n else "There were no changes to save."
        st.rerun()
else:
    hist = L.build_history_table(data, sort, query)
    st.dataframe(hist, hide_index=True,
                 column_config={"Might": st.column_config.NumberColumn("Might", format="%d")},
                 height=min(35 * (len(hist) + 1) + 3, 640))
    st.caption("Counts are the number of weeks each player attended each event. Might is the latest recorded value.")

# ------------------------------------------------------------------ fixes and setup
with st.expander("Fix mistakes and manage the roster"):
    st.markdown("**Clear one event for the week shown**")
    f1, f2, f3 = st.columns([2, 1, 1])
    clr_ev = f1.selectbox("Event to clear", [e["id"] for e in data.events], format_func=lambda i: names.get(i, i),
                          key="clr_ev") if data.events else None
    ok1 = f2.checkbox("Yes, clear it", key="ok_clr")
    if f3.button("Clear marks", disabled=not (clr_ev and ok1)):
        n = apply(lambda d: L.clear_attendance(d, wk, clr_ev))
        ss.flash = f"Cleared {n} marks for {names[clr_ev]}."
        st.rerun()

    st.markdown("**Clear might for the week shown**")
    m1, m2 = st.columns([3, 1])
    ok2 = m1.checkbox("Yes, clear all might values recorded for this week", key="ok_clrm")
    if m2.button("Clear might", disabled=not ok2):
        n = apply(lambda d: L.clear_might(d, wk))
        ss.flash = f"Cleared {n} might values."
        st.rerun()

    st.markdown("**Add or remove players**")
    p1, p2 = st.columns([3, 1])
    new_player = p1.text_input("Add a player by name", key="new_player")
    if p2.button("Add player", disabled=not new_player.strip()):
        nm = L.clean_name(new_player)
        res = apply(lambda d: L.commit_rows(d, [{"name": nm}], L.MODE_ROSTER, None, wk, False, False))
        ss.flash = f"{nm} added to the roster." if res["new_players"] else f"{nm} is already on the roster."
        st.rerun()
    rm = st.multiselect("Remove players (this also deletes their marks and might)", list(data.roster),
                        format_func=lambda p: data.roster[p])
    ok3 = st.checkbox("Yes, remove them", key="ok_rm")
    if st.button("Remove selected players", disabled=not (rm and ok3)):
        apply(lambda d: L.remove_players(d, rm))
        ss.flash = f"Removed {len(rm)} players."
        st.rerun()

    st.markdown("**Events**")
    e1, e2 = st.columns([3, 1])
    new_event = e1.text_input("Add an event", key="new_event")
    if e2.button("Add event", disabled=not new_event.strip()):
        err = apply(lambda d: L.add_event(d, new_event), undoable=False)
        ss.flash = err or f"{new_event.strip()} added."
        st.rerun()
    rm_ev = st.multiselect("Remove events (past marks are kept in the history export)", [e["id"] for e in data.events],
                           format_func=lambda i: names.get(i, i))
    if st.button("Remove selected events", disabled=not rm_ev):
        apply(lambda d: [L.remove_event(d, i) for i in rm_ev], undoable=False)
        ss.flash = "Events removed."
        st.rerun()

with st.expander("Export, back up and restore"):
    d1, d2, d3 = st.columns(3)
    d1.download_button("Export week as CSV", L.week_csv(data, wk).encode("utf-8-sig"),
                       file_name=f"attendance-{wk}.csv", mime="text/csv")
    d2.download_button("Export all history (CSV)", L.history_csv(data).encode("utf-8-sig"),
                       file_name=f"attendance-history-{date.today().isoformat()}.csv", mime="text/csv",
                       )
    import json as _json
    d3.download_button("Back up (JSON)", _json.dumps(L.data_to_dict(data), ensure_ascii=False, indent=1),
                       file_name=f"alliance-attendance-backup-{date.today().isoformat()}.json",
                       mime="application/json")
    st.markdown("**Restore a backup** (also accepts the backup from the earlier web page version). "
                "This only adds missing players, marks and might values. Nothing is deleted or overwritten.")
    bf = st.file_uploader("Backup file", type=["json"], key="restore")
    if bf and st.button("Merge backup"):
        text = bf.getvalue().decode("utf-8", errors="replace")

        def _merge(d):
            merged, msg = L.merge_backup(d, text)
            if merged is None:
                return msg
            d.events, d.roster, d.attendance, d.might = merged.events, merged.roster, merged.attendance, merged.might
            return msg

        ss.flash = apply(_merge)
        st.rerun()
