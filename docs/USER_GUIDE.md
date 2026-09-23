# WaBridge user guide

This walks through one migration from start to finish. Budget about an hour, most of it
waiting on copies. You need a computer (Windows, macOS or Linux), one USB cable for each phone,
and both phones charged.

**Before you start: your Android chats stay on the Android phone. Nothing here deletes them.**
Still, if you rely on these chats, make a Google Drive backup in WhatsApp first.

**What works today (verified on real phones, September 2026):** text messages and group chats
arrive on the iPhone with the right names and dates. **Photos, videos and voice notes do not open
yet** — they show as placeholders. Run a chats-only migration; media support is being worked on.

## Part 1 — Android phone

### 1.1 Get your 64-digit key

WhatsApp encrypts its backups. You need the key it uses.

1. Open WhatsApp → ⋮ → **Settings** → **Chats** → **Chat backup** → **End-to-end encrypted backup**.
2. Tap **Turn on**. If WhatsApp offers a *passkey* or *password* first, look for **More options** → **Use 64-digit encryption key instead**.
3. Tap **Generate your 64-digit key**, then **long-press the key to copy it**. Paste it somewhere safe (a note on your computer). WhatsApp shows it only once.
4. Tap **Continue** → **Create**.

If end-to-end encrypted backup was *already* on with a password: Settings → Chats → Chat backup → End-to-end encrypted backup → **Change password** → *I lost my encryption key* → follow the prompts to generate a fresh key. Then continue below so a new backup is written with it.

### 1.2 Write a fresh local backup

Settings → Chats → Chat backup → tap the green **Back up** button. Wait for it to finish. (If Google Drive backup is enabled it will also upload; that's fine.)

### 1.3 Enable USB debugging

Settings → About phone → tap **Build number** seven times → back → **Developer options** → turn on **USB debugging**. Plug the phone into the computer and tap **Allow** on the phone when asked.

## Part 2 — iPhone

1. Install WhatsApp from the App Store and register with the **same phone number** you use on Android. (WhatsApp will warn that the number is in use elsewhere; that's expected. The Android phone stops receiving messages once the iPhone is registered, but its history is untouched.)
2. Send **one message** to anyone so WhatsApp creates its database.
3. Turn off backup encryption: connect the iPhone to the computer, open **Finder** (macOS) or **iTunes / Apple Devices** (Windows), select the iPhone, and untick **Encrypt local backup**. Enter your backup password when asked. (Or use `wabridge ios disable-encryption --password …`.)
4. Turn off **Find My iPhone** for the duration: Settings → your name → Find My → Find My iPhone → off (Apple ID password). Apple refuses to restore any backup while it is on. Turn it back on afterwards. If the switch is greyed out: Settings → Screen Time → Content & Privacy Restrictions → Location Services → *Allow changes*.
5. Unlock the iPhone and tap **Trust** when the computer asks.

## Part 3 — Run WaBridge

WaBridge is not on PyPI yet. Download the code and use the zero-setup launcher, which installs
Python, `adb` and WaBridge itself into the project folder and starts the guided wizard:

```bash
git clone https://github.com/parvesh-rm/wabridge.git   # or download the ZIP from GitHub and unzip it
cd wabridge
bash start.sh          # Windows: start.bat
```

The wizard asks for the key, waits for each phone and runs every step below. If you prefer the
plain command line, the same setup gives you the `wabridge` command in `.venv`:

```bash
.venv/bin/wabridge doctor        # Windows: .venv\Scripts\wabridge doctor
```

`doctor` must show `adb`, `pymobiledevice3`, one Android device and one iPhone. Then:

```bash
.venv/bin/wabridge migrate --no-media --key "PASTE THE 64 DIGITS HERE"
```

`--no-media` is the verified path (see the top of this guide); leave it off only if you want to
help test media.

What you'll see:

1. Pulling the databases (and, without `--no-media`, all media — minutes to an hour) from Android.
2. Decrypting.
3. Backing up the iPhone (keep it unlocked and plugged in).
4. Converting and injecting.
5. Restoring. **The iPhone reboots. Do not unplug it until it shows the lock screen.**

After the reboot, open WhatsApp on the iPhone. Group chats appear straight away. Individual chats sometimes appear in the list only after the next message is exchanged with that person; the history is there.

## If something goes wrong

* `No .crypt15 backup on the phone` — you skipped 1.1/1.2. End-to-end encrypted backup must be on **and** a backup written afterwards.
* `Could not decrypt` — the key doesn't belong to this backup. Generate a new key (1.1) and back up again (1.2).
* `This backup is encrypted` — untick *Encrypt local backup* (Part 2 step 3) and run `wabridge ios backup` again.
* `WhatsApp data is not in this backup` — register WhatsApp on the iPhone and send a message first, then back up again.
* `MBErrorDomain/211` or `Apple refuses to restore while Find My iPhone is on` — Part 2 step 4.
* The restore finished but WhatsApp shows nothing — run `wabridge ios restore --system`. Some iOS versions only restore app data with that flag. To put the iPhone back exactly how it was before, run `wabridge ios rollback` — WaBridge keeps the untouched first backup in `wabridge-work/ios_backup_pristine/` and never overwrites it.
* Names show as phone numbers — export your Android contacts (Contacts app → Fix & manage → Export to file) and run `wabridge convert --contacts contacts.vcf` then `wabridge inject` and `wabridge ios restore` again. Also grant WhatsApp on the iPhone access to Contacts.

Every step can be re-run on its own. `wabridge --help` lists them.

When you're done and happy, delete the `wabridge-work` folder: it contains your entire decrypted chat history.
