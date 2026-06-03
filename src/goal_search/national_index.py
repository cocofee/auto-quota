from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import config
from db.sqlite import connect as _db_connect

try:
    import jieba
except Exception:  # pragma: no cover
    jieba = None


DEFAULT_INDEX_PATH = config.DATA_DIR / "goal_search" / "national_index.sqlite"

PUNCT_RE = re.compile(r"[\s,，。；;:：、/\\|()\[\]{}（）【】《》<>+_=`~!！?？\"'“”‘’]+")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
DN_RE = re.compile(r"(?i)(?<![A-Z0-9])(?:D(?:N|E)?|SC)\s*(\d+)(?!\d)|[Φφ]\s*(\d+)(?!\d)")
CONCRETE_RE = re.compile(r"(?i)(?:^|[^A-Z])C\s*(\d{2})(?:[^0-9]|$)")
CABLE_SECTION_RE = re.compile(r"(?i)(?:\d+\s*[x*×]\s*)?(\d+(?:\.\d+)?)\s*(?:mm2|mm²|平方)")
CABLE_X_SECTION_RE = re.compile(r"(?i)\b\d+\s*[x*×]\s*(\d+(?:\.\d+)?)\b")
WIRE_MODEL_SECTION_RE = re.compile(r"(?i)(?:BYJ|BVR|BV)\s*-?\s*(\d+(?:\.\d+)?)\b")
CABLE_CORES_RE = re.compile(r"(?i)\b(\d+)\s*[x*×]\s*\d+(?:\.\d+)?(?:\s*(?:mm2|mm²|平方))?\b")
CABLE_COMPOUND_RE = re.compile(r"(?i)(\d+)\s*[x*×]\s*\d+(?:\.\d+)?")
CABLE_COMPOUND_EXPR_RE = re.compile(r"(?i)\d+\s*[x*×]\s*\d+(?:\.\d+)?(?:\s*\+\s*\d+\s*[x*×]\s*\d+(?:\.\d+)?)+")
CABLE_CORE_WORD_RE = re.compile(r"(?:(\d+)|([一二三四五六七八九十两单]))\s*芯")
CIRCUITS_RE = re.compile(r"(\d+)\s*(?:回路|路)")
THICKNESS_RE = re.compile(
    r"(?:厚度|板材厚度|δ)\s*(?:=|≤|<=)?\s*(\d+(?:\.\d+)?)|"
    r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*厚"
)
SPEC_RE = re.compile(
    r"(?i)(DN\s*\d+|De\s*\d+|D\s*\d+|Φ\s*\d+|φ\s*\d+|C\s*\d{2}|"
    r"HRB\s*\d+|HPB\s*\d+|YJV|WDZ|NH|ZR|BV|BVR|RVV|PVC|UPVC|PPR|PE|HDPE|"
    r"SC\s*\d+|JDG\s*\d+|KBG\s*\d+|\d+(?:\.\d+)?\s*"
    r"(?:mm|cm|m2|m3|kg|t|kw|kva|a|芯|孔|回路|厚|宽|高|以内))"
)

SYNONYMS = {
    "砼": "混凝土",
    "商品砼": "预拌混凝土",
    "商砼": "预拌混凝土",
    "钢砼": "钢筋混凝土",
    "φ": "Φ",
    "×": "x",
    "*": "x",
    "衬塑管": "衬塑钢管",
    "线槽": "桥架 线槽",
    "管内穿线": "配线 管内穿线",
    "穿管敷设": "管内穿线",
    "潜污泵": "排污泵",
    "暗敷": "暗配",
    "明敷": "明配",
    "胶粘": "粘接",
    "胶水粘接": "粘接",
    "u-pvc": "upvc",
    "pvc-u": "upvc",
    "拖把池": "拖布池",
    "小便斗": "小便器",
    "座便器": "坐便器",
}

ACTION_WORDS = (
    "安装",
    "敷设",
    "铺设",
    "浇筑",
    "砌筑",
    "制作",
    "拆除",
    "运输",
    "回填",
    "开挖",
    "喷刷",
    "抹灰",
    "找平",
    "防水",
    "保温",
    "调试",
)

