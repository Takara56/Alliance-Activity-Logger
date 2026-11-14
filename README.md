# KS Alliance Activity Logger (Streamlit)

Weekly attendance and might for a Kingshot alliance. Officers upload screenshots or Excel/CSV files, check the names
the app read, and confirm. Everything is free to run: the text reader (RapidOCR) works offline, hosting is Streamlit
Community Cloud, and the log is kept in a Google Sheet that officers can also open and read directly.

## What it does

- Event dropdown: Swordland Showdown, Alliance Mobilization, Alliance Championship, Viking's Vengeance,
  Tri-Alliance Clash, Sanctuary Battle, KvK. Add or remove events under "Fix mistakes and manage the roster".
- Three ways to load names: screenshots (text recognition), Excel / CSV, or pasted text.
- A review step before anything is saved. Misread names can be edited, and near matches to an existing player
  (for example `lronFist` for `IronFist`) are matched to that player instead of creating a duplicate.
- "Update might (power list)" saves each player's might for the selected week. A week with no entry shows the last
  known value and the week it came from.
- The week ledger: players down the side, events across the top, check boxes, might, and a total. Sort by player name,
  attendance, or might. "All weeks" shows how many weeks each player attended each event.
- Undo the last change, clear one event for a week, clear might for a week, remove players.
- CSV export of one week or the whole history, a JSON backup, and a restore that only adds missing data.
  Restore also accepts the backup file from the earlier web page version, so you can carry your data over.

## One-time setup

### 1. Put the code on GitHub
Create a free GitHub account and a new repository (private is fine). Upload everything in this folder
(`app.py`, `logic.py`, `ocr.py`, `store.py`, `requirements.txt`, `packages.txt`, `README.md`). Do not upload `data.json`
or any key file.

### 2. Make the Google Sheet that stores the log
1. Create an empty Google Sheet. Copy its ID from the address bar:
   `https://docs.google.com/spreadsheets/d/THIS_PART_IS_THE_ID/edit`.
2. Go to console.cloud.google.com, create a project, and turn on the **Google Sheets API** and **Google Drive API**.
3. Under IAM & Admin, Service Accounts, create a service account. Open it, go to Keys, and add a JSON key.
   A file downloads. Keep it private.
4. In the JSON file, find `client_email`. Share your Google Sheet with that address as an **Editor**.
5. The app creates its tabs (Events, Roster, Attendance, Might) the first time it runs. Share the sheet with
   officers as Viewers if you want them to read it. Only the app should edit it.

### 3. Deploy on Streamlit Community Cloud
1. Sign in at share.streamlit.io with GitHub and choose **New app**. Pick your repository and `app.py`.
2. Open **Advanced settings**. Choose Python 3.11 if offered, because the text reader's packages are most reliable there.
3. Paste this into **Secrets**, filling in your own values from the JSON key file:

```toml
app_password = "choose-a-password-for-your-officers"
sheet_id = "THE_SHEET_ID"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "...@....iam.gserviceaccount.com"
client_id = "..."
token_uri = "https://oauth2.googleapis.com/token"
```

4. Deploy. The first start takes a few minutes while packages install. Send officers the app link and the password.

If `app_password` is left out, anyone with the link can use the app. If the Google settings are left out, the app
saves to a local file that is erased when the app restarts, and it shows a warning.

## Running it on your own computer
```
pip install -r requirements.txt
streamlit run app.py
```
Tesseract (`tesseract-ocr`) is only needed if RapidOCR cannot be installed.

## Reading screenshots well
- Use full-size, uncropped screenshots. Very tall ones are cut into overlapping sections automatically.
- Use "Area to read" to keep only the name (and might) columns. The preview shows what will be read.
- In might mode, "Which number is the might?" chooses between several number columns.
- Use "Words to ignore" for headers or buttons that keep showing up as names.
- Always check the review table. Names with unusual symbols or non-Latin letters are the most likely to need fixing.
- The text reader was not tuned on real Kingshot screens. Expect to adjust the area to read on the first few uploads.

## Notes
- Two officers can use it at once. Each change reloads the latest log first, so one officer does not overwrite another.
  Two people changing the same cell in the same moment is the only way to lose an edit.
- Google Sheets allows about 60 reads a minute per user, which is plenty for a small team.
- Streamlit Community Cloud apps go to sleep after a few days without visitors. Opening the link wakes them.
- Take a JSON backup after each week's uploads.

## Troubleshooting
- `ImportError: libGL.so.1`: make sure `packages.txt` was uploaded with `libgl1` in it.
- No text reader found: check the app logs for errors installing `rapidocr-onnxruntime`. Tesseract is the fallback and
  is installed from `packages.txt`.
- "The caller does not have permission" or a 403: the Google Sheet was not shared with the service account's
  `client_email` as an Editor.
- Tests for the logic: `python -m unittest discover -s tests`.
