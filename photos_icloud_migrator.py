#!/usr/bin/env python3
"""
photos-icloud-migrator
Author: Antigravity and junoonx
Description: Production-grade, standalone Apple Photos & iCloud migration suite for
             Google Photos Takeout and OneDrive archives.
"""

import os
import sys
import time
import json
import sqlite3
import argparse
import datetime
import subprocess
import shutil
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from core.schema_adapter import PhotosSchemaAdapter
from core.permissions_checker import is_accessibility_trusted, verify_permissions, open_accessibility_preferences, request_accessibility_permission
from core.live_photo_synthesizer import LivePhotoSynthesizer

class PhotosICloudMigrator:
    def __init__(self, source_dir=None, library_db=None, state_db=None, 
                 batch_size=100, delay_sec=1, auto_yes=False, interactive=False):
        self.repo_dir = os.path.dirname(os.path.abspath(__file__))
        self.batch_size = batch_size
        self.delay_sec = delay_sec
        self.auto_yes = auto_yes
        self.media_extensions = (
            '.jpg', '.jpeg', '.png', '.heic', '.webp', '.mov', '.mp4', 
            '.m4v', '.tiff', '.tif', '.dng', '.avi', '.gif', '.cr2', 
            '.bmp', '.raw', '.nef', '.arw'
        )
        
        # 1. Resolve Exiftool Binary
        self.exiftool_path = self._resolve_exiftool()
        
        # 2. Resolve Paths (CLI arguments or interactive Finder Pickers)
        self.source_dir, self.library_db, self.state_db = self._resolve_paths(source_dir, library_db, state_db, interactive)
        
        # 3. Initialize Schema Adapter & Database
        self.schema_adapter = PhotosSchemaAdapter(self.library_db)
        self.live_photo_synth = LivePhotoSynthesizer(self.exiftool_path)
        self.init_state_database()
        
        # 4. Watchdog state
        self._watchdog_active = False

    def _resolve_exiftool(self):
        """Resolves exiftool binary location with strict fatal exit guard."""
        # 1. PyInstaller standalone bundle path
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            bundle_bin = os.path.join(sys._MEIPASS, "bin", "exiftool")
            if os.path.exists(bundle_bin) and os.access(bundle_bin, os.X_OK):
                return bundle_bin

        # 2. Local repo directory path
        bundled = os.path.join(self.repo_dir, "bin", "exiftool")
        if os.path.exists(bundled) and os.access(bundled, os.X_OK):
            return bundled
            
        sys_which = shutil.which("exiftool")
        if sys_which:
            return sys_which
            
        if os.path.exists("/usr/local/bin/exiftool"):
            return "/usr/local/bin/exiftool"
            
        print("\n" + "!"*60)
        print(" [FATAL ERROR] Required dependency 'exiftool' is missing.")
        print(" Please ensure './bin/exiftool' is present or install via: brew install exiftool")
        print("!"*60 + "\n")
        sys.exit(1)

    def _prompt_finder_folder(self, prompt_text):
        """Prompts the user with a native macOS Finder folder selection dialog."""
        script = f'POSIX path of (choose folder with prompt "{prompt_text}")'
        res = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().rstrip('/')
        return None

    def _prompt_finder_library(self, prompt_text):
        """Prompts the user to select their Apple Photos Library (.photoslibrary package or folder)."""
        script = f'''
        try
            set picked to (choose file with prompt "{prompt_text}" of type {{"com.apple.photos.library", "photoslibrary", "package", "public.item"}})
            return POSIX path of picked
        on error
            try
                set picked to (choose file with prompt "{prompt_text}")
                return POSIX path of picked
            on error
                try
                    set picked to (choose folder with prompt "{prompt_text}")
                    return POSIX path of picked
                on error
                    return ""
                end try
            end try
        end try
        '''
        res = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().rstrip('/')
        return None

    def _load_config(self):
        """Loads previously selected paths from .migrator_config.json."""
        config_path = os.path.join(self.repo_dir, ".migrator_config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_config(self):
        """Saves current selected paths to .migrator_config.json for seamless resumption."""
        config_path = os.path.join(self.repo_dir, ".migrator_config.json")
        try:
            with open(config_path, "w") as f:
                json.dump({
                    "source_dir": self.source_dir,
                    "library_db": self.library_db,
                    "state_db": self.state_db
                }, f, indent=2)
        except Exception:
            pass

    def _resolve_paths(self, source_dir, library_db, state_db, force_interactive):
        """Resolves source and target paths via arguments, saved config, or Finder pickers."""
        saved_config = self._load_config()
        
        # Default fallbacks
        default_source = saved_config.get("source_dir") or os.path.expanduser("~/Pictures/Takeout")
        default_library = saved_config.get("library_db") or os.path.expanduser("~/Pictures/Photos Library.photoslibrary/database/Photos.sqlite")
        
        # Source Directory
        if force_interactive and not source_dir and not saved_config.get("source_dir"):
            print("\nOpening Finder to select Source Media Folder...")
            picked_source = self._prompt_finder_folder("Select Source Media Folder (Google Takeout / OneDrive)")
            source_dir = picked_source or default_source
        elif not source_dir:
            if os.path.exists(default_source):
                source_dir = default_source
            else:
                found_src = None
                if os.path.exists("/Volumes"):
                    for vol in os.listdir("/Volumes"):
                        candidate = os.path.join("/Volumes", vol, "Takeout")
                        if os.path.exists(candidate):
                            found_src = candidate
                            break
                source_dir = found_src or self._prompt_finder_folder("Select Source Media Folder") or default_source
            
        source_dir = os.path.abspath(source_dir)

        # Library Path
        if force_interactive and not library_db and not saved_config.get("library_db"):
            print("\nOpening Finder to select Apple Photos Library...")
            picked_lib = self._prompt_finder_library("Select your Apple Photos Library (.photoslibrary)")
            if picked_lib:
                if picked_lib.endswith('.sqlite'):
                    library_db = picked_lib
                elif picked_lib.endswith('.photoslibrary'):
                    library_db = os.path.join(picked_lib, "database/Photos.sqlite")
                elif os.path.isdir(picked_lib):
                    found_lib = None
                    for item in os.listdir(picked_lib):
                        if item.endswith('.photoslibrary'):
                            found_lib = os.path.join(picked_lib, item, "database/Photos.sqlite")
                            break
                    library_db = found_lib or os.path.join(picked_lib, "Photos Library.photoslibrary/database/Photos.sqlite")
                else:
                    library_db = os.path.join(picked_lib, "database/Photos.sqlite")
            else:
                library_db = default_library
        elif not library_db:
            if os.path.exists(default_library):
                library_db = default_library
            else:
                found_lib = None
                if os.path.exists("/Volumes"):
                    for vol in os.listdir("/Volumes"):
                        candidate = os.path.join("/Volumes", vol, "Photos Library.photoslibrary/database/Photos.sqlite")
                        if os.path.exists(candidate):
                            found_lib = candidate
                            break
                if found_lib:
                    library_db = found_lib
                else:
                    picked = self._prompt_finder_library("Select Apple Photos Library (.photoslibrary)")
                    if picked:
                        library_db = os.path.join(picked, "database/Photos.sqlite") if picked.endswith('.photoslibrary') else picked
                    else:
                        library_db = default_library

        # Normalize library_db path if user passed a .photoslibrary directory
        if library_db.endswith('.photoslibrary'):
            library_db = os.path.join(library_db, "database/Photos.sqlite")

        library_db = os.path.abspath(library_db)
        
        # State DB
        if not state_db:
            state_db = saved_config.get("state_db") or os.path.join(source_dir, "batchimport_sqlite.db")
        state_db = os.path.abspath(state_db)

        # Persist resolved paths
        try:
            config_path = os.path.join(self.repo_dir, ".migrator_config.json")
            with open(config_path, "w") as f:
                json.dump({
                    "source_dir": source_dir,
                    "library_db": library_db,
                    "state_db": state_db
                }, f, indent=2)
        except Exception:
            pass

        return source_dir, library_db, state_db

    def init_state_database(self):
        os.makedirs(os.path.dirname(self.state_db), exist_ok=True)
        conn = sqlite3.connect(self.state_db)
        cursor = conn.cursor()
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS migration_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE,
            file_name TEXT,
            file_size INTEGER,
            file_format TEXT,
            exif_date_status TEXT,
            exif_timestamp TEXT,
            has_gps INTEGER DEFAULT 0,
            batch_number INTEGER DEFAULT 0,
            status TEXT, -- 'PENDING', 'IMPORTED', 'ALREADY_EXISTS', 'FAILED_QUARANTINED'
            icloud_sync_status TEXT DEFAULT 'PENDING_UPLOAD', -- 'PENDING_UPLOAD', 'SYNCED_TO_ICLOUD', 'LOCAL_ONLY'
            error_message TEXT,
            attempt_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        ''')
        
        cursor.execute("PRAGMA table_info(migration_files);")
        cols = [c[1] for c in cursor.fetchall()]
        if 'icloud_sync_status' not in cols:
            cursor.execute("ALTER TABLE migration_files ADD COLUMN icloud_sync_status TEXT DEFAULT 'PENDING_UPLOAD';")

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS migration_batches (
            batch_number INTEGER PRIMARY KEY,
            total_files INTEGER,
            verified_imported INTEGER,
            failed_files INTEGER,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            status TEXT
        );
        ''')
        conn.commit()
        conn.close()

    def dismiss_photos_modal_dialogs(self):
        """Dismisses any lingering modal alert dialogs (Cannot Import, Duplicate prompts) in Photos.app."""
        dismiss_script = '''
        tell application "System Events"
            if exists (process "Photos") then
                tell process "Photos"
                    try
                        repeat with w in (every window)
                            try
                                repeat with sh in (every sheet of w)
                                    try
                                        if exists (button "OK" of sh) then
                                            click button "OK" of sh
                                        else if exists (button "Don't Import" of sh) then
                                            try
                                                if exists (checkbox "Apply to all duplicates" of sh) then
                                                    click checkbox "Apply to all duplicates" of sh
                                                end if
                                            end try
                                            click button "Don't Import" of sh
                                        else if exists (button "Cancel" of sh) then
                                            click button "Cancel" of sh
                                        end if
                                    end try
                                end repeat
                            end try
                            try
                                if exists (button "OK" of w) then
                                    click button "OK" of w
                                else if exists (button "Don't Import" of w) then
                                    try
                                        if exists (checkbox "Apply to all duplicates" of w) then
                                            click checkbox "Apply to all duplicates" of w
                                        end if
                                    end try
                                    click button "Don't Import" of w
                                else if exists (button "Cancel" of w) then
                                    click button "Cancel" of w
                                end if
                            end try
                        end repeat
                    end try
                end tell
            end if
        end tell
        '''
        try:
            subprocess.run(['osascript', '-e', dismiss_script], capture_output=True, text=True)
        except Exception:
            pass

    def _start_watchdog_thread(self):
        """Starts asynchronous background thread to continuously guard against modal dialogs."""
        self._watchdog_active = True
        def _watchdog_loop():
            while self._watchdog_active:
                self.dismiss_photos_modal_dialogs()
                time.sleep(1.0)
        self._watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True)
        self._watchdog_thread.start()

    def _stop_watchdog_thread(self):
        self._watchdog_active = False

    def check_readiness_and_briefing(self):
        print("\n" + "="*65)
        print("  photos-icloud-migrator: PRE-FLIGHT")
        print("="*65)
        
        # 1. Source Directory Check
        if not os.path.exists(self.source_dir):
            print(f"[FAIL] Source directory does not exist: {self.source_dir}")
            return False
        print(f"[PASS] Source Media Directory   : {self.source_dir}")
        
        # 2. Photos APFS Database Check
        if not os.path.exists(self.library_db):
            print(f"[FAIL] Photos Library database not found: {self.library_db}")
            return False
        print(f"[PASS] Apple Photos Database    : {self.library_db}")
        
        # 3. Display Library Content via Schema Adapter
        lib_stats = self.schema_adapter.get_library_counts()
        if lib_stats:
            tot_p = lib_stats.get('total_photos', 0)
            tot_v = lib_stats.get('total_videos', 0)
            print(f"       -> Total Assets in Photos : {tot_p:,} Photos, {tot_v:,} Videos")
            c_p = lib_stats.get('cloud_photos')
            c_v = lib_stats.get('cloud_videos')
            if c_p is not None and c_v is not None:
                print(f"       -> Synced to iCloud Photos: {c_p:,} Photos, {c_v:,} Videos")

        # 4. Exiftool Binary Check
        print(f"[PASS] Bundled EXIF Engine      : {self.exiftool_path}")

        # 5. Accessibility Check
        access_ok, access_msg = verify_permissions()
        if access_ok:
            print("[PASS] macOS Accessibility Trust : Granted (Modal Watchdog Active)")
        else:
            print(f"[WARN] macOS Accessibility Trust : Missing (Auto-dismissal limited)")
            print("       -> Required for the automated watchdog to auto-dismiss 'Cannot Import' modal dialogs.")
            if not self.auto_yes:
                try:
                    ans = input("\n[ACTION REQUIRED] Open macOS Accessibility Settings now to grant permission? [Y/n]: ").strip().lower()
                    if ans in ('', 'y', 'yes'):
                        print("\nOpening macOS System Settings -> Privacy & Security -> Accessibility...")
                        request_accessibility_permission()
                        print("👉 Please toggle the switch ON for 'Terminal' (or your terminal application), then return here.\n")
                        input("Press [Enter] after enabling Accessibility in System Settings to re-verify... ")
                        access_ok, _ = verify_permissions()
                        if not access_ok:
                            print("\nRefreshing process environment with macOS to apply updated permissions...")
                            self._save_config()
                            time.sleep(1.0)
                            # Re-execute script/binary so fresh process inherits new TCC security token
                            if getattr(sys, 'frozen', False):
                                os.execv(sys.executable, sys.argv)
                            else:
                                os.execv(sys.executable, [sys.executable] + sys.argv)
                        else:
                            print("[PASS] macOS Accessibility Trust : Granted (Modal Watchdog Active)")
                except KeyboardInterrupt:
                    print("\nMigration aborted by user.")
                    sys.exit(0)
        
        # 6. Photos.app bridge check
        subprocess.run(['osascript', '-e', 'tell application "Photos" to activate'], capture_output=True, text=True)
        time.sleep(1.5)
        res = subprocess.run(['osascript', '-e', 'tell application "Photos" to return name'], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[WARN] Photos.app bridge check returned: {res.stderr.strip()}")
        else:
            print("[PASS] Apple Photos App Bridge  : Operational")
            
        print("="*65)
        
        # Operational Policy Briefing
        failed_dir = os.path.join(self.source_dir, "Failed")
        print("\n" + "-"*65)
        print("  CONSTRAINTS")
        print("-"*65)
        print(f"• Failed Items Quarantine : Unimportable files moved to: {failed_dir}")
        print(f"• Pre-Import Deduplication: 0 duplicate prompts (pre-filtered against SQLite)")
        print(f"• CloudKit Telemetry      : Monitored live via Photos.sqlite sync states")
        print(f"• Tracking Database Path  : {self.state_db}")
        print("-"*65 + "\n")

        if not self.auto_yes:
            try:
                input("Press [Enter] to acknowledge and start migration (or Ctrl+C to abort)... ")
            except KeyboardInterrupt:
                print("\nMigration aborted by user.")
                sys.exit(0)

        print("\nStarting execution pipeline...\n")
        return True

    def find_json_sidecar(self, fname):
        dir_name = os.path.dirname(fname)
        base_name = os.path.basename(fname)
        candidates = [
            os.path.join(dir_name, base_name + '.json'),
            os.path.join(dir_name, base_name + '.supplemental-metadata.json')
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        prefix = base_name.rsplit('.', 1)[0]
        try:
            for f in os.listdir(dir_name):
                if f.startswith(prefix) and f.endswith('.json'):
                    return os.path.join(dir_name, f)
        except Exception:
            pass
        return None

    def _fast_check_exif(self, file_path):
        """Fast binary header reader for existing EXIF DateTime tags (0.1ms bypass)."""
        date_pattern = re.compile(rb'\b(19\d{2}|20\d{2}):(0[1-9]|1[0-2]):(0[1-9]|[12]\d|3[01]) ([01]\d|2[0-3]):([0-5]\d):([0-5]\d)\b')
        try:
            with open(file_path, 'rb') as f:
                chunk = f.read(65536)
                m = date_pattern.search(chunk)
                if m:
                    return m.group(0).decode('ascii', errors='ignore')
        except Exception:
            pass
        return None

    def verify_and_repair_exif(self, file_path):
        try:
            # 1. Fast-Path Binary Header Scan (0.1ms - bypasses ExifTool subprocess spawn)
            fast_date = self._fast_check_exif(file_path)
            if fast_date:
                return 'VALID', fast_date, 0

            # 2. ExifTool Verification Fallback
            cmd = [self.exiftool_path, '-s3', '-CreateDate', '-DateTimeOriginal', file_path]
            res = subprocess.run(cmd, capture_output=True, text=True)
            existing_date = res.stdout.strip()
            
            if existing_date:
                first_date = existing_date.split('\n')[0].strip()
                return 'VALID', first_date, 0
                
            sidecar = self.find_json_sidecar(file_path)
            if sidecar:
                with open(sidecar, 'r') as sf:
                    sdata = json.load(sf)
                    ts = int(sdata.get('photoTakenTime', {}).get('timestamp', 0))
                    geo = sdata.get('geoDataExif') or sdata.get('geoData')
                    lat = geo.get('latitude', 0.0) if geo else 0.0
                    lng = geo.get('longitude', 0.0) if geo else 0.0
                    
                    if ts > 0:
                        dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
                        date_str = dt.strftime('%Y:%m:%d %H:%M:%S')
                        
                        target_path = file_path
                        renamed = False
                        if file_path.lower().endswith('.heic'):
                            file_cmd = subprocess.run(['file', file_path], capture_output=True, text=True).stdout
                            if 'JPEG image data' in file_cmd:
                                target_path = file_path[:-5] + '.JPG'
                                os.rename(file_path, target_path)
                                renamed = True
                        elif file_path.lower().endswith('.png'):
                            file_cmd = subprocess.run(['file', file_path], capture_output=True, text=True).stdout
                            if 'JPEG image data' in file_cmd:
                                target_path = file_path[:-4] + '.JPG'
                                os.rename(file_path, target_path)
                                renamed = True

                        exif_cmd = [
                            self.exiftool_path,
                            '-overwrite_original',
                            '-api', 'largefilesupport=1',
                            '-m',
                            f'-DateTimeOriginal={date_str}',
                            f'-CreateDate={date_str}'
                        ]
                        if lat != 0.0 or lng != 0.0:
                            lat_ref = 'N' if lat >= 0 else 'S'
                            lng_ref = 'E' if lng >= 0 else 'W'
                            exif_cmd.extend([
                                f'-GPSLatitude={abs(lat)}',
                                f'-GPSLatitudeRef={lat_ref}',
                                f'-GPSLongitude={abs(lng)}',
                                f'-GPSLongitudeRef={lng_ref}'
                            ])
                        exif_cmd.append(target_path)
                        subprocess.run(exif_cmd, capture_output=True, text=True)
                        if renamed:
                            os.rename(target_path, file_path)
                            
                        has_gps = 1 if (lat != 0.0 or lng != 0.0) else 0
                        return 'REPAIRED_SIDECAR', date_str, has_gps
                        
            dir_name = os.path.basename(os.path.dirname(file_path))
            parent_dir_name = os.path.basename(os.path.dirname(os.path.dirname(file_path)))
            rel_dir = os.path.relpath(os.path.dirname(file_path), self.source_dir)
            match = re.search(r'\b(19[89]\d|20[0-2]\d)\b', dir_name) or re.search(r'\b(19[89]\d|20[0-2]\d)\b', parent_dir_name) or re.search(r'\b(19[89]\d|20[0-2]\d)\b', rel_dir)
            
            if match:
                year = match.group(1)
                fallback_date_str = f"{year}:01:01 12:00:00"
            else:
                mtime = os.path.getmtime(file_path)
                dt = datetime.datetime.fromtimestamp(mtime)
                fallback_date_str = dt.strftime('%Y:%m:%d %H:%M:%S')

            target_path = file_path
            renamed = False
            if file_path.lower().endswith('.heic'):
                file_cmd = subprocess.run(['file', file_path], capture_output=True, text=True).stdout
                if 'JPEG image data' in file_cmd:
                    target_path = file_path[:-5] + '.JPG'
                    os.rename(file_path, target_path)
                    renamed = True
            elif file_path.lower().endswith('.png'):
                file_cmd = subprocess.run(['file', file_path], capture_output=True, text=True).stdout
                if 'JPEG image data' in file_cmd:
                    target_path = file_path[:-4] + '.JPG'
                    os.rename(file_path, target_path)
                    renamed = True

            exif_cmd = [
                self.exiftool_path,
                '-overwrite_original',
                '-api', 'largefilesupport=1',
                '-m',
                f'-DateTimeOriginal={fallback_date_str}',
                f'-CreateDate={fallback_date_str}',
                target_path
            ]
            subprocess.run(exif_cmd, capture_output=True, text=True)
            if renamed:
                os.rename(target_path, file_path)
                
            return 'REPAIRED_FOLDER', fallback_date_str, 0

        except Exception:
            return 'MISSING_NO_SOURCE', None, 0

    def discover_and_sync_inventory(self):
        print("Discovering media files and syncing with tracking database...")
        conn_state = sqlite3.connect(self.state_db)
        cursor_state = conn_state.cursor()
        
        cursor_state.execute("SELECT file_path, status FROM migration_files;")
        tracked_files = dict(cursor_state.fetchall())
        
        library_assets = self.schema_adapter.get_library_assets_set()
        
        new_records = []
        total_discovered = 0

        for root, dirs, files in os.walk(self.source_dir):
            if "/Failed" in root or root.endswith("/Failed"):
                continue
            for f in files:
                if f.lower().endswith(self.media_extensions) and not f.startswith('.'):
                    total_discovered += 1
                    full_p = os.path.join(root, f)
                    if full_p in tracked_files:
                        continue
                        
                    try:
                        sz = os.path.getsize(full_p)
                        if sz == 0:
                            continue
                            
                        ext = os.path.splitext(f)[1].upper().replace('.', '')
                        stem, f_ext = self.schema_adapter.clean_stem(f)
                        clean_fn = stem + f_ext
                        
                        if (f, sz) in library_assets or (clean_fn, sz) in library_assets or (f"{stem} (Edited){f_ext}", sz) in library_assets:
                            status = 'ALREADY_EXISTS'
                        else:
                            status = 'PENDING'
                            
                        new_records.append((full_p, f, sz, ext, 'PENDING_AUDIT', None, 0, 0, status, 'PENDING_UPLOAD', None))
                    except Exception:
                        pass

        if new_records:
            cursor_state.executemany('''
            INSERT OR IGNORE INTO migration_files 
            (file_path, file_name, file_size, file_format, exif_date_status, exif_timestamp, has_gps, batch_number, status, icloud_sync_status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            ''', new_records)
            conn_state.commit()

        # Active Two-Way Reconciliation: Re-verify all PENDING files against Photos.sqlite
        cursor_state.execute("SELECT file_path, file_name, file_size FROM migration_files WHERE status = 'PENDING';")
        pending_rows = cursor_state.fetchall()
        existing_in_photos = []
        for p, fname, sz in pending_rows:
            stem, f_ext = self.schema_adapter.clean_stem(fname)
            clean_fn = stem + f_ext
            if (fname, sz) in library_assets or (clean_fn, sz) in library_assets or (f"{stem} (Edited){f_ext}", sz) in library_assets:
                existing_in_photos.append(p)
        if existing_in_photos:
            cursor_state.executemany("UPDATE migration_files SET status='ALREADY_EXISTS', updated_at=CURRENT_TIMESTAMP WHERE file_path=?;", [(p,) for p in existing_in_photos])
            conn_state.commit()

        cursor_state.execute("SELECT status, count(*) FROM migration_files GROUP BY status;")
        status_counts = dict(cursor_state.fetchall())
        cursor_state.execute("SELECT icloud_sync_status, count(*) FROM migration_files GROUP BY icloud_sync_status;")
        cloud_counts = dict(cursor_state.fetchall())
        conn_state.close()

        lib_stats = self.schema_adapter.get_library_counts()
        print("\n" + "="*65)
        print("  ARCHIVE INVENTORY & PROGRESS BREAKDOWN")
        print("="*65)
        if lib_stats:
            tot_p = lib_stats.get('total_photos', 0)
            tot_v = lib_stats.get('total_videos', 0)
            c_p = lib_stats.get('cloud_photos')
            c_v = lib_stats.get('cloud_videos')
            print("📸 Apple Photos System Library:")
            print(f"   • Total Library Content        : {tot_p:,} Photos, {tot_v:,} Videos")
            if c_p is not None and c_v is not None:
                print(f"   • Confirmed Synced to iCloud   : {c_p:,} Photos, {c_v:,} Videos")
            print()

        already_in_lib = status_counts.get('ALREADY_EXISTS', 0)
        migrated_prev = status_counts.get('IMPORTED', 0)
        pending_import = status_counts.get('PENDING', 0)
        quarantined = status_counts.get('FAILED_QUARANTINED', 0) + status_counts.get('FAILED', 0)

        print("📦 Source Archive (Takeout / OneDrive):")
        print(f"   • Total Media Files Found      : {total_discovered:,}")
        print(f"   • Pre-Existing in Photos (Skip): {already_in_lib:,}  (Deduplicated - already in library)")
        print(f"   • Migrated in Previous Runs    : {migrated_prev:,}  (Successfully imported)")
        if quarantined > 0:
            print(f"   • Quarantined / Corrupted      : {quarantined:,}  (Moved to Failed/)")
        print(f"   • Remaining to Import          : {pending_import:,}  (Queued for migration)")
        print("="*65 + "\n")
        return pending_import

    def import_batch_applescript(self, file_paths):
        if not file_paths:
            return True
        self.dismiss_photos_modal_dialogs()
        escaped = [f'POSIX file "{p.replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34))}"' for p in file_paths]
        paths_str = ', '.join(escaped)
        script = f'''
        tell application "Photos"
            activate
            set targetFiles to {{{paths_str}}}
            try
                import targetFiles skip check duplicates yes
            on error
                import targetFiles
            end try
        end tell
        '''
        res = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
        return res.returncode == 0

    def quarantine_failed_file(self, file_path, reason="Unrecognizable file format or corrupt"):
        """Relocates a failed file to the <source_dir>/Failed/ directory."""
        try:
            rel_path = os.path.relpath(file_path, self.source_dir)
            target_dest = os.path.join(self.source_dir, "Failed", rel_path)
            os.makedirs(os.path.dirname(target_dest), exist_ok=True)
            shutil.move(file_path, target_dest)
            
            conn = sqlite3.connect(self.state_db)
            cur = conn.cursor()
            cur.execute("""
            UPDATE migration_files 
            SET status='FAILED_QUARANTINED', error_message=?, file_path=?, updated_at=CURRENT_TIMESTAMP 
            WHERE file_path=?;
            """, (reason, target_dest, file_path))
            conn.commit()
            conn.close()
            return target_dest
        except Exception:
            return None

    def import_single_file_fallbacks(self, file_paths, progress_callback=None):
        verified = []
        failed = []
        for idx, p in enumerate(file_paths):
            if progress_callback:
                progress_callback(f"Single fallback {idx+1}/{len(file_paths)}")
            self.dismiss_photos_modal_dialogs()
            escaped = p.replace('\\', '\\\\').replace('"', '\\"')
            script = f'''
            tell application "Photos"
                activate
                set targetFiles to {{POSIX file "{escaped}"}}
                import targetFiles
            end tell
            '''
            subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
            
            fname = os.path.basename(p)
            stem, ext = self.schema_adapter.clean_stem(fname)
            clean_name = stem + ext
            
            is_verified = False
            # Progressive polling loop (up to 4 attempts over 3.0s)
            for attempt in range(4):
                time.sleep(0.6)
                self.dismiss_photos_modal_dialogs()
                try:
                    library_assets = self.schema_adapter.get_batch_assets_set([p])
                    lib_fnames = {rec[0].lower() for rec in library_assets if rec[0]}
                    lib_stems = {os.path.splitext(rec[0])[0].lower() for rec in library_assets if rec[0]}
                    
                    if (fname.lower() in lib_fnames or 
                        clean_name.lower() in lib_fnames or 
                        stem.lower() in lib_stems or
                        (ext.lower() in ('.mov', '.mp4') and stem.lower() in lib_stems)):
                        is_verified = True
                        break
                except Exception:
                    pass

            if is_verified:
                verified.append(p)
            else:
                self.quarantine_failed_file(p, reason="Rejected by Photos.app during single-item isolation")
                failed.append(p)
                
        return verified, failed

    def verify_batch_in_photos(self, chunk_files, initial_wait_sec=5, max_wait_sec=20, progress_callback=None):
        start_poll = time.time()
        last_count = 0
        last_change_time = time.time()
        last_dismiss_time = 0
        
        while True:
            time.sleep(0.3)
            now = time.time()
            if now - last_dismiss_time > 1.5:
                self.dismiss_photos_modal_dialogs()
                last_dismiss_time = now

            library_assets = self.schema_adapter.get_batch_assets_set(chunk_files)
            lib_fnames = {rec[0] for rec in library_assets if rec[0]}
            lib_stems = {os.path.splitext(rec[0])[0].lower() for rec in library_assets if rec[0]}
                
            verified = []
            unverified = []
            for p in chunk_files:
                fname = os.path.basename(p)
                try:
                    sz = os.path.getsize(p)
                    stem, ext = self.schema_adapter.clean_stem(fname)
                    clean_name = stem + ext

                    is_verified = (
                        fname in lib_fnames or 
                        clean_name in lib_fnames or 
                        (ext.lower() in ('.mov', '.mp4') and stem.lower() in lib_stems)
                    )

                    if is_verified:
                        verified.append(p)
                    else:
                        unverified.append(p)
                except Exception:
                    unverified.append(p)
                    
            if progress_callback:
                progress_callback(f"Verifying in Photos: {len(verified)}/{len(chunk_files)}")

            if len(verified) == len(chunk_files):
                return verified, []
                
            if len(verified) > last_count:
                last_count = len(verified)
                last_change_time = time.time()
                
            now = time.time()
            if (now - last_change_time > 6.0 and now - start_poll > initial_wait_sec) or (now - start_poll > max_wait_sec):
                break

        if unverified:
            self.dismiss_photos_modal_dialogs()
            time.sleep(0.3)
            verified_fb, truly_failed = self.import_single_file_fallbacks(unverified, progress_callback=progress_callback)
            verified.extend(verified_fb)
            return verified, truly_failed

        return verified, unverified

    def check_icloud_sync_status(self, watch_interval=5, max_watch_sec=None):
        print("\n" + "="*60)
        print("  iCloud Upload Verification Engine")
        print("="*60)
        
        conn_state = sqlite3.connect(self.state_db)
        cursor_state = conn_state.cursor()
        cursor_state.execute("SELECT file_path, file_name, file_size FROM migration_files WHERE status IN ('IMPORTED', 'ALREADY_EXISTS');")
        target_files = cursor_state.fetchall()
        conn_state.close()
        
        if not target_files:
            print("No imported or existing files found in state database to verify for iCloud sync.")
            return

        print(f"Tracking iCloud upload status for {len(target_files):,} cataloged library items...\n")
        start_time = time.time()
        
        while True:
            cloud_records = self.schema_adapter.get_cloud_sync_status_map()
            synced_count = 0
            pending_count = 0
            synced_paths = []
            
            for p, fname, fsize in target_files:
                stem, ext = self.schema_adapter.clean_stem(fname)
                clean_name = stem + ext
                is_synced = (
                    cloud_records.get(fname.lower(), False) or
                    cloud_records.get(clean_name.lower(), False) or
                    cloud_records.get(stem.lower(), False) or
                    cloud_records.get((fname, fsize), False)
                )
                if is_synced:
                    synced_count += 1
                    synced_paths.append(p)
                else:
                    pending_count += 1

            conn_state = sqlite3.connect(self.state_db)
            cursor_state = conn_state.cursor()
            if synced_paths:
                cursor_state.executemany("UPDATE migration_files SET icloud_sync_status='SYNCED_TO_ICLOUD', updated_at=CURRENT_TIMESTAMP WHERE file_path=?;", [(sp,) for sp in synced_paths])
            conn_state.commit()
            conn_state.close()

            total = len(target_files)
            pct = (synced_count / total) * 100 if total > 0 else 0
            bar_len = 25
            filled_len = int(bar_len * synced_count // total) if total > 0 else 0
            bar = '█' * filled_len + '░' * (bar_len - filled_len)
            elapsed = time.time() - start_time

            dashboard = (
                f"\r[{bar}] {pct:5.1f}% | "
                f"iCloud Synced: {synced_count:,}/{total:,} | "
                f"Pending Cloud Upload: {pending_count:,} | "
                f"Monitoring: {elapsed/60:4.1f}m"
            )
            sys.stdout.write(dashboard)
            sys.stdout.flush()

            if pending_count == 0 or (max_watch_sec and elapsed >= max_watch_sec):
                break
                
            if not max_watch_sec:
                break
                
            time.sleep(watch_interval)

        print("\n\n" + "="*60)
        print("  iCLOUD SYNC STATUS SUMMARY")
        print("="*60)
        print(f"Total Files Cataloged       : {len(target_files):,}")
        print(f"Confirmed in iCloud         : {synced_count:,} ({pct:.1f}%)")
        print(f"Pending Background Upload   : {pending_count:,}")
        print(f"State Database Updated      : {self.state_db}")
        print("="*60 + "\n")

        self.print_quarantine_fallback_guidance()

    def prepare_all_media(self, max_files=None):
        """Stage 1: Multi-threaded EXIF Metadata Repair and Live Photo QuickTime Synthesis."""
        conn = sqlite3.connect(self.state_db)
        cursor = conn.cursor()
        if max_files:
            cursor.execute("SELECT file_path FROM migration_files WHERE status='PENDING' AND (exif_date_status IS NULL OR exif_date_status != 'EXIF_READY') LIMIT ?;", (max_files,))
        else:
            cursor.execute("SELECT file_path FROM migration_files WHERE status='PENDING' AND (exif_date_status IS NULL OR exif_date_status != 'EXIF_READY');")
        files_to_prep = [r[0] for r in cursor.fetchall()]
        conn.close()

        total_prep = len(files_to_prep)
        print(f"\n" + "="*65)
        print("  STAGE 1: LOSSLESS METADATA REPAIR & LIVE PHOTO SYNTHESIS")
        print("="*65)
        if total_prep == 0:
            print("✓ All queued media files have already been audited and repaired.\n")
            return

        print(f"Auditing and preparing {total_prep:,} media files across 12 threads...")
        start_t = time.time()
        prepared_count = 0
        live_paired_count = 0

        # 1. Parallel EXIF Fast-Path & Sidecar Repair
        chunk_size = 200
        total_chunks = (total_prep + chunk_size - 1) // chunk_size

        for c_idx in range(total_chunks):
            chunk = files_to_prep[c_idx * chunk_size : (c_idx + 1) * chunk_size]
            with ThreadPoolExecutor(max_workers=12) as executor:
                results = list(executor.map(self.verify_and_repair_exif, chunk))
                
            conn = sqlite3.connect(self.state_db)
            cursor = conn.cursor()
            for p, (exif_st, dt_str, gps) in zip(chunk, results):
                cursor.execute("""
                UPDATE migration_files 
                SET exif_date_status='EXIF_READY', exif_timestamp=?, has_gps=? 
                WHERE file_path=?;
                """, (dt_str, gps, p))
            conn.commit()
            conn.close()

            prepared_count += len(chunk)
            elapsed = time.time() - start_t
            rate = prepared_count / elapsed if elapsed > 0 else 0
            
            elapsed_min = elapsed / 60
            elapsed_str = f"{elapsed_min/60:.1f}h" if elapsed_min >= 60 else f"{elapsed_min:.1f}m"
            rem_prep = total_prep - prepared_count
            eta_sec = rem_prep / rate if rate > 0 else 0
            eta_min = eta_sec / 60
            eta_str = f"{eta_min/60:.1f}h" if eta_min >= 60 else f"{eta_min:.1f}m"

            pct = (prepared_count / total_prep) * 100
            bar_len = 20
            filled_len = int(bar_len * prepared_count // total_prep)
            bar = '█' * filled_len + '░' * (bar_len - filled_len)

            status = f"\r[{bar}] {pct:5.1f}% | Stage 1: Prepared {prepared_count:,}/{total_prep:,} | Speed: {rate:5.1f} f/s | Elapsed: {elapsed_str} | ETA: {eta_str}"
            sys.stdout.write(status.ljust(110))
            sys.stdout.flush()

        # 2. Live Photo Synthesis Pairing Pass
        print("\n\nScanning for Google Takeout Still + Motion clips to pair into native Live Photos...")
        seen_dirs = set()
        for p in files_to_prep:
            d = os.path.dirname(p)
            if d not in seen_dirs:
                seen_dirs.add(d)
                pairs = self.live_photo_synth.find_potential_live_photos_in_directory(d)
                for img_p, vid_p in pairs:
                    ok, _ = self.live_photo_synth.pair_still_and_video(img_p, vid_p)
                    if ok:
                        live_paired_count += 1

        print(f"✓ Stage 1 Complete: {prepared_count:,} files prepared | {live_paired_count:,} Live Photos synthesized in {(time.time()-start_t):.1f}s\n")

    def stream_import_to_photos(self, max_files=None):
        """Stage 2: Pure High-Speed Streaming Ingestion into Apple Photos."""
        conn = sqlite3.connect(self.state_db)
        cursor = conn.cursor()
        if max_files:
            cursor.execute("SELECT file_path FROM migration_files WHERE status IN ('PENDING', 'FAILED') LIMIT ?;", (max_files,))
        else:
            cursor.execute("SELECT file_path FROM migration_files WHERE status IN ('PENDING', 'FAILED');")
        pending_files = [row[0] for row in cursor.fetchall()]
        conn.close()

        total_to_process = len(pending_files)
        print("="*65)
        print("  STAGE 2: HIGH-SPEED APPLE PHOTOS STREAMING INGESTION")
        print("="*65)
        if total_to_process == 0:
            print("✓ All files are already imported into Apple Photos!\n")
            return

        total_batches = (total_to_process + self.batch_size - 1) // self.batch_size
        print(f"Streaming {total_to_process:,} prepared files across {total_batches} batches (Batch Size: {self.batch_size})...\n")

        # Start asynchronous modal watchdog & macOS power assertion (caffeinate)
        self._start_watchdog_thread()
        caffeinate_proc = None
        try:
            caffeinate_proc = subprocess.Popen(['caffeinate', '-disu'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        total_migrated = 0
        total_failed = 0
        start_time = time.time()

        def render_live_dashboard(batch_num, phase_label, custom_migrated=None):
            nonlocal total_migrated, total_failed, start_time, total_batches, total_to_process
            current_migrated = custom_migrated if custom_migrated is not None else total_migrated
            elapsed = time.time() - start_time
            rate = current_migrated / elapsed if elapsed > 0 else 0
            rem_files = total_to_process - (current_migrated + total_failed)
            eta_sec = rem_files / rate if rate > 0 else 0
            
            elapsed_min = elapsed / 60
            elapsed_str = f"{elapsed_min/60:.1f}h" if elapsed_min >= 60 else f"{elapsed_min:.1f}m"
            
            eta_min = eta_sec / 60
            eta_str = f"{eta_min/60:.1f}h" if eta_min >= 60 else f"{eta_min:.1f}m"
            
            pct = (current_migrated / total_to_process) * 100 if total_to_process > 0 else 0
            bar_len = 20
            filled_len = int(bar_len * current_migrated // total_to_process) if total_to_process > 0 else 0
            bar = '█' * filled_len + '░' * (bar_len - filled_len)

            status_line = (
                f"\r[{bar}] {pct:5.1f}% | "
                f"Batch {batch_num}/{total_batches} [{phase_label}] | "
                f"Migrated: {current_migrated:,} | "
                f"Quarantined: {total_failed} | "
                f"Speed: {rate:4.1f} f/s | "
                f"Elapsed: {elapsed_str} | "
                f"ETA: {eta_str}"
            )
            sys.stdout.write(status_line.ljust(130))
            sys.stdout.flush()

        try:
            for b_idx in range(total_batches):
                batch_num = b_idx + 1
                chunk = pending_files[b_idx * self.batch_size : (b_idx + 1) * self.batch_size]

                # 1. Dispatch Batch to Photos.app
                render_live_dashboard(batch_num, "Dispatching to Photos.app")
                self.import_batch_applescript(chunk)

                # 2. Progressive Targeted Verification (80ms indexed query)
                def make_verify_callback(b_num):
                    def cb(msg):
                        render_live_dashboard(b_num, msg)
                    return cb

                verified, failed = self.verify_batch_in_photos(chunk, progress_callback=make_verify_callback(batch_num))
                
                conn = sqlite3.connect(self.state_db)
                cursor = conn.cursor()
                if verified:
                    cursor.executemany("UPDATE migration_files SET status='IMPORTED', batch_number=?, error_message=NULL, updated_at=CURRENT_TIMESTAMP WHERE file_path=?;", [(batch_num, v) for v in verified])
                conn.commit()
                conn.close()

                total_migrated += len(verified)
                total_failed += len(failed)

                # 3. Batch Complete
                render_live_dashboard(batch_num, f"✓ Batch +{len(verified)}")

                if b_idx + 1 < total_batches:
                    time.sleep(self.delay_sec)
        finally:
            self._stop_watchdog_thread()
            if caffeinate_proc:
                try:
                    caffeinate_proc.terminate()
                except Exception:
                    pass

        print("\n\n" + "="*60)
        print("  MIGRATION SESSION COMPLETE")
        print("="*60)
        print(f"Total Processed in this run : {total_to_process:,}")
        print(f"Successfully Verified       : {total_migrated:,}")
        print(f"Quarantined in Failed/      : {total_failed:,}")
        print(f"Total Time                  : {(time.time() - start_time)/60:.1f} minutes")
        print(f"State Database Updated      : {self.state_db}")
        
        try:
            conn_st = sqlite3.connect(self.state_db)
            cur_st = conn_st.cursor()
            cur_st.execute("SELECT icloud_sync_status, count(*) FROM migration_files GROUP BY icloud_sync_status;")
            c_summary = dict(cur_st.fetchall())
            conn_st.close()
            print(f"iCloud Sync (Confirmed)     : {c_summary.get('SYNCED_TO_ICLOUD', 0):,}")
            print(f"iCloud Sync (Pending Upload): {c_summary.get('PENDING_UPLOAD', 0):,}")
        except Exception:
            pass
        print("="*60 + "\n")

        # Fallback Guidance & Disclaimer
        failed_dir = os.path.join(self.source_dir, "Failed")
        if os.path.exists(failed_dir):
            try:
                failed_items = [f for f in os.listdir(failed_dir) if not f.startswith('.')]
                if failed_items:
                    print("\033[92m" + "─"*65)
                    print(f"  💡 MANUAL FALLBACK IMPORT (FOR {len(failed_items):,} QUARANTINED FILES)")
                    print("─"*65)
                    print(f"You can drag and drop the folder below directly into Apple Photos:")
                    print(f"📁 {failed_dir}\n")
                    print("⚠️  DISCLAIMER & EXPECTED BEHAVIORS OF MANUAL DRAG-AND-DROP:")
                    print("1. Duplicate Prompts : Apple Photos may prompt you to resolve potential duplicates.")
                    print("2. Live Photos       : Unpaired files will import as separate still + motion items.")
                    print("3. Capture Timestamps: Files lacking EXIF headers will adopt the current import")
                    print("                       timestamp instead of their historical Takeout date.")
                    print("─"*65 + "\033[0m\n")
            except Exception:
                pass

    def deduplicate_true_copies(self, auto_mark_duplicates=True):
        """Stage 0: Krokiet / Czkawka 3-Tier Progressive True Content Deduplication."""
        from core.krokiet_duplicate_finder import KrokietDuplicateFinder
        finder = KrokietDuplicateFinder(self.source_dir, state_db=self.state_db)
        
        conn = sqlite3.connect(self.state_db)
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM migration_files WHERE status='PENDING';")
        pending_files = [r[0] for r in cursor.fetchall()]
        conn.close()
        
        if not pending_files:
            return
            
        print("\n" + "="*65)
        print("  STAGE 0: KROKIET TRUE CONTENT DEDUPLICATION")
        print("="*65)
        print(f"Scanning {len(pending_files):,} pending files for byte-level duplicate clusters...")
        
        start_t = time.time()
        clusters = finder.find_exact_duplicates(file_paths=pending_files)
        report = finder.get_summary_report(clusters)
        
        redundant_to_mark = []
        for hash_val, paths in clusters.items():
            for duplicate_path in paths[1:]:
                redundant_to_mark.append(duplicate_path)
                
        if redundant_to_mark and auto_mark_duplicates:
            conn = sqlite3.connect(self.state_db)
            cursor = conn.cursor()
            cursor.executemany("UPDATE migration_files SET status='ALREADY_EXISTS', error_message='Exact byte-for-byte duplicate (Krokiet)' WHERE file_path=?;", [(p,) for p in redundant_to_mark])
            conn.commit()
            conn.close()
            
        elapsed = time.time() - start_t
        print(f"✓ Stage 0 Complete: Found {report['total_duplicate_groups']:,} duplicate groups ({len(redundant_to_mark):,} redundant copies, {report['reclaimable_mb']:.1f} MB) in {elapsed:.1f}s")
        if redundant_to_mark:
            print(f"  -> Marked {len(redundant_to_mark):,} duplicate copies as ALREADY_EXISTS (skipping redundant EXIF & Import passes).\n")
        else:
            print("  -> 0 redundant duplicates found in pending queue.\n")

    def run_migration(self, max_files=None, dry_run=False, dedup_only=False, prepare_only=False, import_only=False, skip_dedup=False, check_cloud_after=True):
        if not self.check_readiness_and_briefing():
            print("Aborting: Readiness checks failed.")
            return

        pending_count = self.discover_and_sync_inventory()
        if dry_run:
            print("[DRY RUN COMPLETED] No files were imported.")
            return

        # Stage 0: Krokiet True Deduplication
        if not import_only and not skip_dedup:
            self.deduplicate_true_copies()
            if dedup_only:
                print("Stopping after Stage 0 as requested (--dedup-only).")
                return

        # Stage 1: Preparation & Synthesis
        if not import_only:
            self.prepare_all_media(max_files=max_files)
            if prepare_only:
                print("Stopping after Stage 1 as requested (--prepare-only).")
                return

        # Stage 2: High-Speed Streaming Ingestion
        self.stream_import_to_photos(max_files=max_files)

        # Stage 3: iCloud Verification
        if check_cloud_after:
            self.check_icloud_sync_status()

        # Final Fallback Guidance & Disclaimer
        self.print_quarantine_fallback_guidance()

    def print_quarantine_fallback_guidance(self):
        """Displays formatted green manual fallback import guidance and disclaimer if quarantined files exist."""
        failed_dir = os.path.join(self.source_dir, "Failed")
        failed_items = []
        if os.path.exists(failed_dir):
            try:
                failed_items = [f for f in os.listdir(failed_dir) if not f.startswith('.')]
            except Exception:
                pass
                
        # Also check state database for quarantined files count
        db_failed_count = 0
        try:
            conn = sqlite3.connect(self.state_db)
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM migration_files WHERE status='FAILED_QUARANTINED';")
            r = cur.fetchone()
            if r:
                db_failed_count = r[0]
            conn.close()
        except Exception:
            pass
            
        total_quarantined = max(len(failed_items), db_failed_count)
        if total_quarantined > 0:
            print("\033[92m" + "─"*70)
            print(f"  💡 MANUAL FALLBACK IMPORT (FOR {total_quarantined:,} QUARANTINED FILES)")
            print("─"*70)
            print(f"If any valid files remain quarantined, you can drag and drop this folder")
            print(f"directly into your Apple Photos window:")
            print(f"📁 {failed_dir}\n")
            print("⚠️  DISCLAIMER & EXPECTED BEHAVIORS OF MANUAL DRAG-AND-DROP:")
            print("1. Duplicate Prompts : Apple Photos will prompt you to decide on potential duplicates.")
            print("2. Live Photos       : Unpaired files will import as separate still + motion items.")
            print("3. Capture Timestamps: Files lacking embedded EXIF dates will default to today's date")
            print("                       instead of their historical Takeout chronological date.")
            print("─"*70 + "\033[0m\n")

    def run_krokiet_duplicate_analysis(self):
        """Executes Krokiet/Czkawka progressive multi-tier duplicate analysis across entire archive."""
        from core.krokiet_duplicate_finder import KrokietDuplicateFinder
        finder = KrokietDuplicateFinder(self.source_dir, state_db=self.state_db)
        print("\n" + "="*65)
        print("  KROKIET / CZKAWKA TRUE DUPLICATE DISCOVERY ENGINE")
        print("="*65)
        clusters = finder.find_exact_duplicates(progress_callback=print)
        report = finder.get_summary_report(clusters)
        
        print("\n" + "="*65)
        print("  KROKIET DUPLICATE ANALYSIS SUMMARY")
        print("="*65)
        print(f"Total Duplicate Groups Found   : {report['total_duplicate_groups']:,}")
        print(f"Redundant Duplicate Files      : {report['total_redundant_files']:,}")
        print(f"Reclaimable Disk Space         : {report['reclaimable_gb']:.2f} GB ({report['reclaimable_mb']:.1f} MB)")
        print("="*65 + "\n")
        
        if clusters:
            print("Sample True Duplicate Clusters (First 5):")
            for idx, (h, paths) in enumerate(list(clusters.items())[:5]):
                print(f"\n[{idx+1}] Hash: {h[:16]}... ({len(paths)} identical copies)")
                for p in paths:
                    print(f"    - {p}")
            print()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="photos-icloud-migrator: Standalone Cloud-to-iCloud Migration Suite")
    parser.add_argument("--source-dir", default=None, help="Root directory of media files (prompts Finder if omitted)")
    parser.add_argument("--library-db", default=None, help="Path to Photos.sqlite or .photoslibrary (prompts Finder if omitted)")
    parser.add_argument("--state-db", default=None, help="Tracking SQLite database path (defaults to <source-dir>/batchimport_sqlite.db)")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size per import cycle (Default: 100)")
    parser.add_argument("--delay", type=int, default=1, help="Seconds delay between batches (Default: 1)")
    parser.add_argument("--max-files", type=int, default=None, help="Limit total files for testing")
    parser.add_argument("--dry-run", action="store_true", help="Run pre-flight check and inventory scan only")
    parser.add_argument("--dedup-only", action="store_true", help="Run Stage 0 only: Krokiet progressive true deduplication without importing")
    parser.add_argument("--prepare-only", action="store_true", help="Run Stage 1 only: EXIF repair & Live Photo synthesis without importing")
    parser.add_argument("--import-only", action="store_true", help="Run Stage 2 only: Skip preparation and stream directly to Apple Photos")
    parser.add_argument("--skip-dedup", action="store_true", help="Skip Stage 0 deduplication and proceed directly to Stage 1")
    parser.add_argument("--krokiet-dedup", action="store_true", help="Run Krokiet/Czkawka multi-tier progressive duplicate analysis report")
    parser.add_argument("--interactive", "-i", action="store_true", help="Force interactive macOS Finder folder pickers")
    parser.add_argument("--yes", "-y", action="store_true", help="Automatically acknowledge pre-flight briefing prompt")
    parser.add_argument("--icloud-status", nargs="?", const=0, type=int, default=None, metavar="SECONDS",
                        help="Check iCloud sync status (snapshot report if omitted, or live monitor for N seconds)")
    
    args = parser.parse_args()
    
    migrator = PhotosICloudMigrator(
        source_dir=args.source_dir,
        library_db=args.library_db,
        state_db=args.state_db,
        batch_size=args.batch_size,
        delay_sec=args.delay,
        auto_yes=args.yes,
        interactive=args.interactive
    )
    
    if args.krokiet_dedup:
        migrator.run_krokiet_duplicate_analysis()
    elif args.icloud_status is not None:
        if args.icloud_status > 0:
            migrator.check_icloud_sync_status(watch_interval=5, max_watch_sec=args.icloud_status)
        else:
            migrator.check_icloud_sync_status(watch_interval=5, max_watch_sec=0)
    else:
        migrator.run_migration(
            max_files=args.max_files,
            dry_run=args.dry_run,
            dedup_only=args.dedup_only,
            prepare_only=args.prepare_only,
            import_only=args.import_only,
            skip_dedup=args.skip_dedup
        )