MATERIAL_WORDS = (
    "混凝土",
    "砂浆",
    "钢筋",
    "模板",
    "砖",
    "砌块",
    "钢管",
    "镀锌钢管",
    "衬塑钢管",
    "塑料管",
    "复合管",
    "分歧管",
    "铜管",
    "桥架",
    "电缆",
    "电线",
    "阀门",
    "风管",
    "风机",
    "风扇",
    "灯",
    "灯具",
    "配电箱",
    "沥青",
    "水泥",
    "涂料",
    "不锈钢",
)

CONNECTION_WORDS = ("法兰", "螺纹", "焊接", "热熔", "电熔", "沟槽", "卡箍", "粘接", "承插")
INSTALL_METHOD_WORDS = ("明装", "暗装", "明配", "暗配", "落地", "悬挂", "嵌入", "埋地", "架空", "户内", "户外")

QUERY_FAMILY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "electrical_box",
        (
            "\u7535\u7bb1",
        ),
    ),
    (
        "weak_current_device",
        (
            "led\u5c4f",
            "\u663e\u793a\u5c4f",
            "\u89c6\u9891\u4f20\u8f93",
            "\u89c6\u9891\u63a7\u5236",
            "\u89c6\u9891\u7efc\u5408",
            "\u76d1\u63a7\u6444\u50cf",
            "\u6444\u50cf\u8bbe\u5907",
            "\u6444\u50cf\u673a",
            "\u6269\u58f0",
            "\u80cc\u666f\u97f3\u4e50",
            "\u76ee\u6807\u8bc6\u522b",
            "\u8bfb\u5361",
        ),
    ),
    (
        "lamp",
        (
            "\u5438\u9876led",
            "\u9632\u6c34\u9632\u5c18\u5438\u9876led",
        ),
    ),
)


@dataclass(slots=True)
class QuotaSignal:
    family: str = ""
    action: str = ""
    material: str = ""
    connection: str = ""
    install_method: str = ""
    dn: float | None = None
    cable_section: float | None = None
    cable_cores: int | None = None
    circuits: int | None = None
    concrete_grade: int | None = None
    thickness: float | None = None
    param_type: str = ""
    tokens: list[str] | None = None
    normalized_text: str = ""

    def cluster_key(self, unit: str = "") -> str:
        return cluster_key(
            family=self.family,
            action=self.action,
            material=self.material,
            connection=self.connection,
            unit=unit,
            param_type=self.param_type,
        )


def clean_text(value: object) -> str:
    return str(value or "").strip()


def normalize_text(value: object) -> str:
    text = clean_text(value).lower()
    for old, new in SYNONYMS.items():
        text = text.replace(old.lower(), new.lower())
    text = PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def key_text(value: object) -> str:
    return re.sub(r"\s+", "", normalize_text(value))


def is_pipe_device_false_trigger(value: object) -> bool:
    compact = key_text(value)
    return any(word in compact for word in ("风管式空调", "管道风机"))


def chinese_ngrams(text: str) -> list[str]:
    grams: list[str] = []
    compact = re.sub(r"\s+", "", text)
    for chunk in CHINESE_RE.findall(compact):
        for size in (2, 3):
            if len(chunk) >= size:
                grams.extend(chunk[i : i + size] for i in range(0, len(chunk) - size + 1))
    return grams


def tokenize(value: object) -> list[str]:
    text = normalize_text(value)
    tokens: list[str] = [m.group(0).replace(" ", "").lower() for m in SPEC_RE.finditer(text)]
    if jieba is not None:
        tokens.extend(w.strip().lower() for w in jieba.cut(text) if len(w.strip()) > 1)
    else:
        tokens.extend(part for part in text.split() if len(part) > 1)
    tokens.extend(chinese_ngrams(text))
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        token = token.strip()
        if len(token) <= 1 or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def first_match(text: str, words: Iterable[str]) -> str:
    compact = text.replace(" ", "")
    for word in words:
        if word and word.lower() in compact:
            return word
    return ""


