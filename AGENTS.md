# Photos-to-iCloud – Contributor & AI Agent Guide

## Language & Tone

All code, comments, commit messages, and documentation must be written in **English**.

---

## Python & Codebase Style

Applies across the root script (`photos_icloud_migrator.py`) and all modules in `core/`.

### 1. Formatting & Code Shape
- **Line Length**: Max 120 characters.
- **Function Focus**: Keep functions focused (< 40–50 lines). Extract logical sub-steps into dedicated, descriptive helpers (e.g. `_resolve_exiftool()`, `clean_stem()`).
- **Naming by Intent**: Name variables by their semantic meaning, not their container (e.g., `duplicate_paths`, not `list_of_dups_var`).
- **File Length**: Keep modular core components under 500 lines.
- **Comments**: Keep comments minimal and focused on *why* (e.g., AppleScript modal workarounds, macOS Core Data schema differences), not *what* the code trivially does.

### 2. Error Handling & Robustness
- **Never Silently Swallow Failures**: Avoid bare `except:` or `except Exception: pass` without logging or explicit fallback semantics. Swallowed exceptions conceal schema mismatches and cascade into performance stalls.
- **Context-Rich Errors**: When catching filesystem, database, or AppleScript errors, always log the relevant file path or operation context.
- **Safe Fallbacks**: Ingestion failures must route unimportable or corrupted items safely to `Failed/` with structured SQLite audit logging without halting batch ingestion.

### 3. Database & State Invariants
- **SQLite Concurrency & Hygiene**: Always open and close SQLite connections cleanly or use context managers (`with sqlite3.connect(...)`).
- **Indexed Lookups**: Batch queries against `Photos.sqlite` must use indexed lookups with parameter binding (`IN (?, ?, ...)`), avoiding full-table scans.
- **State Machine Integrity**: Assets transition strictly between deterministic states: `PENDING` $\rightarrow$ `ALREADY_EXISTS` | `IMPORTED` | `FAILED_QUARANTINED`.

### 4. macOS & Apple Photos Interoperability
- **Apple Events Safety**: Never hardcode AppleScript timeouts or assume instant UI execution. Always couple bulk AppleScript calls with the background `modal_watchdog` thread.
- **Schema Dynamism**: Never hardcode macOS-specific SQLite columns (`ZASSET` vs `ZGENERICASSET`, `ZORIGINALFILENAME` vs `ZFILENAME`). All library queries must execute through `PhotosSchemaAdapter`.
- **Live Photo 2-to-1 Pairing**: Always preserve the paired relationship between `.JPG`/`.HEIC` and `.MOV`/`.MP4` via QuickTime `ContentIdentifier` UUIDs.

---

## Decoupled 4-Stage Architecture & Credits

Every stage in the pipeline must acknowledge and credit the upstream open-source tools and repositories that inspired or power the functionality:

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

1. **Stage 0 – True Content Deduplication**:
   - **Inspiration**: [**Czkawka & Krokiet** by `qarmin`](https://github.com/qarmin/czkawka).
   - **Technique**: 3-Tier progressive hashing (`File Size` $\rightarrow$ `4KB Head/Tail Hash` $\rightarrow$ `Full BLAKE2b Hash`) for ultra-fast byte-for-byte duplicate discovery across arbitrary folder trees.
2. **Stage 1 – Lossless Metadata Repair & Live Photo Synthesis**:
   - **Powered by**: [**ExifTool** by Phil Harvey](https://exiftool.org).
   - **Technique**: Deep EXIF metadata parsing, Google JSON sidecar reconciliation, and QuickTime `com.apple.quicktime.content.identifier` atom injection for native Apple Live Photo fusion.
3. **Stage 2 – High-Speed Apple Photos Streaming Ingestion**:
   - **Powered by**: macOS Apple Events / AppleScript Automation (`osascript`) coupled with dynamic Core Data schema adaptation (`Photos.sqlite`).
4. **Stage 3 – iCloud CloudKit Telemetry**:
   - **Powered by**: macOS CloudKit sync state inspection (`Photos.sqlite` & `syncstatus.plist`).

---

## Non-Negotiable Project Invariants

1. **Zero Data Loss Guarantee**: The suite is strictly non-destructive. Source files are never permanently deleted; unimportable files are safely quarantined in `Failed/`.
2. **Standalone Universal Portability**: The project must build and execute as a self-contained macOS universal binary via PyInstaller (`dist/photos-icloud-migrator`) with zero external runtime dependencies.
3. **Transparent Credits**: Always credit upstream libraries and inspirations in tool outputs, documentation, and source headers.

---

## Baseline Quality Gate

Before submitting any code changes, verify the following:

1. **Syntax & Unit Sanity**:
   ```bash
   python3 -m py_compile photos_icloud_migrator.py core/*.py
   ```
2. **Pre-Flight Dry Run Check**:
   ```bash
   ./dist/photos-icloud-migrator --dry-run -y
   ```
3. **Krokiet Duplicate Verification**:
   ```bash
   ./dist/photos-icloud-migrator --krokiet-dedup
   ```
4. **Standalone Binary Rebuild**:
   ```bash
   pyinstaller --onefile --name photos-icloud-migrator --add-data "bin:bin" --add-data "core:core" photos_icloud_migrator.py
   ```
