"""Comprehensive Evaluation Runner for RCBC Field-Remark Classifier (Step 5).

Runs evaluation across:
  - 2026-09-25 (178 rows) with 'default' and 'rcbc_v2'
  - 2026-09-28 (106 rows) with 'default' and 'rcbc_v2'

Generates immutable evaluated Excel files in storage/evaluation/ with Step 2 logging columns.
Computes CSU %, RFD %, Full Agreement %, flips in both directions, and cohort analyses.
"""

import json
from pathlib import Path
from typing import Any

from engine.summarizer import TwoTierRemarksSummarizer
from src.mc03.services.remarks_lab.parser import parse_field_result_sheet
from src.mc03.services.remarks_lab.pipeline import RemarksLabPipeline

FILES = {
    "09-25": Path(r"C:\Users\SPM\Downloads\RCBC_FIELD_EVALUATED_2026-09-25.xlsx"),
    "09-28": Path(r"C:\Users\SPM\Downloads\RCBC_FIELD_EVALUATED_2026-09-28.xlsx"),
}

PROFILES = ["default", "rcbc_v2"]

OUTPUT_DIR = Path("storage/evaluation")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

COHORTS = {
    "Residency / Denied-Entry": [22217, 22223, 22229, 22245, 20147, 20199, 20351, 20196],
    "Recon": [22190, 22194, 22195, 22196, 22247],
    "Unit Status": [20217, 20210, 22233, 22197, 22252, 20135, 20138, 20155, 20170],
    "TP Dispositions": [22191, 22192],
    "Instruction Misses (Niece / Maid)": [20205, 20152],
    "Empty RFD on FFV": [20145, 20232, 20266, 20314, 20319, 20340],
}


def run_single_eval(file_key: str, file_path: Path, profile_id: str):
    print(f"\n========================================================")
    print(f"Running evaluation: {file_key} ({file_path.name}) with profile: {profile_id}")
    print(f"========================================================")
    raw_rows = parse_field_result_sheet(file_path, test_mode=True)
    summarizer = TwoTierRemarksSummarizer(profile_id=profile_id)
    pipeline = RemarksLabPipeline(two_tier_summarizer=summarizer)

    def _progress(d: dict[str, Any]):
        msg = d.get("message", "")
        pct = d.get("percent", 0)
        if pct in (25, 50, 75, 95):
            print(f"  [{pct}%] {msg}")

    report = pipeline.process_rows(raw_rows, test_mode=True, run_ai=True, progress_callback=_progress)

    # Save immutable Excel file
    out_name = f"RCBC_FIELD_EVALUATED_{'2026-' + file_key}_{profile_id}.xlsx"
    out_path = OUTPUT_DIR / out_name
    excel_buf = pipeline.export_to_excel(report)
    with open(out_path, "wb") as f:
        f.write(excel_buf.getvalue())
    print(f"  Saved evaluated Excel: {out_path} ({out_path.stat().st_size} bytes)")

    # Metrics
    total = len(report.rows)
    csu_matches = sum(1 for r in report.rows if r.csu_match)
    rfd_matches = sum(1 for r in report.rows if r.rfd_match)
    full_matches = sum(1 for r in report.rows if r.csu_match and r.rfd_match)

    csu_pct = round(csu_matches / total * 100, 1) if total else 0.0
    rfd_pct = round(rfd_matches / total * 100, 1) if total else 0.0
    full_pct = round(full_matches / total * 100, 1) if total else 0.0

    print(f"  Total: {total} | CSU: {csu_matches}/{total} ({csu_pct}%) | RFD: {rfd_matches}/{total} ({rfd_pct}%) | Full: {full_matches}/{total} ({full_pct}%)")

    return {
        "report": report,
        "total": total,
        "csu_matches": csu_matches,
        "rfd_matches": rfd_matches,
        "full_matches": full_matches,
        "csu_pct": csu_pct,
        "rfd_pct": rfd_pct,
        "full_pct": full_pct,
        "rows_by_idx": {r.row_index: r for r in report.rows},
    }


def analyze_cohorts(results: dict[str, dict[str, Any]]):
    all_cohort_data = {}
    for cohort_name, row_ids in COHORTS.items():
        cohort_rows = []
        for rid in row_ids:
            # find which file has this row
            found_data = None
            for fk in ["09-25", "09-28"]:
                def_res = results[fk]["default"]["rows_by_idx"].get(rid)
                v2_res = results[fk]["rcbc_v2"]["rows_by_idx"].get(rid)
                if def_res or v2_res:
                    row_obj = v2_res or def_res
                    found_data = {
                        "row_index": rid,
                        "file": fk,
                        "concat": row_obj.concat_val,
                        "remark": row_obj.raw_remarks,
                        "da_csu": row_obj.manual_csu,
                        "da_rfd": row_obj.manual_rfd,
                        "default_csu": def_res.predicted_csu if def_res else "N/A",
                        "default_rfd": def_res.predicted_rfd if def_res else "N/A",
                        "default_csu_match": def_res.csu_match if def_res else False,
                        "default_rfd_match": def_res.rfd_match if def_res else False,
                        "v2_csu": v2_res.predicted_csu if v2_res else "N/A",
                        "v2_rfd": v2_res.predicted_rfd if v2_res else "N/A",
                        "v2_csu_match": v2_res.csu_match if v2_res else False,
                        "v2_rfd_match": v2_res.rfd_match if v2_res else False,
                        "csu_reasoning": v2_res.csu_reasoning if v2_res else "",
                        "rfd_reasoning": v2_res.rfd_reasoning if v2_res else "",
                    }
                    break
            if found_data:
                cohort_rows.append(found_data)
            else:
                cohort_rows.append({"row_index": rid, "status": "Not found in 09-25 or 09-28"})
        all_cohort_data[cohort_name] = cohort_rows
    return all_cohort_data