def _family_hint(compact: str) -> str:
    if "\u5f31\u7535\u7bb1" in compact:
        return "weak_current_device"
    for family, hints in QUERY_FAMILY_HINTS:
        if any(hint in compact for hint in hints):
            return family
    return ""


def _chinese_core_count(value: str) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return int(value)
    digits = {
        "单": 1,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    return digits.get(value)


def _compound_cable_cores(text: str) -> int | None:
    compound = CABLE_COMPOUND_EXPR_RE.search(text)
    if compound:
        total = sum(int(match.group(1)) for match in CABLE_COMPOUND_RE.finditer(compound.group(0)))
        return total if total > 0 else None
    match = CABLE_COMPOUND_RE.search(text)
    return int(match.group(1)) if match else None


def infer_family(value: object) -> str:
    text = normalize_text(value)
    compact = text.replace(" ", "")
    hinted_family = _family_hint(compact)
    if hinted_family:
        return hinted_family
    if "电缆头" in compact or "终端头" in compact or "中间头" in compact:
        return "cable_head"
    if any(word in compact for word in ("支吊架", "支/吊架", "支架", "吊架", "支撑架", "基础型钢", "管架")):
        return "support"
    if "电缆" in compact:
        return "cable"
    if any(word in compact for word in ("配电箱", "控制箱", "照明箱", "动力箱", "端子箱")):
        return "electrical_box"
    if any(word in compact for word in ("桥架", "线槽")):
        return "bridge"
    if is_pipe_device_false_trigger(text):
        return "fan"
    if any(word in compact for word in ("分歧器", "分歧管")):
        return "pipe"
    if "焊接钢管" in compact and not any(word in compact for word in ("配管", "电线管", "导管")):
        return "pipe"
    if any(word in compact for word in ("电线管", "配管", "导管", "jdg", "kbg", "sc")):
        return "conduit"
    if any(word in compact for word in ("配线", "导线", "电线", "铜芯线", "照明线路", "动力线", "穿线", "byj", "bv", "bvr")):
        return "wire"
    if any(word in compact for word in ("风管", "通风管", "柔性软风管", "柔性接口", "伸缩节", "防火阀", "排烟阀", "风阀", "风口", "散流器", "百叶")):
        return "duct"
    if any(word in compact for word in ("风机", "通风机", "排风机", "换气扇", "排气扇", "风扇")):
        return "fan"
    if any(word in compact for word in ("灯具", "灯管", "灯带", "筒灯", "射灯", "平板灯", "装饰灯", "荧光灯", "吸顶灯", "led灯", "线形灯")):
        return "lamp"
    industrial_tank = "水槽" in compact and any(word in compact for word in ("气柜", "壁板", "底板", "刷油", "除锈"))
    if not industrial_tank and any(word in compact for word in ("水槽", "洗涤盆", "洗脸盆", "洗手盆", "坐便器", "蹲便器", "蹲式大便器", "大便器", "小便器", "小便斗", "地漏", "水龙头", "拖布池", "卫生器具")):
        return "sanitary"
    if any(word in compact for word in ("插座", "五孔", "三孔", "二三极", "二、三极")):
        return "socket"
    if any(word in compact for word in ("开关", "单联单控", "双联单控", "三联单控", "单控", "双控")):
        return "switch"
    if "套管" in compact and "钻孔" not in compact:
        return "sleeve"
    if "空气过滤器" in compact or "过滤器框架" in compact or "高效过滤器" in compact:
        return "air_filter"
    if "过滤器" in compact and any(word in compact for word in ("水", "法兰", "螺纹", "阀门", "dn")):
        return "valve"
    if any(word in compact for word in ("阀", "止回", "闸阀", "蝶阀", "截止阀", "球阀")):
        return "valve"
    if any(word in compact for word in ("水泵", "排污泵", "潜污泵", "泵")):
        return "pump"
    if any(word in compact for word in ("压力表", "温度计", "流量计", "仪表")):
        return "instrument"
    if "混凝土" in compact:
        return "concrete"
    if "钢筋" in compact:
        return "rebar"
    if "模板" in compact:
        return "formwork"
    if "管" in compact:
        return "pipe"
    return ""


def extract_signal(value: object) -> QuotaSignal:
    raw_text = clean_text(value).lower().replace("×", "x").replace("*", "x")
    text = normalize_text(value)
    family = infer_family(text)
    dns: list[int] = []
    for match in DN_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        if raw:
            dns.append(int(raw))

    section = None
    section_match = CABLE_SECTION_RE.search(text)
    if not section_match and family in {"cable", "cable_head", "wire"}:
        section_match = CABLE_X_SECTION_RE.search(text)
    if not section_match and family == "wire":
        section_match = WIRE_MODEL_SECTION_RE.search(raw_text) or WIRE_MODEL_SECTION_RE.search(text)
    if section_match:
        section = float(section_match.group(1))

    cable_cores = None
    if family in {"cable", "cable_head", "wire"}:
        cable_cores = _compound_cable_cores(raw_text) or _compound_cable_cores(text)
        if cable_cores is None:
            cable_cores_match = CABLE_CORES_RE.search(text)
            if cable_cores_match:
                cable_cores = int(cable_cores_match.group(1))
        if cable_cores is None:
            cable_core_word_match = CABLE_CORE_WORD_RE.search(text)
            if cable_core_word_match:
                cable_cores = _chinese_core_count(cable_core_word_match.group(1) or cable_core_word_match.group(2))

    circuits = None
    circuits_match = CIRCUITS_RE.search(text)
    if circuits_match:
        circuits = int(circuits_match.group(1))

    concrete_grade = None
    concrete_match = CONCRETE_RE.search(text) if family == "concrete" else None
    if concrete_match:
        concrete_grade = int(concrete_match.group(1))

    thickness = None
    thickness_match = THICKNESS_RE.search(text)
    if thickness_match:
        thickness = float(thickness_match.group(1) or thickness_match.group(2))

    param_type = ""
    for key, value_present in (
        ("dn", bool(dns)),
        ("cable_section", section is not None),
        ("cable_cores", cable_cores is not None),
        ("circuits", circuits is not None),
        ("concrete_grade", concrete_grade is not None),
        ("thickness", thickness is not None),
    ):
        if value_present:
            param_type = key
            break

    action = first_match(text, ACTION_WORDS)
    material = first_match(text, MATERIAL_WORDS)
    connection = first_match(text, CONNECTION_WORDS)
    install_method = first_match(text, INSTALL_METHOD_WORDS)

    return QuotaSignal(
        family=family,
        action=action,
        material=material,
        connection=connection,
        install_method=install_method,
        dn=float(dns[0]) if dns else None,
        cable_section=section,
        cable_cores=cable_cores,
        circuits=circuits,
        concrete_grade=concrete_grade,
        thickness=thickness,
        param_type=param_type,
        tokens=tokenize(text),
        normalized_text=text,
    )


def cluster_key(*, family: str, action: str, material: str, connection: str, unit: str, param_type: str) -> str:
    return "|".join(
        [
            clean_text(family),
            clean_text(action),
            clean_text(material),
            clean_text(connection),
            clean_text(unit),
            clean_text(param_type),
        ]
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"pragma table_info({table})").fetchall()}


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists national_quotas (
            province text not null,
            quota_id text not null,
            name text not null,
            unit text not null default '',
            chapter text not null default '',
            specialty text not null default '',
            family text not null default '',
            action text not null default '',
            material text not null default '',
            connection text not null default '',
            install_method text not null default '',
            dn real,
            cable_section real,
            cable_cores integer,
            circuits integer,
            concrete_grade integer,
            thickness real,
            param_type text not null default '',
            cluster_key text not null default '',
            tokens text not null default '[]',
            normalized_text text not null default '',
            primary key (province, quota_id)
        )
        """
    )
    conn.execute("create index if not exists idx_national_cluster on national_quotas(cluster_key, province)")
    conn.execute("create index if not exists idx_national_family on national_quotas(province, family)")
    conn.execute("create index if not exists idx_national_province on national_quotas(province)")
    conn.execute(
        """
        create table if not exists national_index_meta (
            key text primary key,
            value text not null
        )
        """
    )


def iter_quota_db_rows(province: str, db_path: Path) -> Iterable[dict[str, Any]]:
    conn = _db_connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        has_quotas = conn.execute(
            "select 1 from sqlite_master where type='table' and name='quotas'"
        ).fetchone()
        if not has_quotas:
            return
        cols = _table_columns(conn, "quotas")
        optional = [
            "work_type",
            "specialty",
            "chapter",
            "material",
            "connection",
            "dn",
            "cable_section",
            "cable_cores",
            "circuits",
            "search_text",
        ]
        select_cols = ["quota_id", "name", "unit"] + [col for col in optional if col in cols]
        for row in conn.execute(f"select {', '.join(select_cols)} from quotas"):
            data = dict(row)
            data["province"] = province
            yield data
    finally:
        conn.close()


def row_to_index_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    text = " ".join(
        clean_text(row.get(key))
        for key in ("quota_id", "name", "unit", "chapter", "specialty", "material", "connection", "search_text")
        if clean_text(row.get(key))
    )
    signal = extract_signal(text)
    _apply_structured_values(signal, row)
    unit = clean_text(row.get("unit"))
    return (
        clean_text(row.get("province")),
        clean_text(row.get("quota_id")),
        clean_text(row.get("name")),
        unit,
        clean_text(row.get("chapter")),
        clean_text(row.get("specialty")),
        signal.family,
        signal.action,
        signal.material,
        signal.connection,
        signal.install_method,
        signal.dn,
        signal.cable_section,
        signal.cable_cores,
        signal.circuits,
        signal.concrete_grade,
        signal.thickness,
        signal.param_type,
        signal.cluster_key(unit),
        json.dumps(signal.tokens or [], ensure_ascii=False),
        signal.normalized_text,
    )


def _to_float(value: object) -> float | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _apply_structured_values(signal: QuotaSignal, row: dict[str, Any]) -> None:
    dn = _to_float(row.get("dn"))
    cable_section = _to_float(row.get("cable_section"))
    cable_cores = _to_int(row.get("cable_cores"))
    circuits = _to_int(row.get("circuits"))
    if dn is not None:
        signal.dn = dn
    if cable_section is not None:
        signal.cable_section = cable_section
    if cable_cores is not None:
        signal.cable_cores = cable_cores
    if circuits is not None:
        signal.circuits = circuits
    for key, value_present in (
        ("dn", signal.dn is not None),
        ("cable_section", signal.cable_section is not None),
        ("cable_cores", signal.cable_cores is not None),
        ("circuits", signal.circuits is not None),
        ("concrete_grade", signal.concrete_grade is not None),
        ("thickness", signal.thickness is not None),
    ):
        if value_present:
            signal.param_type = key
            break


def build_national_index(
    *,
    output_path: Path = DEFAULT_INDEX_PATH,
    province_filter: set[str] | None = None,
    limit_provinces: int | None = None,
    limit_rows_per_province: int | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    out = sqlite3.connect(str(tmp_path))
    try:
        create_schema(out)
        province_dirs = sorted(path for path in config.PROVINCES_DB_DIR.iterdir() if (path / "quota.db").exists())
        if province_filter:
            province_dirs = [path for path in province_dirs if path.name in province_filter]
        if limit_provinces is not None:
            province_dirs = province_dirs[:limit_provinces]

        insert_sql = """
            insert or replace into national_quotas (
                province, quota_id, name, unit, chapter, specialty, family, action,
                material, connection, install_method, dn, cable_section, cable_cores, circuits,
                concrete_grade, thickness, param_type, cluster_key, tokens,
                normalized_text
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        total_rows = 0
        province_counts: dict[str, int] = {}
        for province_dir in province_dirs:
            count = 0
            batch: list[tuple[Any, ...]] = []
            for row in iter_quota_db_rows(province_dir.name, province_dir / "quota.db"):
                batch.append(row_to_index_tuple(row))
                count += 1
                total_rows += 1
                if len(batch) >= 1000:
                    out.executemany(insert_sql, batch)
                    batch.clear()
                if limit_rows_per_province is not None and count >= limit_rows_per_province:
                    break
            if batch:
                out.executemany(insert_sql, batch)
            province_counts[province_dir.name] = count
            out.commit()

        meta = {
            "schema_version": "goal_national_index.v2",
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "province_count": len(province_counts),
            "row_count": total_rows,
            "elapsed_sec": round(time.perf_counter() - started, 3),
        }
        out.executemany(
            "insert or replace into national_index_meta(key, value) values (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in meta.items()],
        )
        out.commit()
    finally:
        out.close()

    if output_path.exists():
        output_path.unlink()
    tmp_path.replace(output_path)
    return {
        "output_path": str(output_path),
        "province_count": len(province_counts),
        "row_count": total_rows,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "province_counts": province_counts,
    }


def query_same_cluster(
    *,
    province: str,
    signal: QuotaSignal,
    unit: str = "",
    index_path: Path = DEFAULT_INDEX_PATH,
    limit: int = 32,
) -> list[dict[str, Any]]:
    if not index_path.exists() or not signal.family:
        return []
    conn = sqlite3.connect(str(index_path))
    conn.row_factory = sqlite3.Row
    try:
        key = signal.cluster_key(unit)
        results: list[dict[str, Any]] = []
        seen_quota_ids: set[str] = set()

        def add_rows(rows: Iterable[sqlite3.Row], *, match: str, bonus: float) -> None:
            for row in rows:
                data = dict(row)
                quota_id = clean_text(data.get("quota_id"))
                if not quota_id or quota_id in seen_quota_ids:
                    continue
                data["national_match"] = match
                data["national_bonus"] = bonus
                seen_quota_ids.add(quota_id)
                results.append(data)

        exact_rows = conn.execute(
            """
            select quota_id, name, unit, family, action, material, connection,
                   install_method, param_type, cluster_key
            from national_quotas
            where province = ? and cluster_key = ?
            limit ?
            """,
            (province, key, limit),
        ).fetchall()
        add_rows(exact_rows, match="national_local_exact_cluster", bonus=0.18)

        evidence = conn.execute(
            """
            select count(*) as row_count, count(distinct province) as province_count
            from national_quotas
            where cluster_key = ?
            """,
            (key,),
        ).fetchone()
        cross_province_cluster = bool(evidence and int(evidence["province_count"] or 0) > 1)

        if len(results) < limit and (cross_province_cluster or not results):
            # Build ORDER BY dynamically: only include fields that have non-empty
            # signal values (avoid penalizing correct candidates when signal extraction
            # is incomplete, e.g. bill text missing action words)
            order_clauses: list[str] = []
            order_params: list = []
            # Skip unit in ORDER BY: bill unit (e.g. "m2") often differs in format
            # from quota unit (e.g. "10m2") even for the same concept
            for field, sig_value in (
                ("action", signal.action),
                ("material", signal.material),
                ("connection", signal.connection),
                ("param_type", signal.param_type),
            ):
                if sig_value:
                    order_clauses.append(f"case when {field} = ? then 0 else 1 end")
                    order_params.append(sig_value)
            order_sql = ", ".join(order_clauses) if order_clauses else "1"
            family_rows = conn.execute(
                f"""
                select quota_id, name, unit, family, action, material, connection,
                       install_method, param_type, cluster_key
                from national_quotas
                where province = ? and family = ?
                order by {order_sql}
                limit ?
                """,
                [province, signal.family] + order_params + [max(0, limit - len(results))],
            ).fetchall()
            add_rows(
                family_rows,
                match="national_cross_cluster_family" if cross_province_cluster else "national_local_family",
                bonus=0.08 if cross_province_cluster else 0.04,
            )
        return results
    finally:
        conn.close()
