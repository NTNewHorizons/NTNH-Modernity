#!/usr/bin/env python3
"""
clean_assets.py — Resource Pack Asset Cleaner
Scans mods/ for mcmod.info files, collects all known modids,
then compares against assets/ folders and moves unused ones to a backup.

Usage:
    python clean_assets.py                  # Run from resourcepack root
    python clean_assets.py --mods ../mods   # Custom mods path
    python clean_assets.py --help
"""

import argparse
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Folders that must NEVER be removed, regardless of mcmod.info contents.
# These are vanilla namespaces, Forge internals, or shared library domains.
# ─────────────────────────────────────────────────────────────────────────────
PERMANENT_KEEP = {
    "minecraft",
    "forge",
    "fml",
    "mcp",
    "realmsapi",
    # Common shared-domain libraries used by multiple mods
    "cofh",       # CoFH Core / Thermal series shared domain
    "ic2",        # IndustrialCraft 2
    "nei",        # Not Enough Items
}


def parse_args():
    p = argparse.ArgumentParser(description="Clean unused asset folders from a resource pack.")
    p.add_argument(
        "--mods",
        default="mods",
        help="Path to the mods folder (default: ./mods)",
    )
    p.add_argument(
        "--assets",
        default="assets",
        help="Path to the resource pack assets folder (default: ./assets)",
    )
    p.add_argument(
        "--backup",
        default="assets_backup_unused",
        help="Folder to move unused assets into (default: ./assets_backup_unused)",
    )
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# mcmod.info parsing
# ─────────────────────────────────────────────────────────────────────────────

