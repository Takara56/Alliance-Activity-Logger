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

## Reading names more accurately

- The app uses RapidOCR 3 with the PP-OCRv5 models (the most accurate free reader), and falls back to older RapidOCR, then Tesseract. The line "Read with: ..." above the review table tells you which one ran.
- On the upload panel, pick **Name letters** (Latin for accents and Turkish names, Cyrillic for Russian) and tick **Sharpen and enlarge the image first** for small or blurry screenshots.
- **Corrections are remembered.** In the review table, "Read as" is what the reader saw and "Name" is what gets saved. If you fix a misread name, the app stores the misspelling in a new **Aliases** tab of your Google Sheet and fixes it automatically next time.
- Crop the left/right sliders to just the name column; it makes the biggest difference.

## Clearing everything

On the Manage tab, **Clear all players at once** (type DELETE) removes every player with their marks and might. **Undo the last change** brings them back.

## Player name changes (MightPulse)

1. Sign in with Discord at https://api.mightpulse.com/ and create a free API key.
2. Add to your Streamlit secrets: `mightpulse_key = "kss_..."` (optional: `kingdom = "2159"`, `alliance_tag = "KOR"`).
3. Manage tab > **Sync with MightPulse**. Players are matched by name the first time and tied to their game ID; after that, a rename is picked up on the next sync with all history kept.
4. Anyone not linked (renamed before the first sync) can be folded together with **Merge**. **Rename** fixes a name by hand.
Sync once a week before logging. The free key allows 60 requests a minute, and one sync is a single request.

## Screen layouts

In the upload panel, **Screen layout** picks the part of the screenshot to read. It is chosen for you:
- **Update might (power list)** uses the Alliance Ranking layout (names and power, skipping the header and the pinned bottom row).
- **Alliance Mobilization** uses its own layout (names only; the [KOR] tag, "Missions" lines and your pinned row are skipped).
- **Every other event reads the whole screenshot** with no crop. Add a layout for it once you have a sample screenshot.

## Officers and admin

Officers see only: pick the event, drop in the screenshots, check the names, confirm. Reading starts by itself as soon as the files are added, and names that are not on the roster are marked with a warning. Below that they see the week ledger and a "Made a mistake with an event?" box.

Everything else (roster, player names, merge and rename, events, clear all, exports and backups, reading options) is admin only. To turn it on, add to the app secrets:

    admin_password = "pick-something-different-from-app_password"

Then open the small arrow at the top left of the page, open **Admin**, and enter it. Until admin_password is set, the admin tools stay locked for everyone.

With `mightpulse_key` set, names and might are refreshed from MightPulse automatically (at most every 6 hours), so nobody has to press Sync.
