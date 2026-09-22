# WaBridge user guide

This walks through one migration from start to finish. Budget about an hour, most of it
waiting on copies. You need a computer (Windows, macOS or Linux), one USB cable for each phone,
and both phones charged.

**Before you start: your Android chats stay on the Android phone. Nothing here deletes them.**
Still, if you rely on these chats, make a Google Drive backup in WhatsApp first.

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
4. Unlock the iPhone and tap **Trust** when the computer asks.

## Part 3 — Run WaBridge

Install Python 3.10+ and the Android platform-tools, then:

```bash
pip install "wabridge[ios]"
wabridge doctor
```

`doctor` must show `adb`, `pymobiledevice3`, one Android device and one iPhone. Then:

```bash
wabridge migrate --key "PASTE THE 64 DIGITS HERE"
```

What you'll see:

1. Pulling databases and media from Android (minutes to an hour depending on media size; add `--no-media` for text only).
2. Decrypting.
3. Backing up the iPhone (keep it unlocked and plugged in).
4. Converting and injecting.
5. Restoring. **The iPhone reboots. Do not unplug it until it shows the lock screen.**

After the reboot, open WhatsApp on the iPhone. Group chats appear straight away. Individual chats sometimes appear in the list only after the next message is exchanged with that person; the history is there.

## If something goes wrong

* `No .crypt15 backup on the phone` — you skipped 1.1/1.2. End-to-end encrypted backup must be on **and** a backup written afterwards.
* `Could not decrypt` — the key doesn't belong to this backup. Generate a new key (1.1) and back up again (1.2).
* `This backup is encrypted` — untick *Encrypt local backup* (Part 2 step 3) and run `wabridge ios backup` again.
* `WhatsApp data is not in this backup` — register WhatsApp on the iPhone and send a message first.
* The restore finished but WhatsApp shows nothing — run `wabridge ios restore --system`. Some iOS versions only restore app data with that flag. To put the iPhone back exactly how it was before, run `wabridge ios rollback` — WaBridge keeps the untouched first backup in `wabridge-work/ios_backup_pristine/` and never overwrites it.
* Names show as phone numbers — export your Android contacts (Contacts app → Fix & manage → Export to file) and run `wabridge convert --contacts contacts.vcf` then `wabridge inject` and `wabridge ios restore` again. Also grant WhatsApp on the iPhone access to Contacts.

Every step can be re-run on its own. `wabridge --help` lists them.

When you're done and happy, delete the `wabridge-work` folder: it contains your entire decrypted chat history.
