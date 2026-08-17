#!/usr/bin/env python3
"""
Photos Library Schema Adapter for macOS
Author: Antigravity and junoonx
Description: Dynamically inspects and adapts to SQLite schema variations across macOS versions
             (Ventura, Sonoma, Sequoia) without hardcoding fixed column definitions.
"""

import os
import sqlite3
import plistlib
import re

class PhotosSchemaAdapter:
    def __init__(self, library_db_path):
        self.library_db = os.path.abspath(library_db_path)
        self.db_uri = f"file:{self.library_db}?mode=ro"
        self.asset_cols = {}
        self.attr_cols = {}
        self.asset_table = "ZASSET"
        self.valid = False
        self._inspect_schema()

    def _inspect_schema(self):
        if not os.path.exists(self.library_db):
            self.valid = False
            return
            
        try:
            conn = sqlite3.connect(self.db_uri, uri=True)
            cur = conn.cursor()
            
            # 1. Determine Asset Table (ZASSET vs ZGENERICASSET)
            cur.execute("PRAGMA table_info(ZASSET);")
            cols = {row[1]: row[2] for row in cur.fetchall()}
            if cols:
                self.asset_table = "ZASSET"
                self.asset_cols = cols
            else:
                cur.execute("PRAGMA table_info(ZGENERICASSET);")
                self.asset_cols = {row[1]: row[2] for row in cur.fetchall()}
                self.asset_table = "ZGENERICASSET"
            
            # 2. Inspect Additional Attributes Table
            cur.execute("PRAGMA table_info(ZADDITIONALASSETATTRIBUTES);")
            self.attr_cols = {row[1]: row[2] for row in cur.fetchall()}
            
            conn.close()
            self.valid = bool(self.asset_cols and self.attr_cols)
        except Exception:
            self.valid = False
            self.asset_cols = {}
            self.attr_cols = {}

    @staticmethod
    def clean_stem(fn):
        """Extracts clean base stem and extension, removing edit/duplicate suffixes."""
        name, ext = os.path.splitext(fn)
        name = re.sub(r'\s*\((?:Edited|\d+)\)$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'-edited$', '', name, flags=re.IGNORECASE)
        return name, ext

    def get_library_assets_set(self):
        """Returns set of (filename, filesize) for all registered assets (used for initial inventory reconciliation)."""
        if not self.valid:
            return set()
            
        try:
            conn = sqlite3.connect(self.db_uri, uri=True)
            cur = conn.cursor()
            
            fname_col = "ZORIGINALFILENAME" if "ZORIGINALFILENAME" in self.attr_cols else "ZFILENAME"
            fsize_col = "ZORIGINALFILESIZE" if "ZORIGINALFILESIZE" in self.attr_cols else "ZFILESIZE"
            
            query = f"SELECT {fname_col}, {fsize_col} FROM ZADDITIONALASSETATTRIBUTES WHERE {fname_col} IS NOT NULL;"
            cur.execute(query)
            records = set(cur.fetchall())
            conn.close()
            return records
        except Exception:
            return set()

    def get_batch_assets_set(self, file_paths):
        """High-speed targeted query: checks ONLY the assets in the current batch (<15ms)."""
        if not self.valid or not file_paths:
            return set()
            
        try:
            all_candidates = set()
            for p in file_paths:
                fn = os.path.basename(p)
                all_candidates.add(fn)
                stem, ext = self.clean_stem(fn)
                all_candidates.add(stem + ext)
                all_candidates.add(f"{stem} (Edited){ext}")
                all_candidates.add(f"{stem} (1){ext}")
                all_candidates.add(f"{stem}-edited{ext}")
                if ext.lower() in ('.mov', '.mp4'):
                    for img_ext in ['.JPG', '.jpg', '.HEIC', '.heic', '.JPEG', '.jpeg', '.PNG', '.png']:
                        all_candidates.add(stem + img_ext)
                        all_candidates.add(f"{stem} (Edited){img_ext}")

            candidates_list = list(all_candidates)
            placeholders = ','.join(['?'] * len(candidates_list))
            
            fname_col = "ZORIGINALFILENAME" if "ZORIGINALFILENAME" in self.attr_cols else "ZFILENAME"
            fsize_col = "ZORIGINALFILESIZE" if "ZORIGINALFILESIZE" in self.attr_cols else "ZFILESIZE"
            
            query = f"SELECT {fname_col}, {fsize_col} FROM ZADDITIONALASSETATTRIBUTES WHERE {fname_col} COLLATE NOCASE IN ({placeholders});"
            conn = sqlite3.connect(self.db_uri, uri=True)
            cur = conn.cursor()
            cur.execute(query, candidates_list)
            records = set(cur.fetchall())
            conn.close()
            return records
        except Exception:
            return set()

    def get_library_counts(self):
        """Returns photo, video, and iCloud sync counts from SQLite and syncstatus.plist."""
        if not self.valid:
            return {}
            
        stats = {
            "total_photos": 0,
            "total_videos": 0,
            "cloud_photos": None,
            "cloud_videos": None
        }
        
        try:
            conn = sqlite3.connect(self.db_uri, uri=True)
            cur = conn.cursor()
            
            kind_col = "ZKIND" if "ZKIND" in self.asset_cols else "ZKINDTYPE"
            if kind_col in self.asset_cols:
                cur.execute(f"SELECT {kind_col}, count(*) FROM {self.asset_table} GROUP BY {kind_col};")
                for k_val, count in cur.fetchall():
                    if k_val == 0:
                        stats["total_photos"] = count
                    elif k_val == 1:
                        stats["total_videos"] = count
            
            # Dynamic iCloud CloudKit Upload Count Inspection
            cloud_state_col = None
            for candidate in ["ZCLOUDLOCALSTATE", "ZCLOUDASSETGUID", "ZCLOUDBATCHPUBLISHDATE"]:
                if candidate in self.asset_cols:
                    cloud_state_col = candidate
                    break
                    
            if cloud_state_col == "ZCLOUDLOCALSTATE":
                cur.execute(f"SELECT count(*) FROM {self.asset_table} WHERE ZCLOUDLOCALSTATE = 1 AND {kind_col} = 0;")
                r_p = cur.fetchone()
                if r_p:
                    stats["cloud_photos"] = r_p[0]
                    
                cur.execute(f"SELECT count(*) FROM {self.asset_table} WHERE ZCLOUDLOCALSTATE = 1 AND {kind_col} = 1;")
                r_v = cur.fetchone()
                if r_v:
                    stats["cloud_videos"] = r_v[0]
            elif cloud_state_col:
                cur.execute(f"SELECT count(*) FROM {self.asset_table} WHERE {cloud_state_col} IS NOT NULL AND {kind_col} = 0;")
                r_p = cur.fetchone()
                if r_p:
                    stats["cloud_photos"] = r_p[0]
                    
                cur.execute(f"SELECT count(*) FROM {self.asset_table} WHERE {cloud_state_col} IS NOT NULL AND {kind_col} = 1;")
                r_v = cur.fetchone()
                if r_v:
                    stats["cloud_videos"] = r_v[0]
            
            conn.close()
        except Exception:
            pass

        # Fallback to syncstatus.plist if available
        try:
            plist_path = os.path.join(os.path.dirname(self.library_db), "..", "syncstatus.plist")
            if os.path.exists(plist_path):
                with open(plist_path, 'rb') as fp:
                    pdata = plistlib.load(fp)
                    if "syncProgress" in pdata:
                        sp = pdata["syncProgress"]
                        stats["cloud_photos"] = sp.get("totalPhotosSynced", stats["cloud_photos"])
                        stats["cloud_videos"] = sp.get("totalVideosSynced", stats["cloud_videos"])
        except Exception:
            pass

        return stats

    def get_cloud_sync_status_map(self):
        """Returns dict of {key: is_synced_to_icloud} supporting (fname, fsize), fname.lower(), clean_stem.lower()."""
        if not self.valid:
            return {}
            
        sync_map = {}
        try:
            conn = sqlite3.connect(self.db_uri, uri=True)
            cur = conn.cursor()
            
            fname_col = "ZORIGINALFILENAME" if "ZORIGINALFILENAME" in self.attr_cols else "ZFILENAME"
            fsize_col = "ZORIGINALFILESIZE" if "ZORIGINALFILESIZE" in self.attr_cols else "ZFILESIZE"
            
            # Check CloudKit columns in asset table
            cloud_col = None
            for c in ["ZCLOUDLOCALSTATE", "ZCLOUDASSETGUID", "ZCLOUDBATCHPUBLISHDATE"]:
                if c in self.asset_cols:
                    cloud_col = c
                    break
                    
            if cloud_col:
                query = f"""
                SELECT a.{fname_col}, a.{fsize_col}, g.{cloud_col}
                FROM ZADDITIONALASSETATTRIBUTES a
                JOIN {self.asset_table} g ON a.ZASSET = g.Z_PK
                WHERE a.{fname_col} IS NOT NULL;
                """
                cur.execute(query)
                for fn, sz, c_val in cur.fetchall():
                    if cloud_col == "ZCLOUDLOCALSTATE":
                        is_synced = bool(c_val == 1)
                    else:
                        is_synced = bool(c_val is not None and c_val != 0)
                    sync_map[(fn, sz)] = is_synced
                    sync_map[fn.lower()] = is_synced
                    stem, ext = self.clean_stem(fn)
                    sync_map[(stem + ext).lower()] = is_synced
                    sync_map[stem.lower()] = is_synced
            
            conn.close()
        except Exception:
            pass
            
        return sync_map