def analyze_flips(def_res: dict[str, Any], v2_res: dict[str, Any]):
    def_rows = def_res["rows_by_idx"]
    v2_rows = v2_res["rows_by_idx"]

    csu_improved = []
    csu_regressed = []
    rfd_improved = []
    rfd_regressed = []
    full_improved = []
    full_regressed = []

    for rid, d_r in def_rows.items():
        v_r = v2_rows.get(rid)
        if not v_r:
            continue

        # CSU
        if not d_r.csu_match and v_r.csu_match:
            csu_improved.append({
                "row_index": rid,
                "concat": v_r.concat_val,
                "da_csu": v_r.manual_csu,
                "default": d_r.predicted_csu,
                "rcbc_v2": v_r.predicted_csu,
            })
        elif d_r.csu_match and not v_r.csu_match:
            csu_regressed.append({
                "row_index": rid,
                "concat": v_r.concat_val,
                "da_csu": v_r.manual_csu,
                "default": d_r.predicted_csu,
                "rcbc_v2": v_r.predicted_csu,
                "reasoning": v_r.csu_reasoning,
            })

        # RFD
        if not d_r.rfd_match and v_r.rfd_match:
            rfd_improved.append({
                "row_index": rid,
                "concat": v_r.concat_val,
                "da_rfd": v_r.manual_rfd,
                "default": d_r.predicted_rfd,
                "rcbc_v2": v_r.predicted_rfd,
            })
        elif d_r.rfd_match and not v_r.rfd_match:
            rfd_regressed.append({
                "row_index": rid,
                "concat": v_r.concat_val,
                "da_rfd": v_r.manual_rfd,
                "default": d_r.predicted_rfd,
                "rcbc_v2": v_r.predicted_rfd,
                "reasoning": v_r.rfd_reasoning,
            })

        # Full
        d_full = d_r.csu_match and d_r.rfd_match
        v_full = v_r.csu_match and v_r.rfd_match
        if not d_full and v_full:
            full_improved.append(rid)
        elif d_full and not v_full:
            full_regressed.append(rid)

    return {
        "csu_improved": csu_improved,
        "csu_regressed": csu_regressed,
        "rfd_improved": rfd_improved,
        "rfd_regressed": rfd_regressed,
        "full_improved": full_improved,
        "full_regressed": full_regressed,
    }


def main():
    results = {}
    for fk, fp in FILES.items():
        results[fk] = {}
        for pid in PROFILES:
            results[fk][pid] = run_single_eval(fk, fp, pid)

    # Flips analysis
    flips_data = {}
    for fk in FILES:
        flips_data[fk] = analyze_flips(results[fk]["default"], results[fk]["rcbc_v2"])

    cohorts_data = analyze_cohorts(results)

    # Save summary report to JSON
    summary = {
        "metrics": {
            fk: {
                pid: {
                    "total": results[fk][pid]["total"],
                    "csu_pct": results[fk][pid]["csu_pct"],
                    "rfd_pct": results[fk][pid]["rfd_pct"],
                    "full_pct": results[fk][pid]["full_pct"],
                    "csu_matches": results[fk][pid]["csu_matches"],
                    "rfd_matches": results[fk][pid]["rfd_matches"],
                    "full_matches": results[fk][pid]["full_matches"],
                }
                for pid in PROFILES
            }
            for fk in FILES
        },
        "flips": flips_data,
        "cohorts": cohorts_data,
    }

    with open("storage/evaluation/eval_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n========================================================")
    print("EVALUATION SUMMARY")
    print("========================================================")
    for fk in FILES:
        d = summary["metrics"][fk]["default"]
        v2 = summary["metrics"][fk]["rcbc_v2"]
        print(f"\n--- File {fk} (Total {d['total']} rows) ---")
        print(f"  CSU Accuracy:    Default: {d['csu_pct']}% ({d['csu_matches']}/{d['total']}) -> rcbc_v2: {v2['csu_pct']}% ({v2['csu_matches']}/{v2['total']}) [Δ {round(v2['csu_pct'] - d['csu_pct'], 1)}%]")
        print(f"  RFD Accuracy:    Default: {d['rfd_pct']}% ({d['rfd_matches']}/{d['total']}) -> rcbc_v2: {v2['rfd_pct']}% ({v2['rfd_matches']}/{v2['total']}) [Δ {round(v2['rfd_pct'] - d['rfd_pct'], 1)}%]")
        print(f"  Full Agreement:  Default: {d['full_pct']}% ({d['full_matches']}/{d['total']}) -> rcbc_v2: {v2['full_pct']}% ({v2['full_matches']}/{v2['total']}) [Δ {round(v2['full_pct'] - d['full_pct'], 1)}%]")
        f_data = flips_data[fk]
        print(f"  Flips: CSU Improved={len(f_data['csu_improved'])}, CSU Regressed={len(f_data['csu_regressed'])}")
        print(f"  Flips: RFD Improved={len(f_data['rfd_improved'])}, RFD Regressed={len(f_data['rfd_regressed'])}")
        print(f"  Flips: Full Agreement Improved={len(f_data['full_improved'])}, Full Agreement Regressed={len(f_data['full_regressed'])}")


if __name__ == "__main__":
    main()
