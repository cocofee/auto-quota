"""
涓€閿鍏ュ畾棰濇暟鎹伐鍏?
鍔熻兘锛氭壂鎻忔寚瀹氱渷浠界洰褰曚笅鎵€鏈墄lsx 鈫?鑷姩璇嗗埆specialty 鈫?瀵煎叆鏁版嵁搴?鈫?鐢熸垚瑙勫垯JSON 鈫?閲嶅缓绱㈠紩

榛樿澧為噺妯″紡锛氳嚜鍔ㄨ烦杩囧凡瀵煎叆鐨勬枃浠讹紝鍙鐞嗘柊澧炴枃浠躲€?鐢?--full 寮哄埗鍏ㄩ噺閲嶅銆?
鐢ㄦ硶锛?    python tools/import_all.py --province "鍖椾含2024"        # 澧為噺瀵煎叆锛堥粯璁わ級
    python tools/import_all.py --full                       # 鍏ㄩ噺閲嶅
    python tools/import_all.py --skip-index                 # 璺宠繃绱㈠紩閲嶅缓
"""

import time
import argparse
from pathlib import Path

# 娣诲姞椤圭洰鏍圭洰褰曞埌璺緞
PROJECT_ROOT = Path(__file__).parent.parent

from loguru import logger
import config
from src.quota_db import QuotaDB, detect_specialty_from_excel


def _resolve_import_province(name: str = None) -> str:
    """瑙ｆ瀽瀵煎叆鐩爣鐪佷唤锛屼紭鍏堝尮閰?data/quota_data 涓湡瀹炲彲鐢ㄧ殑鐪佷唤鐩綍銆?
    濮旀墭缁?config.resolve_province(scope="data") 缁熶竴澶勭悊锛?    閬垮厤澶氬缁存姢閲嶅鐨勮В鏋愰€昏緫銆?    """
    return config.resolve_province(name, interactive=False, scope="data")


def _filter_new_files(xlsx_files: list[Path], db: QuotaDB) -> list[Path]:
    """Filter files for incremental import.

    Priority:
    1) Compare by content hash (preferred).
    2) Fall back to size/mtime when old records have no hash.
    """
    import hashlib

    history = db.get_import_history()
    success_history = [h for h in history if h.get("status", "success") != "error"]
    path_map = {h["file_path"]: h for h in success_history}
    hash_set = {h["file_hash"] for h in success_history if h.get("file_hash")}

    new_files = []
    skipped_files = []
    modified_files = []

    for f in xlsx_files:
        full_path = str(f.resolve())
        try:
            hasher = hashlib.md5()
            with open(f, "rb") as fh:
                for chunk in iter(lambda: fh.read(8192), b""):
                    hasher.update(chunk)
            current_hash = hasher.hexdigest()
        except OSError as e:
            print(f"    [WARNING] cannot read file {f.name}: {e}; treat as new file")
            new_files.append(f)
            continue

        prev = path_map.get(full_path)

        if prev and prev.get("file_hash"):
            if prev["file_hash"] == current_hash:
                skipped_files.append(f)
            else:
                modified_files.append(f)
        elif current_hash in hash_set:
            skipped_files.append(f)
            print(f"    (same content imported from another path: {f.name})")
        elif prev is not None:
            stat = f.stat()
            if prev["file_size"] == stat.st_size and abs(prev["file_mtime"] - stat.st_mtime) < 1:
                skipped_files.append(f)
            else:
                modified_files.append(f)
        else:
            new_files.append(f)

    if skipped_files:
        print(f"  [SKIP] already imported: {len(skipped_files)} files")
        for f in skipped_files:
            print(f"    - {f.name}")

    if modified_files:
        print(f"  [REIMPORT] modified: {len(modified_files)} files")
        for f in modified_files:
            print(f"    - {f.name}")

    if new_files:
        print(f"  [NEW] pending import: {len(new_files)} files")
        for f in new_files:
            print(f"    - {f.name}")

    print()
    return new_files + modified_files


