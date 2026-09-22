"""WaBridge: free, open-source Android -> iOS WhatsApp migration.

Pipeline overview (see DESIGN.md):

    Android phone ──adb pull──▶ msgstore.db.crypt15 + Media/
                               │ 64-digit key (crypt15.decrypt)
                               ▼
                           msgstore.db ──android.msgstore.parse──▶ intermediate model
                                                                        │
    iPhone ──pymobiledevice3 backup──▶ unencrypted backup dir           │
                                          │ ios.backup.extract          │
                                          ▼                             ▼
                                   ChatStorage.sqlite ◀── ios.chatstorage.Writer ──
                                          │ ios.backup.inject (Manifest.db rows + media blobs)
                                          ▼
    iPhone ◀──pymobiledevice3 restore── modified backup dir
"""

__version__ = "0.1.0a1"
