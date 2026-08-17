# photos-icloud-migrator

A robust, self-contained migration suite to import large media archives (**Google Photos Takeout**, **Microsoft OneDrive**, local folders) into **macOS Apple Photos** and sync them to **iCloud Photos**.

---

## ⚡ TL;DR — 2-Step Quick Start (Zero Setup)

1. **Download the standalone binary**:
   ```bash
   curl -L -o photos-icloud-migrator https://github.com/junoonx/Photos-to-iCloud/raw/main/dist/photos-icloud-migrator
   ```
2. **Execute in Terminal**:
   ```bash
   chmod +x ./photos-icloud-migrator && ./photos-icloud-migrator -i
   ```
   *(Launches native macOS Finder folder/library pickers with all tools and engines bundled — zero Python or setup required).*

---

### What This Tool Does:
1. **Fixes Media's Metadata (Dates, Locations, Camera)**: Enriches and repairs EXIF metadata using Google JSON sidecars and folder structures so all media appears in proper chronological order with complete GPS and device info.
2. **Creates Real Live Photos**: Fuses independent `.JPG` still photos and matching `.MP4`/`.MOV` motion clips into native Apple Live Photos (avoiding split files or single `.HEIC` limitations).
3. **Skips Duplicates Automatically**: Checks your Apple Photos library first and skips files you already imported, with zero popup prompts.
4. **Never Freezes on Errors**: Closes unexpected popups automatically and moves broken or unreadable files into a `Failed` folder so the rest keep importing unattended.
5. **Ready to Run with Zero Setup**: Packaged as a single standalone Mac app that runs immediately with no software, dependencies, or Python setup required.

---

## 🚀 Quick Start & Pipeline Stages

The suite follows a high-speed, modular 4-stage pipeline:

```text
┌────────────────────────────────────────────────────────┐
│ Stage 0: True Content Deduplication (Krokiet Hashing)  │  --> (--dedup-only)
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ Stage 1: Lossless EXIF Repair & Live Photo Synthesis   │  --> (--prepare-only)
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ Stage 2: High-Speed Apple Photos Streaming Ingestion   │  --> (--import-only)
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ Stage 3: iCloud CloudKit Upload Telemetry              │  --> (--icloud-status)
└────────────────────────────────────────────────────────┘
```

---

### 1. Full End-to-End Migration (Default)
Runs Stage 0 $\rightarrow$ Stage 1 $\rightarrow$ Stage 2 $\rightarrow$ Stage 3 sequentially:
```bash
./dist/photos-icloud-migrator -y
```

---

### 2. Stage 0 Only: True Content Deduplication (Krokiet / Czkawka)
Discovers byte-level duplicate clusters and marks redundant copies to skip unnecessary work:
```bash
./dist/photos-icloud-migrator --dedup-only
```

---

### 3. Stage 1 Only: Fix Metadata & Pair Live Photos
Prepares all dates, GPS coordinates, and Live Photo pairs without opening Apple Photos:
```bash
./dist/photos-icloud-migrator --prepare-only
```

---

### 4. Stage 2 Only: Stream Import into Apple Photos
Streams all prepared files directly into Apple Photos in batches:
```bash
./dist/photos-icloud-migrator --import-only
```

---

### 5. Pre-Flight Dry Run
Scans your environment and reports exact photo/video counts without making any changes:
```bash
./dist/photos-icloud-migrator --dry-run
```

---

### 7. Check or Monitor iCloud Upload Status
Inspects Apple Photos' internal sync tables to track background iCloud uploads:
```bash
# One-time snapshot report
./dist/photos-icloud-migrator --icloud-status

# Live monitoring for 5 minutes (300 seconds)
./dist/photos-icloud-migrator --icloud-status 300
```

---

## 📁 Repository Structure