def extract_modids_from_mcmod_info(text: str) -> list[str]:
    """Parse mcmod.info JSON (handles both list and dict-wrapped formats)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    entries = []
    if isinstance(data, list):
        # Flat array format: [{modid: ...}, ...]
        entries = data
    elif isinstance(data, dict):
        # Wrapped format: {modListVersion: 2, modList: [...]}
        entries = data.get("modList", [])

    modids = []
    for entry in entries:
        if isinstance(entry, dict):
            mid = entry.get("modid") or entry.get("modId")
            if mid and isinstance(mid, str):
                modids.append(mid.strip().lower())
    return modids


def scan_jar(jar_path: Path) -> tuple[list[str], bool]:
    """
    Open a jar and extract modids from mcmod.info.
    Returns (modids, has_mcmod_info).
    """
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names_lower = {n.lower(): n for n in zf.namelist()}
            if "mcmod.info" not in names_lower:
                return [], False
            with zf.open(names_lower["mcmod.info"]) as f:
                text = f.read().decode("utf-8", errors="replace")
            return extract_modids_from_mcmod_info(text), True
    except (zipfile.BadZipFile, OSError):
        return [], False


def scan_mods_folder(mods_path: Path) -> tuple[set[str], list[str], list[str]]:
    """
    Walk mods_path and collect every modid found across all jars.
    Returns:
        all_modids      — set of every modid seen
        no_mcmod_jars   — jar names with no mcmod.info (warnings)
        bad_jars        — jars that couldn't be opened
    """
    all_modids: set[str] = set()
    no_mcmod_jars: list[str] = []
    bad_jars: list[str] = []

    jar_files = sorted(mods_path.rglob("*.jar"))
    if not jar_files:
        print(f"  [!] No .jar files found in {mods_path}")

    for jar in jar_files:
        modids, has_mcmod = scan_jar(jar)
        if not has_mcmod:
            no_mcmod_jars.append(jar.name)
        elif not modids:
            bad_jars.append(jar.name)  # has mcmod.info but no parseable modids
        else:
            all_modids.update(modids)

    return all_modids, no_mcmod_jars, bad_jars


# ─────────────────────────────────────────────────────────────────────────────
# Main logic
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    mods_path   = Path(args.mods).resolve()
    assets_path = Path(args.assets).resolve()
    backup_path = Path(args.backup).resolve()

    # ── Sanity checks ──────────────────────────────────────────────────────
    if not mods_path.is_dir():
        sys.exit(f"[ERROR] Mods folder not found: {mods_path}")
    if not assets_path.is_dir():
        sys.exit(f"[ERROR] Assets folder not found: {assets_path}")

    print("=" * 72)
    print("  Resource Pack Asset Cleaner")
    print("=" * 72)
    print(f"  Mods   : {mods_path}")
    print(f"  Assets : {assets_path}")
    print(f"  Backup : {backup_path}")
    print()

    # ── Step 1: Collect modids from all jars ───────────────────────────────
    print("── Step 1: Scanning mods for mcmod.info ──────────────────────────")
    all_modids, no_mcmod_jars, bad_jars = scan_mods_folder(mods_path)

    # Add permanent keeps to the known-good set
    all_modids.update(PERMANENT_KEEP)

    print(f"  Found {len(all_modids) - len(PERMANENT_KEEP)} modids from jars "
          f"(+ {len(PERMANENT_KEEP)} permanent keeps).")

    if no_mcmod_jars:
        print(f"\n  ⚠  {len(no_mcmod_jars)} jar(s) had NO mcmod.info "
              "(their assets, if any, won't be auto-detected):")
        for name in sorted(no_mcmod_jars):
            print(f"       • {name}")

    if bad_jars:
        print(f"\n  ⚠  {len(bad_jars)} jar(s) had mcmod.info but zero parseable modids:")
        for name in sorted(bad_jars):
            print(f"       • {name}")

    # ── Step 2: List asset folders ─────────────────────────────────────────
    print("\n── Step 2: Listing asset folders ─────────────────────────────────")
    asset_folders = sorted(
        d.name for d in assets_path.iterdir() if d.is_dir()
    )
    print(f"  Found {len(asset_folders)} asset folder(s).")

    # ── Step 3: Classify each folder ──────────────────────────────────────
    kept:    list[str] = []   # has a matching modid → safe
    removed: list[str] = []   # no matching modid → will be moved
    guarded: list[str] = []   # in PERMANENT_KEEP

    for folder in asset_folders:
        folder_lower = folder.lower()
        if folder_lower in PERMANENT_KEEP:
            guarded.append(folder)
        elif folder_lower in all_modids:
            kept.append(folder)
        else:
            removed.append(folder)

    # ── Step 4: Print classification ──────────────────────────────────────
    print("\n── Step 3: Classification results ────────────────────────────────")

    print(f"\n  ✅  KEPT ({len(kept)}) — matched a known modid:")
    for f in kept:
        print(f"       {f}")

    print(f"\n  🔒  PERMANENT KEEP ({len(guarded)}) — vanilla / Forge core:")
    for f in guarded:
        print(f"       {f}")

    print(f"\n  ⚠   UNMATCHED / WARNING ({len(removed)}) — no modid found for these.")
    print("       These may be modid→folder aliases (e.g. 'appeng' for")
    print("       'appliedenergistics2'). Review carefully before confirming!")
    print()
    for f in removed:
        print(f"       • {f}")

    if not removed:
        print("\n  Nothing to move. Your resource pack is already clean! 🎉")
        return

    # ── Step 5: Dry-run summary ────────────────────────────────────────────
    print("\n── Step 4: Dry-run summary ───────────────────────────────────────")
    print(f"\n  The following {len(removed)} folder(s) would be MOVED to:")
    print(f"  {backup_path}\n")
    for f in removed:
        src = assets_path / f
        dst = backup_path / f
        print(f"    {src}")
        print(f"    → {dst}\n")

    # ── Step 6: Confirm ────────────────────────────────────────────────────
    print("─" * 72)
    print("  Review the list above carefully.")
    print("  Any folder listed under UNMATCHED might be a modid alias —")
    print("  if you're unsure, press N and investigate first.")
    print("─" * 72)
    answer = input("\n  Proceed? Move unmatched folders to backup? [y/N]: ").strip().lower()

    if answer != "y":
        print("\n  Aborted. Nothing was changed.")
        return

    # ── Step 7: Move ───────────────────────────────────────────────────────
    backup_path.mkdir(parents=True, exist_ok=True)
    errors = []

    for folder_name in removed:
        src = assets_path / folder_name
        dst = backup_path / folder_name
        try:
            if dst.exists():
                shutil.rmtree(dst)
            shutil.move(str(src), str(dst))
            print(f"  Moved: {folder_name}")
        except OSError as e:
            errors.append((folder_name, str(e)))
            print(f"  [ERROR] Could not move {folder_name}: {e}")

    print()
    if errors:
        print(f"  ⚠  {len(errors)} folder(s) failed to move (see above).")
    else:
        print(f"  ✅ Done! {len(removed)} folder(s) moved to:")
        print(f"     {backup_path}")
        print()
        print("  If you later discover a folder was moved by mistake,")
        print("  simply move it back from the backup folder.")


if __name__ == "__main__":
    main()
