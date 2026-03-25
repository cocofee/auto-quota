# -*- coding: utf-8 -*-
"""
准确率追踪 — 记录每次运行的关键指标，追踪系统是否在进化

核心指标：
- 经验库直通率（越高=系统越"聪明"，但如果错误率同步升则有问题）
- 高置信度比例（越高越好）
- 审核纠正率（越低越好，说明匹配准确）
- 用户手动修正率（最关键：下降=真的在变好）

用法：
  from src.accuracy_tracker import AccuracyTracker
  tracker = AccuracyTracker()
  tracker.record_run(stats, input_file, mode, province)  # 每次匹配后调用
  tracker.show_trend()                                    # 查看趋势
"""

import sqlite3
import time
from pathlib import Path

from loguru import logger

import config
from db.sqlite import connect_init as _db_connect_init


# 数据库路径：和经验库放一起
_DB_PATH = config.COMMON_DB_DIR / "run_history.db"
_KNOWLEDGE_LAYERS = ("ExperienceDB", "RuleKnowledge", "MethodCards")


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接，不存在则自动创建表。"""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _db_connect_init(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,           -- 运行日期 (YYYY-MM-DD)
            run_time TEXT NOT NULL,           -- 运行时间 (HH:MM:SS)
            input_file TEXT,                  -- 输入文件名
            mode TEXT,                        -- 匹配模式 (search/agent)
            province TEXT,                    -- 省份

            total INTEGER,                    -- 总清单项数
            matched INTEGER,                  -- 已匹配数
            high_conf INTEGER,                -- 高置信度(≥85)数
            mid_conf INTEGER,                 -- 中置信度(60-85)数
            low_conf INTEGER,                 -- 低置信度(<60)数
            exp_hits INTEGER,                 -- 经验库命中数
            review_rejected INTEGER DEFAULT 0, -- 审核规则拦截的经验库直通数

            elapsed REAL,                     -- 耗时（秒）
            created_at REAL                   -- 记录创建时间戳
        )
    """)
    # 审核记录表：记录Jarvis审核纠正的统计
    conn.execute("""
        CREATE TABLE IF NOT EXISTS review_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            run_time TEXT NOT NULL,
            input_file TEXT,
            province TEXT,

            total INTEGER,                    -- 总匹配条数
            auto_corrections INTEGER,         -- 自动纠正条数
            manual_items INTEGER,             -- 需人工处理条数
            measure_items INTEGER,            -- 措施项（不套定额）条数
            correct_count INTEGER,            -- 审核通过（无错）条数

            created_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_hit_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            run_time TEXT NOT NULL,
            input_file TEXT,
            mode TEXT,
            province TEXT,
            task_id TEXT DEFAULT '',
            layer TEXT NOT NULL,
            total_results INTEGER DEFAULT 0,
            hit_count INTEGER DEFAULT 0,
            direct_count INTEGER DEFAULT 0,
            assist_count INTEGER DEFAULT 0,
            high_conf_count INTEGER DEFAULT 0,
            low_risk_count INTEGER DEFAULT 0,
            green_count INTEGER DEFAULT 0,
            hint_count INTEGER DEFAULT 0,
            created_at REAL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_hit_history_created
        ON knowledge_hit_history(created_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_hit_history_layer_date
        ON knowledge_hit_history(layer, run_date)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_hit_result_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            run_time TEXT NOT NULL,
            input_file TEXT,
            mode TEXT,
            province TEXT,
            task_id TEXT DEFAULT '',
            result_index INTEGER NOT NULL,
            layer TEXT NOT NULL,
            object_ref TEXT DEFAULT '',
            hit_type TEXT NOT NULL,
            match_source TEXT DEFAULT '',
            confidence INTEGER DEFAULT 0,
            review_risk TEXT DEFAULT '',
            light_status TEXT DEFAULT '',
            created_at REAL
        )
    """)
    try:
        conn.execute("ALTER TABLE knowledge_hit_result_history ADD COLUMN object_ref TEXT DEFAULT ''")
    except Exception:
        pass
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_hit_result_history_task
        ON knowledge_hit_result_history(task_id, result_index, layer)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_hit_result_history_created
        ON knowledge_hit_result_history(created_at)
    """)
    conn.commit()
    return conn


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _extract_trace_steps(result: dict) -> list[dict]:
    trace = result.get("trace")
    if not isinstance(trace, dict):
        return []
    steps = trace.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def _summarize_knowledge_hits(results: list[dict]) -> dict[str, dict[str, int]]:
    summary = {
        layer: {
            "hit_count": 0,
            "direct_count": 0,
            "assist_count": 0,
            "high_conf_count": 0,
            "low_risk_count": 0,
            "green_count": 0,
            "hint_count": 0,
        }
        for layer in _KNOWLEDGE_LAYERS
    }

    for result in results or []:
        if not isinstance(result, dict):
            continue

        match_source = str(result.get("match_source", "") or "").strip().lower()
        confidence = _safe_int(result.get("confidence", 0))
        review_risk = str(result.get("review_risk", "") or "").strip().lower()
        light_status = str(result.get("light_status", "") or "").strip().lower()
        rule_hints = str(result.get("rule_hints", "") or "").strip()

        reference_cases_count = 0
        rules_context_count = 0
        method_cards_count = 0
        for step in _extract_trace_steps(result):
            reference_cases_count = max(
                reference_cases_count,
                _safe_int(step.get("reference_cases_count", 0)),
            )
            rules_context_count = max(
                rules_context_count,
                _safe_int(step.get("rules_context_count", 0)),
            )
            method_cards_count = max(
                method_cards_count,
                _safe_int(step.get("method_cards_count", 0)),
            )

        layer_flags = {
            "ExperienceDB": {
                "hit": match_source.startswith("experience") or reference_cases_count > 0,
                "direct": match_source.startswith("experience"),
            },
            "RuleKnowledge": {
                "hit": match_source.startswith("rule") or rules_context_count > 0 or bool(rule_hints),
                "direct": match_source.startswith("rule"),
            },
            "MethodCards": {
                "hit": method_cards_count > 0,
                "direct": False,
            },
        }

        for layer, flags in layer_flags.items():
            if not flags["hit"]:
                continue
            item = summary[layer]
            item["hit_count"] += 1
            if flags["direct"]:
                item["direct_count"] += 1
            else:
                item["assist_count"] += 1
            if confidence >= 90:
                item["high_conf_count"] += 1
            if light_status == "green":
                item["green_count"] += 1
            if review_risk in {"", "low"}:
                item["low_risk_count"] += 1
            if layer == "RuleKnowledge" and rule_hints:
                item["hint_count"] += 1

    return summary


def _extract_knowledge_hit_details(results: list[dict]) -> list[dict]:
    details: list[dict] = []
    for idx, result in enumerate(results or []):
        if not isinstance(result, dict):
            continue

        match_source = str(result.get("match_source", "") or "").strip().lower()
        confidence = _safe_int(result.get("confidence", 0))
        review_risk = str(result.get("review_risk", "") or "").strip().lower()
        light_status = str(result.get("light_status", "") or "").strip().lower()
        rule_hints = str(result.get("rule_hints", "") or "").strip()

        reference_cases_count = 0
        rules_context_count = 0
        method_cards_count = 0
        for step in _extract_trace_steps(result):
            reference_cases_count = max(
                reference_cases_count,
                _safe_int(step.get("reference_cases_count", 0)),
            )
            rules_context_count = max(
                rules_context_count,
                _safe_int(step.get("rules_context_count", 0)),
            )
            method_cards_count = max(
                method_cards_count,
                _safe_int(step.get("method_cards_count", 0)),
            )

        layer_flags = (
            ("ExperienceDB", match_source.startswith("experience") or reference_cases_count > 0, match_source.startswith("experience")),
            ("RuleKnowledge", match_source.startswith("rule") or rules_context_count > 0 or bool(rule_hints), match_source.startswith("rule")),
            ("MethodCards", method_cards_count > 0, False),
        )
        for layer, hit, direct in layer_flags:
            if not hit:
                continue
            object_refs: list[str] = []
            for step in _extract_trace_steps(result):
                if layer == "ExperienceDB":
                    stage = str(step.get("stage", "") or "")
                    if stage in {"experience_exact", "experience_similar"} and step.get("record_id") not in (None, ""):
                        object_refs.append(f"experience:{step.get('record_id')}")
                    for ref_id in step.get("reference_case_ids", []) or []:
                        ref_text = str(ref_id or "").strip()
                        if ref_text:
                            object_refs.append(f"experience:{ref_text}")
                elif layer == "RuleKnowledge":
                    for ref_id in step.get("rule_context_ids", []) or []:
                        ref_text = str(ref_id or "").strip()
                        if ref_text:
                            object_refs.append(f"rule:{ref_text}")
                elif layer == "MethodCards":
                    for ref_id in step.get("method_card_ids", []) or []:
                        ref_text = str(ref_id or "").strip()
                        if ref_text:
                            object_refs.append(f"method_card:{ref_text}")

            unique_refs: list[str] = []
            for ref in object_refs:
                if ref and ref not in unique_refs:
                    unique_refs.append(ref)
            if not unique_refs:
                unique_refs = [""]

            for object_ref in unique_refs:
                details.append({
                    "result_index": idx,
                    "layer": layer,
                    "object_ref": object_ref,
                    "hit_type": "direct" if direct else "assist",
                    "match_source": match_source,
                    "confidence": confidence,
                    "review_risk": review_risk,
                    "light_status": light_status,
                })
    return details


class AccuracyTracker:
    """准确率追踪器：记录运行指标、查看历史趋势。"""

    def record_run(self, stats: dict, input_file: str = "",
                   mode: str = "", province: str = ""):
        """
        记录一次运行的统计指标。

        参数:
            stats: _build_run_stats() 返回的统计字典
            input_file: 输入文件路径
            mode: 匹配模式
            province: 省份
        """
        now = time.localtime()
        run_date = time.strftime("%Y-%m-%d", now)
        run_time = time.strftime("%H:%M:%S", now)
        file_name = Path(input_file).name if input_file else ""

        conn = None
        try:
            conn = _get_conn()
            conn.execute("""
                INSERT INTO run_history
                (run_date, run_time, input_file, mode, province,
                 total, matched, high_conf, mid_conf, low_conf,
                 exp_hits, review_rejected, elapsed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_date, run_time, file_name, mode, province,
                stats.get("total", 0),
                stats.get("matched", 0),
                stats.get("high_conf", 0),
                stats.get("mid_conf", 0),
                stats.get("low_conf", 0),
                stats.get("exp_hits", 0),
                stats.get("review_rejected", 0),
                stats.get("elapsed", 0),
                time.time(),
            ))
            conn.commit()
            logger.debug(f"运行记录已保存: {run_date} {run_time}")
        except Exception as e:
            logger.warning(f"保存运行记录失败（不影响主流程）: {e}")
        finally:
            if conn is not None:
                conn.close()

    def record_review(self, input_file: str = "", province: str = "",
                      total: int = 0, auto_corrections: int = 0,
                      manual_items: int = 0, measure_items: int = 0,
                      correct_count: int = 0):
        """
        记录一次Jarvis审核的统计指标。

        参数:
            input_file: 输入文件路径
            province: 省份
            total: 总匹配条数
            auto_corrections: 自动纠正条数
            manual_items: 需人工处理条数
            measure_items: 措施项条数
            correct_count: 审核通过（无错）条数
        """
        now = time.localtime()
        run_date = time.strftime("%Y-%m-%d", now)
        run_time = time.strftime("%H:%M:%S", now)
        file_name = Path(input_file).name if input_file else ""

        conn = None
        try:
            conn = _get_conn()
            conn.execute("""
                INSERT INTO review_history
                (run_date, run_time, input_file, province,
                 total, auto_corrections, manual_items,
                 measure_items, correct_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_date, run_time, file_name, province,
                total, auto_corrections, manual_items,
                measure_items, correct_count, time.time(),
            ))
            conn.commit()
            logger.debug(f"审核记录已保存: {run_date} {run_time}")
        except Exception as e:
            logger.warning(f"保存审核记录失败（不影响主流程）: {e}")
        finally:
            if conn is not None:
                conn.close()

    def record_knowledge_hits(self, results: list[dict], *,
                              input_file: str = "", mode: str = "",
                              province: str = "", task_id: str = ""):
        """Record per-run knowledge-layer hit metrics derived from real match results."""
        now = time.localtime()
        run_date = time.strftime("%Y-%m-%d", now)
        run_time = time.strftime("%H:%M:%S", now)
        file_name = Path(input_file).name if input_file else ""
        total_results = len(results or [])
        layer_summary = _summarize_knowledge_hits(results or [])
        detail_rows = _extract_knowledge_hit_details(results or [])

        conn = None
        try:
            conn = _get_conn()
            rows = []
            created_at = time.time()
            for layer in _KNOWLEDGE_LAYERS:
                metrics = layer_summary.get(layer, {})
                rows.append((
                    run_date,
                    run_time,
                    file_name,
                    mode,
                    province,
                    str(task_id or ""),
                    layer,
                    total_results,
                    int(metrics.get("hit_count", 0)),
                    int(metrics.get("direct_count", 0)),
                    int(metrics.get("assist_count", 0)),
                    int(metrics.get("high_conf_count", 0)),
                    int(metrics.get("low_risk_count", 0)),
                    int(metrics.get("green_count", 0)),
                    int(metrics.get("hint_count", 0)),
                    created_at,
                ))
            conn.executemany("""
                INSERT INTO knowledge_hit_history
                (run_date, run_time, input_file, mode, province, task_id, layer,
                 total_results, hit_count, direct_count, assist_count,
                 high_conf_count, low_risk_count, green_count, hint_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            if detail_rows:
                conn.executemany("""
                    INSERT INTO knowledge_hit_result_history
                    (run_date, run_time, input_file, mode, province, task_id,
                     result_index, layer, object_ref, hit_type, match_source, confidence,
                     review_risk, light_status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    (
                        run_date,
                        run_time,
                        file_name,
                        mode,
                        province,
                        str(task_id or ""),
                        int(item["result_index"]),
                        str(item["layer"]),
                        str(item.get("object_ref", "")),
                        str(item["hit_type"]),
                        str(item["match_source"]),
                        int(item["confidence"]),
                        str(item["review_risk"]),
                        str(item["light_status"]),
                        created_at,
                    )
                    for item in detail_rows
                ])
            conn.commit()
            logger.debug(f"知识命中指标已记录: {run_date} {run_time}")
        except Exception as e:
            logger.warning(f"保存知识命中指标失败（不影响主流程）: {e}")
        finally:
            if conn is not None:
                conn.close()

    def get_recent_knowledge_hit_details(self, days: int = 7) -> list[dict]:
        """Return recent result-level knowledge hit rows for review linkage."""
        days = max(1, int(days))
        cutoff = time.time() - (days * 86400)
        conn = None
        try:
            conn = _get_conn()
            rows = conn.execute("""
                SELECT
                    task_id,
                    result_index,
                    layer,
                    object_ref,
                    hit_type,
                    match_source,
                    confidence,
                    review_risk,
                    light_status,
                    run_date,
                    created_at
                FROM knowledge_hit_result_history
                WHERE created_at >= ?
                  AND TRIM(COALESCE(task_id, '')) != ''
                ORDER BY created_at DESC, id DESC
            """, (cutoff,)).fetchall()
            return [
                {
                    "task_id": str(row[0] or ""),
                    "result_index": int(row[1] or 0),
                    "layer": str(row[2] or ""),
                    "object_ref": str(row[3] or ""),
                    "hit_type": str(row[4] or ""),
                    "match_source": str(row[5] or ""),
                    "confidence": int(row[6] or 0),
                    "review_risk": str(row[7] or ""),
                    "light_status": str(row[8] or ""),
                    "run_date": str(row[9] or ""),
                    "created_at": float(row[10] or 0),
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning(f"读取结果级知识命中明细失败: {e}")
            return []
        finally:
            if conn is not None:
                conn.close()

    def get_knowledge_hit_report(self, days: int = 7) -> dict:
        """Return aggregated knowledge-layer hit and benefit metrics."""
        days = max(1, int(days))
        cutoff = time.time() - (days * 86400)
        conn = None
        try:
            conn = _get_conn()
            summary_row = conn.execute("""
                SELECT
                    COUNT(DISTINCT run_date || ' ' || run_time || '|' || COALESCE(task_id, '') || '|' || COALESCE(input_file, '')) AS tracked_runs,
                    COALESCE(SUM(CASE WHEN layer = 'ExperienceDB' THEN total_results ELSE 0 END), 0) AS tracked_results,
                    COUNT(DISTINCT CASE
                        WHEN created_at >= ? THEN run_date || ' ' || run_time || '|' || COALESCE(task_id, '') || '|' || COALESCE(input_file, '')
                    END) AS last_7d_runs,
                    COALESCE(SUM(CASE
                        WHEN layer = 'ExperienceDB' AND created_at >= ? THEN total_results ELSE 0
                    END), 0) AS last_7d_results,
                    COALESCE(SUM(CASE WHEN created_at >= ? THEN hit_count ELSE 0 END), 0) AS last_7d_hits,
                    COALESCE(SUM(CASE WHEN created_at >= ? THEN direct_count ELSE 0 END), 0) AS last_7d_direct
                FROM knowledge_hit_history
            """, (cutoff, cutoff, cutoff, cutoff)).fetchone()

            layer_rows = conn.execute("""
                SELECT
                    layer,
                    COUNT(*) AS run_count,
                    COALESCE(MAX(total_results), 0) AS max_total_results,
                    COALESCE(SUM(total_results), 0) AS total_results,
                    COALESCE(SUM(hit_count), 0) AS hit_count,
                    COALESCE(SUM(direct_count), 0) AS direct_count,
                    COALESCE(SUM(assist_count), 0) AS assist_count,
                    COALESCE(SUM(high_conf_count), 0) AS high_conf_count,
                    COALESCE(SUM(low_risk_count), 0) AS low_risk_count,
                    COALESCE(SUM(green_count), 0) AS green_count,
                    COALESCE(SUM(hint_count), 0) AS hint_count
                FROM knowledge_hit_history
                GROUP BY layer
                ORDER BY layer ASC
            """).fetchall()

            recent_rows = conn.execute("""
                SELECT
                    run_date,
                    COALESCE(SUM(CASE WHEN layer = 'ExperienceDB' THEN total_results ELSE 0 END), 0) AS total_results,
                    COALESCE(SUM(CASE WHEN layer = 'ExperienceDB' THEN 1 ELSE 0 END), 0) AS run_rows,
                    COALESCE(SUM(CASE WHEN layer = 'ExperienceDB' THEN hit_count ELSE 0 END), 0) AS experience_hits,
                    COALESCE(SUM(CASE WHEN layer = 'ExperienceDB' THEN direct_count ELSE 0 END), 0) AS experience_direct,
                    COALESCE(SUM(CASE WHEN layer = 'RuleKnowledge' THEN hit_count ELSE 0 END), 0) AS rule_hits,
                    COALESCE(SUM(CASE WHEN layer = 'RuleKnowledge' THEN direct_count ELSE 0 END), 0) AS rule_direct,
                    COALESCE(SUM(CASE WHEN layer = 'MethodCards' THEN hit_count ELSE 0 END), 0) AS method_hits,
                    COALESCE(SUM(CASE WHEN layer = 'MethodCards' THEN assist_count ELSE 0 END), 0) AS method_assist
                FROM knowledge_hit_history
                WHERE created_at >= ?
                GROUP BY run_date
                ORDER BY run_date ASC
            """, (cutoff,)).fetchall()
        except Exception as e:
            logger.warning(f"读取知识命中指标失败: {e}")
            return {
                "summary": {
                    "tracked_runs": 0,
                    "tracked_results": 0,
                    "last_7d_runs": 0,
                    "last_7d_results": 0,
                    "last_7d_hits": 0,
                    "last_7d_direct": 0,
                },
                "layer_metrics": [],
                "recent_activity": [],
            }
        finally:
            if conn is not None:
                conn.close()

        layer_map = {
            row[0]: {
                "layer": row[0],
                "run_count": int(row[1] or 0),
                "total_results": int(row[3] or 0),
                "hit_count": int(row[4] or 0),
                "direct_count": int(row[5] or 0),
                "assist_count": int(row[6] or 0),
                "high_conf_count": int(row[7] or 0),
                "low_risk_count": int(row[8] or 0),
                "green_count": int(row[9] or 0),
                "hint_count": int(row[10] or 0),
            }
            for row in layer_rows
        }

        layer_metrics = []
        for layer in _KNOWLEDGE_LAYERS:
            item = layer_map.get(layer, {
                "layer": layer,
                "run_count": 0,
                "total_results": 0,
                "hit_count": 0,
                "direct_count": 0,
                "assist_count": 0,
                "high_conf_count": 0,
                "low_risk_count": 0,
                "green_count": 0,
                "hint_count": 0,
            })
            hit_count = int(item["hit_count"])
            total_results = int(item["total_results"])
            item["hit_rate"] = round((hit_count / total_results) * 100, 1) if total_results else 0.0
            item["direct_rate"] = round((int(item["direct_count"]) / hit_count) * 100, 1) if hit_count else 0.0
            item["high_conf_rate"] = round((int(item["high_conf_count"]) / hit_count) * 100, 1) if hit_count else 0.0
            item["low_risk_rate"] = round((int(item["low_risk_count"]) / hit_count) * 100, 1) if hit_count else 0.0
            layer_metrics.append(item)

        recent_activity = [
            {
                "date": str(row[0]),
                "total_results": int(row[1] or 0),
                "runs": int(row[2] or 0),
                "experience_hits": int(row[3] or 0),
                "experience_direct": int(row[4] or 0),
                "rule_hits": int(row[5] or 0),
                "rule_direct": int(row[6] or 0),
                "method_hits": int(row[7] or 0),
                "method_assist": int(row[8] or 0),
            }
            for row in recent_rows
        ]

        return {
            "summary": {
                "tracked_runs": int(summary_row[0] or 0) if summary_row else 0,
                "tracked_results": int(summary_row[1] or 0) if summary_row else 0,
                "last_7d_runs": int(summary_row[2] or 0) if summary_row else 0,
                "last_7d_results": int(summary_row[3] or 0) if summary_row else 0,
                "last_7d_hits": int(summary_row[4] or 0) if summary_row else 0,
                "last_7d_direct": int(summary_row[5] or 0) if summary_row else 0,
            },
            "layer_metrics": layer_metrics,
            "recent_activity": recent_activity,
        }

    def show_trend(self, last_n: int = 20):
        """
        显示最近 N 次运行的趋势。

        输出格式：每行一条运行记录，显示关键比率。
        """
        conn = None
        try:
            conn = _get_conn()
            rows = conn.execute("""
                SELECT run_date, run_time, input_file, mode,
                       total, matched, high_conf, mid_conf, low_conf,
                       exp_hits, review_rejected, elapsed
                FROM run_history
                ORDER BY id DESC
                LIMIT ?
            """, (last_n,)).fetchall()
        except Exception as e:
            print(f"读取运行记录失败: {e}")
            return
        finally:
            if conn is not None:
                conn.close()

        if not rows:
            print("暂无匹配运行记录。")
            # 仍然显示审核记录（可能先跑了审核）
            self._show_review_history(last_n)
            return

        # 按时间正序显示
        rows = list(reversed(rows))

        print("=" * 90)
        print("系统准确率趋势报告")
        print("=" * 90)
        print(f"{'日期':<12} {'文件':<20} {'模式':<7} "
              f"{'总数':>4} {'匹配率':>6} {'绿率':>5} {'经验命中':>8} "
              f"{'审核拦截':>8} {'耗时':>6}")
        print("-" * 90)

        for row in rows:
            (date, time_str, file_name, mode,
             total, matched, high, mid, low,
             exp_hits, review_rej, elapsed) = row

            total = total or 1  # 避免除零
            match_rate = f"{matched * 100 // total}%"
            green_rate = f"{high * 100 // total}%"
            exp_rate = f"{exp_hits}({exp_hits * 100 // total}%)"
            rej_count = f"{review_rej}" if review_rej else "-"
            elapsed_str = f"{elapsed:.0f}s"
            short_file = (file_name[:18] + "..") if len(file_name) > 20 else file_name

            print(f"{date:<12} {short_file:<20} {mode:<7} "
                  f"{total:>4} {match_rate:>6} {green_rate:>5} {exp_rate:>8} "
                  f"{rej_count:>8} {elapsed_str:>6}")

        print("=" * 90)

        # 趋势判断：比较最近5次和之前5次
        if len(rows) >= 10:
            recent = rows[-5:]
            earlier = rows[-10:-5]

            def avg_green_rate(records):
                totals = sum(r[4] or 1 for r in records)
                greens = sum(r[6] for r in records)
                return greens / max(totals, 1)

            def avg_exp_rate(records):
                totals = sum(r[4] or 1 for r in records)
                exps = sum(r[9] for r in records)
                return exps / max(totals, 1)

            recent_green = avg_green_rate(recent)
            earlier_green = avg_green_rate(earlier)
            recent_exp = avg_exp_rate(recent)
            earlier_exp = avg_exp_rate(earlier)

            print("\n趋势分析（最近5次 vs 之前5次）:")
            delta_green = recent_green - earlier_green
            delta_exp = recent_exp - earlier_exp
            arrow_green = "↑" if delta_green > 0.02 else ("↓" if delta_green < -0.02 else "→")
            arrow_exp = "↑" if delta_exp > 0.02 else ("↓" if delta_exp < -0.02 else "→")
            print(f"  高置信度率: {earlier_green:.0%} → {recent_green:.0%} {arrow_green}")
            print(f"  经验命中率: {earlier_exp:.0%} → {recent_exp:.0%} {arrow_exp}")

            if delta_green > 0.02 and delta_exp > 0.02:
                print("  结论: 系统在正向进化，经验积累有效")
            elif delta_exp > 0.05 and delta_green < -0.02:
                print("  警告: 经验命中率升高但准确率下降，可能有错误数据污染经验库！")
            elif delta_green < -0.05:
                print("  警告: 准确率明显下降，建议运行经验库体检")

        # 显示审核纠正记录
        self._show_review_history(last_n)

    def _show_review_history(self, last_n: int = 10):
        """显示最近的Jarvis审核纠正记录。"""
        conn = None
        try:
            conn = _get_conn()
            rows = conn.execute("""
                SELECT run_date, run_time, input_file, province,
                       total, auto_corrections, manual_items,
                       measure_items, correct_count
                FROM review_history
                ORDER BY id DESC
                LIMIT ?
            """, (last_n,)).fetchall()
        except Exception:
            return
        finally:
            if conn is not None:
                conn.close()

        if not rows:
            return

        rows = list(reversed(rows))

        print("\n" + "=" * 80)
        print("Jarvis审核纠正记录")
        print("=" * 80)
        print(f"{'日期':<12} {'文件':<20} "
              f"{'总数':>4} {'自动纠正':>8} {'需人工':>6} {'无错':>4} {'纠正率':>6}")
        print("-" * 80)

        for row in rows:
            (date, time_str, file_name, province,
             total, auto_corr, manual, measure, correct) = row

            total = total or 1
            corr_rate = f"{auto_corr * 100 // total}%"
            short_file = (file_name[:18] + "..") if len(file_name) > 20 else file_name

            print(f"{date:<12} {short_file:<20} "
                  f"{total:>4} {auto_corr:>8} {manual:>6} {correct:>4} {corr_rate:>6}")

        print("=" * 80)
        print("  纠正率越低越好（说明匹配越准确）")


# ============================================================
# 命令行入口（供 bat 脚本直接调用）
# ============================================================

if __name__ == "__main__":
    # 重新导入（sys.path 修改后才能找到 config）
    import config as _cfg
    _DB_PATH = _cfg.COMMON_DB_DIR / "run_history.db"
    tracker = AccuracyTracker()
    tracker.show_trend()