def main():
    parser = argparse.ArgumentParser(description="One-click quota data import")
    parser.add_argument("--province", type=str, default=None,
                        help="Province/version name, for example 'Beijing 2024'")
    parser.add_argument("--skip-index", action="store_true",
                        help="Skip rebuilding BM25/vector indexes")
    parser.add_argument("--full", action="store_true",
                        help="Full re-import (ignore import history)")
    args = parser.parse_args()

    try:
        province = _resolve_import_province(args.province)
    except ValueError as e:
        print(f"Error: {e}")
        return

    mode_label = "full" if args.full else "incremental"
    print("=" * 60)
    print(f"Quota import ({mode_label})")
    print(f"Province: {province}")
    print("=" * 60)
    print()

    quota_dir = config.get_quota_data_dir(province)
    if not quota_dir.exists():
        quota_dir = config.QUOTA_DATA_DIR
        if not quota_dir.exists():
            print(f"Error: quota directory does not exist: {quota_dir}")
            return

    xlsx_files = sorted(quota_dir.glob("*.xlsx"))
    if not xlsx_files:
        print(f"Error: no .xlsx files found in: {quota_dir}")
        return

    print(f"Scan dir: {quota_dir}")
    print(f"Found {len(xlsx_files)} xlsx files")
    print()

    db = QuotaDB(province=province)

    history = db.get_import_history()
    stats = db.get_stats()
    if not args.full and not history and stats.get("total", 0) > 0:
        total_existing = stats.get("total", 0)
        print(f"[WARN] legacy database detected without import history ({total_existing} rows)")
        print("Switching to full mode in 3 seconds (Ctrl+C to cancel)...")
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            print("\nCanceled.")
            return
        print()
        args.full = True

    if args.full:
        db.clear_import_history()
        files_to_import = xlsx_files
        print(f"Full mode: import all {len(files_to_import)} files")
        print()
    else:
        files_to_import = _filter_new_files(xlsx_files, db)
        if not files_to_import:
            print("All files already imported. Use --full to force re-import.")
            return

    print("Prechecking workbook format...")
    valid_files = []
    for xlsx_file in files_to_import:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(xlsx_file), read_only=True, data_only=True)
            sheet_count = len(wb.sheetnames)
            wb.close()
            if sheet_count == 0:
                print(f"  [SKIP] {xlsx_file.name}: empty workbook")
                continue
            valid_files.append(xlsx_file)
        except Exception as e:
            print(f"  [SKIP] {xlsx_file.name}: invalid workbook ({e})")
            continue

    if not valid_files:
        print("No valid files to import.")
        return
    if len(valid_files) < len(files_to_import):
        skipped = len(files_to_import) - len(valid_files)
        print(f"Precheck passed: {len(valid_files)} files, skipped: {skipped}")
    else:
        print(f"Precheck passed: {len(valid_files)} files")
    files_to_import = valid_files
    print()

    print(f"[Step 1] Import quotas into DB ({len(files_to_import)} files)...")
    imported = {}

    if args.full:
        cleared_specialties = set()
        for xlsx_file in files_to_import:
            specialty = detect_specialty_from_excel(str(xlsx_file))
            is_first = specialty not in cleared_specialties
            mode = "clear+import" if is_first else "append"
            print(f"  Import: {xlsx_file.name} -> specialty='{specialty}' ({mode})")
            try:
                count = db.import_excel(str(xlsx_file), specialty=specialty, clear_existing=is_first)
                cleared_specialties.add(specialty)
                imported[specialty] = imported.get(specialty, 0) + count
                try:
                    db.record_import(str(xlsx_file), specialty, count)
                except Exception as e:
                    print(f"    [WARN] failed to record import history: {e}")
                print(f"    Done: {count} rows")
            except Exception as e:
                print(f"    [ERROR] import failed: {e}")
                try:
                    db.record_import(str(xlsx_file), specialty, 0, status="error", error_msg=str(e)[:200])
                except Exception:
                    pass
    else:
        for xlsx_file in files_to_import:
            specialty = detect_specialty_from_excel(str(xlsx_file))
            print(f"  Import: {xlsx_file.name} -> specialty='{specialty}' (append)")
            try:
                count = db.import_excel(str(xlsx_file), specialty=specialty, clear_existing=False)
                imported[specialty] = imported.get(specialty, 0) + count
                try:
                    db.record_import(str(xlsx_file), specialty, count)
                except Exception as e:
                    print(f"    [WARN] failed to record import history: {e}")
                print(f"    Done: {count} rows")
            except Exception as e:
                print(f"    [ERROR] import failed: {e}")
                try:
                    db.record_import(str(xlsx_file), specialty, 0, status="error", error_msg=str(e)[:200])
                except Exception:
                    pass

    total = sum(imported.values())
    print(f"\nImported rows this run: {total}")
    for sp, cnt in imported.items():
        print(f"  {sp}: {cnt}")
    print()

    if total == 0:
        print("No rows imported successfully. Skip rule generation and index rebuild.")
        return

    print("[Step 2] Generate rule JSON files...")
    from tools.extract_quota_rules import process_all_chapters, generate_summary
    import json, os, tempfile

    rules_dir = PROJECT_ROOT / "data" / "quota_rules" / province
    rules_dir.mkdir(parents=True, exist_ok=True)

    for specialty in imported.keys():
        print(f"  Build rules for {specialty}...")
        rules = process_all_chapters(db, specialty=specialty)

        json_path = rules_dir / f"{specialty}定额规则.json"
        json_tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                prefix=f"{json_path.stem}_tmp_",
                dir=str(json_path.parent),
                encoding="utf-8",
                delete=False,
            ) as f:
                json_tmp = f.name
                json.dump(rules, f, ensure_ascii=False, indent=2)
            os.replace(json_tmp, json_path)
        finally:
            if json_tmp and Path(json_tmp).exists():
                try:
                    os.remove(json_tmp)
                except OSError:
                    pass

        summary_path = rules_dir / f"{specialty}定额规则_摘要.txt"
        summary = generate_summary(rules)
        summary_tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                prefix=f"{summary_path.stem}_tmp_",
                dir=str(summary_path.parent),
                encoding="utf-8",
                delete=False,
            ) as f:
                summary_tmp = f.name
                f.write(summary)
            os.replace(summary_tmp, summary_path)
        finally:
            if summary_tmp and Path(summary_tmp).exists():
                try:
                    os.remove(summary_tmp)
                except OSError:
                    pass

        meta = rules["meta"]
        print(
            f"    quotas={meta['total_quotas']}, families={meta['total_families']}, "
            f"standalone={meta['total_standalone']}"
        )
        print(f"    Saved: {json_path.name}")
    print()

    if args.skip_index:
        print("[Skip] Index rebuild (--skip-index)")
    else:
        print("[Step 3] Rebuild search indexes...")
        print("  Build BM25 index...")
        from src.bm25_engine import BM25Engine
        bm25 = BM25Engine(province=province)
        bm25.build_index()
        print(f"    Done: {len(bm25.quota_ids)} items")

        print("  Build vector index (may take longer)...")
        from src.vector_engine import VectorEngine
        vec = VectorEngine(province=province)
        vec.build_index()
        print("    Done")

    print()

    try:
        from src.experience_db import ExperienceDB
        exp_db = ExperienceDB(province=province)
        new_version = db.get_version()
        if new_version:
            stale_count = exp_db.mark_stale_experiences(province, new_version)
            if stale_count > 0:
                print(f"Marked stale experience records: {stale_count}")
    except Exception as e:
        logger.debug(f"Failed to mark stale experiences (non-blocking): {e}")

    print("=" * 60)
    print("Import completed")
    print("=" * 60)
    stats = db.get_stats()
    print(f"DB path: {db.db_path}")
    print(f"Total quotas: {stats['total']}")
    print(f"Total chapters: {stats['chapters']}")
    print(f"Total specialties: {stats['specialties']}")
    for sp, cnt in imported.items():
        print(f"  - {sp}: {cnt} (imported this run)")
    print()
    print(f"Rules path: data/quota_rules/{province}/*定额规则.json")
    if not args.skip_index:
        print("BM25 index: rebuilt")
        print("Vector index: rebuilt")


if __name__ == "__main__":
    main()
