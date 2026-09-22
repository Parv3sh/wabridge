"""`wabridge` command-line interface.

    wabridge doctor
    wabridge migrate --key <64 digits> [--work ./wabridge-work] [--no-media]
    wabridge android pull | decrypt --key … | inspect
    wabridge ios info | disable-encryption --password … | backup | restore [--system] | rollback
    wabridge convert [--contacts export.vcf]
    wabridge inject
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__, pipeline

DEFAULT_WORK = "./wabridge-work"


def _add_work(p: argparse.ArgumentParser) -> None:
    p.add_argument("--work", default=DEFAULT_WORK, help=f"work directory (default {DEFAULT_WORK})")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wabridge",
                                description="Free, open-source Android → iOS WhatsApp migration.")
    p.add_argument("--version", action="version", version=f"wabridge {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check adb, pymobiledevice3 and connected devices")

    wz = sub.add_parser("wizard", help="guided end-to-end migration; stops only when you must tap a phone")
    _add_work(wz)

    m = sub.add_parser("migrate", help="run every step end to end")
    _add_work(m)
    m.add_argument("--key", required=True, help="64-digit WhatsApp backup key (spaces allowed)")
    m.add_argument("--serial", help="Android device serial (if several)")
    m.add_argument("--udid", help="iPhone UDID (if several)")
    m.add_argument("--no-media", action="store_true", help="migrate text only")
    m.add_argument("--contacts", help="contacts .vcf export for name resolution")
    m.add_argument("--system", action="store_true", help="pass --system to the iOS restore")

    a = sub.add_parser("android", help="Android-side steps").add_subparsers(dest="sub", required=True)
    ap = a.add_parser("pull", help="pull WhatsApp databases (and media) over ADB")
    _add_work(ap)
    ap.add_argument("--serial")
    ap.add_argument("--no-media", action="store_true")
    ap.add_argument("--business", action="store_true", help="WhatsApp Business")
    ad = a.add_parser("decrypt", help="decrypt msgstore.db.crypt15")
    _add_work(ad)
    ad.add_argument("--key", required=True)
    ad.add_argument("--file", help="specific .crypt15 file (default: newest pulled)")
    ad.add_argument("--force", action="store_true", help="skip GCM authentication (last resort)")
    ai = a.add_parser("inspect", help="summarise the decrypted database")
    _add_work(ai)
    ai.add_argument("--contacts")

    i = sub.add_parser("ios", help="iPhone-side steps").add_subparsers(dest="sub", required=True)
    ii = i.add_parser("info", help="show the connected iPhone")
    ii.add_argument("--udid")
    ie = i.add_parser("disable-encryption", help="turn off encrypted backups")
    _add_work(ie)
    ie.add_argument("--password", required=True, help="current backup password")
    ie.add_argument("--udid")
    ib = i.add_parser("backup", help="make a full unencrypted backup")
    _add_work(ib)
    ib.add_argument("--udid")
    ir = i.add_parser("restore", help="restore the modified backup")
    _add_work(ir)
    ir.add_argument("--udid")
    ir.add_argument("--system", action="store_true")
    rb = i.add_parser("rollback", help="restore the untouched pre-migration backup")
    _add_work(rb)
    rb.add_argument("--udid")
    rb.add_argument("--system", action="store_true")

    c = sub.add_parser("convert", help="write Android chats into the backup's ChatStorage.sqlite")
    _add_work(c)
    c.add_argument("--backup", help="backup folder (default: the one made by `ios backup`)")
    c.add_argument("--contacts")
    c.add_argument("--no-media", action="store_true")
    c.add_argument("--include-system", action="store_true", help="also import group event messages")
    c.add_argument("--skip-archived", action="store_true")

    j = sub.add_parser("inject", help="put ChatStorage.sqlite + media into the backup")
    _add_work(j)
    j.add_argument("--backup")
    return p


def cmd_doctor() -> int:
    from .android import adb
    from .ios import device

    ok = True
    print("wabridge", __version__)
    try:
        print("adb:", adb.find_adb())
        devs = adb.devices()
        print("  android devices:", ", ".join(f"{d.serial} ({d.state})" for d in devs) or "none")
    except adb.AdbError as e:
        ok = False
        print("adb: MISSING —", e)
    try:
        import pymobiledevice3  # type: ignore  # noqa: F401
        print("pymobiledevice3: installed")
        try:
            d = device.info()
            print(f"  iPhone: {d.name} iOS {d.ios_version} udid={d.udid} encrypted_backups={d.will_encrypt}")
        except device.DeviceError as e:
            print("  iPhone:", e)
    except ImportError:
        ok = False
        print("pymobiledevice3: MISSING — pip install 'wabridge[ios]'")
    try:
        import cryptography  # noqa: F401
        print("cryptography: installed")
    except ImportError:
        ok = False
        print("cryptography: MISSING — pip install cryptography")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.cmd == "doctor":
            return cmd_doctor()
        work = pipeline.Work(getattr(args, "work", DEFAULT_WORK))
        if args.cmd == "wizard":
            from . import wizard

            log_path = work.path("wizard.log")
            log_fh = open(log_path, "a", encoding="utf-8")   # noqa: SIM115 — lives for the session

            def out(line: str = "") -> None:
                print(line)
                log_fh.write(line + "\n")
                log_fh.flush()

            try:
                return wizard.run(work, out=out)
            finally:
                log_fh.close()
        if args.cmd == "migrate":
            pipeline.migrate(work, key=args.key, serial=args.serial, udid=args.udid,
                             include_media=not args.no_media, contacts_vcf=args.contacts, system=args.system)
        elif args.cmd == "android":
            if args.sub == "pull":
                pipeline.android_pull(work, serial=args.serial, include_media=not args.no_media,
                                      business=args.business)
            elif args.sub == "decrypt":
                pipeline.android_decrypt(work, args.key, crypt_file=args.file, force=args.force)
            elif args.sub == "inspect":
                archive = pipeline.load_archive(work, contacts_vcf=args.contacts, include_system=True)
                st = archive.stats()
                print(f"{st['chats']} chats ({st['groups']} groups), {st['messages']} messages, "
                      f"{st['media']} with media")
                for chat in sorted(archive.chats, key=lambda c: -len(c.messages))[:40]:
                    print(f"  {len(chat.messages):>6}  {'[group] ' if chat.is_group else ''}"
                          f"{chat.name or chat.jid}")
        elif args.cmd == "ios":
            from .ios import device
            if args.sub == "info":
                d = device.info(args.udid)
                print(f"{d.name}  iOS {d.ios_version}  UDID {d.udid}  encrypted backups: {d.will_encrypt}")
            elif args.sub == "disable-encryption":
                device.disable_encryption(work.backup_root, args.password, args.udid)
                print("Backup encryption disabled.")
            elif args.sub == "backup":
                pipeline.ios_backup(work, udid=args.udid)
            elif args.sub == "restore":
                pipeline.ios_restore(work, udid=args.udid, system=args.system)
            elif args.sub == "rollback":
                pipeline.ios_rollback(work, udid=args.udid, system=args.system)
        elif args.cmd == "convert":
            pipeline.convert(work, backup_folder=args.backup, contacts_vcf=args.contacts,
                             include_media=not args.no_media, include_system=args.include_system,
                             skip_archived=args.skip_archived)
        elif args.cmd == "inject":
            pipeline.inject(work, backup_folder=args.backup)
        return 0
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as e:  # surfaced as a clean one-liner; -v shows the traceback
        if args.verbose:
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