```text
Photos-to-iCloud/
├── dist/                              # Standalone pre-compiled distribution
│   └── photos-icloud-migrator         # Standalone universal executable (Zero Python Required)
│
├── photos_icloud_migrator.py          # Primary Python application & CLI source
├── README.md                          # User guide and CLI reference
├── AGENTS.md                          # Contributor & AI agent guidelines with upstream credits
│
├── bin/                               # Bundled standalone tooling
│   ├── exiftool                       # Pre-bundled ExifTool executable (v13.59)
│   └── lib/                           # Perl Image::ExifTool module libraries
│
├── core/                              # Modular engine components
│   ├── schema_adapter.py              # Dynamic Apple Photos SQLite schema adapter
│   ├── permissions_checker.py         # macOS Accessibility trust validator
│   ├── live_photo_synthesizer.py      # QuickTime ContentIdentifier Live Photo pairing
│   └── krokiet_duplicate_finder.py    # Krokiet/Czkawka progressive multi-tier duplicate engine
│
└── docs/                              # Consolidated technical documentation
    └── SPECIFICATION.md               # Master architecture spec & verification guide
```

| File / Folder | Purpose |
| :--- | :--- |
| [`dist/photos-icloud-migrator`](dist/photos-icloud-migrator) | **Standalone Binary**: Zero-dependency universal macOS executable with embedded runtime. |
| [`photos_icloud_migrator.py`](photos_icloud_migrator.py) | **Primary Executable**: Finder pickers, pre-flight briefing, batch ingestion, failed quarantine, and live TUI. |
| [`AGENTS.md`](AGENTS.md) | **Agent & Contributor Guide**: Codebase standards, error invariants, quality gates, and upstream credits. |
| [`bin/exiftool`](bin/exiftool) | **Bundled EXIF Engine**: Standalone local ExifTool binary and Perl modules by Phil Harvey. |
| [`core/schema_adapter.py`](core/schema_adapter.py) | **Dynamic Database Adapter**: Safe read-only queries across macOS versions. |
| [`core/permissions_checker.py`](core/permissions_checker.py) | **Accessibility Checker**: Validates System Events permissions for the modal watchdog. |
| [`core/live_photo_synthesizer.py`](core/live_photo_synthesizer.py) | **Live Photo Pairing Engine**: Links Takeout still and motion clips via QuickTime Content Identifiers. |
| [`core/krokiet_duplicate_finder.py`](core/krokiet_duplicate_finder.py) | **Krokiet Duplicate Engine**: 3-Tier progressive hashing inspired by `qarmin/czkawka`. |
| [`docs/SPECIFICATION.md`](docs/SPECIFICATION.md) | **Master Specification**: Consolidated architecture blueprint, component breakdown, and benchmarks. |

---

## 👥 Authors

* **Antigravity and junoonx**

---

## ⚙️ CLI Flags Reference

| Option | Default | Description |
| :--- | :--- | :--- |
| `--source-dir` | `~/Pictures/Takeout` | Source directory containing media files (prompts Finder if missing). |
| `--library-db` | `~/Pictures/Photos Library.photoslibrary/...` | Path to `Photos.sqlite` or `.photoslibrary` (prompts Finder if missing). |
| `--state-db` | `<source-dir>/batchimport_sqlite.db` | SQLite tracking database path. |
| `--batch-size` | `100` | Number of files per import batch. |
| `--delay` | `1` | Delay in seconds between batches. |
| `--max-files` | `None` | Limit total files processed (useful for testing). |
| `--dry-run` | `False` | Performs pre-flight and inventory scan without importing. |
| `--dedup-only` | `False` | Run Stage 0 only: Krokiet progressive true deduplication without importing. |
| `--prepare-only` | `False` | Run Stage 1 only: EXIF repair & Live Photo pairing without importing. |
| `--import-only` | `False` | Run Stage 2 only: Stream prepared files directly into Apple Photos. |
| `--skip-dedup` | `False` | Skip Stage 0 deduplication and proceed directly to Stage 1. |
| `--krokiet-dedup` | `False` | Run Krokiet/Czkawka 3-tier progressive duplicate analysis report. |
| `--interactive`, `-i` | `False` | Force macOS Finder folder/library picker dialogs. |
| `--yes`, `-y` | `False` | Automatically acknowledge pre-flight briefing prompt. |
| `--icloud-status [SEC]` | `None` | Check iCloud sync status (snapshot report if omitted, or live monitor for N seconds). |
