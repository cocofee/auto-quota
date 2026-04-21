from src.unified_data_layer import UnifiedDataLayer


class _FakeExperienceDB:
    def __init__(self, records):
        self.records = records

    def search_experience(self, *args, **kwargs):
        return list(self.records)


class _FakeUniversalKB:
    def __init__(self, records):
        self.records = records

    def search_hints(self, *args, **kwargs):
        return list(self.records)


class _FakePriceDB:
    def __init__(self, result=None, error=None):
        self.result = result or {"items": [], "total": 0, "page": 1, "size": 20}
        self.error = error

    def search_composite_prices(self, **kwargs):
        if self.error:
            raise self.error
        return self.result


class _FakeQuotaDB:
    def __init__(self, rows):
        self.rows = rows

    def search_by_keywords(self, *args, **kwargs):
        return list(self.rows)


class _FakeVectorEngine:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error

    def search(self, *args, **kwargs):
        if self.error:
            raise self.error
        return list(self.rows)


def test_unified_data_layer_prefers_green_experience_case():
    layer = UnifiedDataLayer(
        experience_db=_FakeExperienceDB(
            [
                {
                    "id": 1,
                    "bill_text": "镀锌钢管安装 DN50",
                    "quota_ids": ["C10-1-1"],
                    "quota_names": ["管道安装 镀锌钢管 DN50"],
                    "source": "openclaw_approved",
                    "gate": "green",
                    "layer": "authority",
                    "confidence": 95,
                    "effective_confidence": 95,
                    "confirm_count": 3,
                    "total_score": 0.93,
                    "match_type": "exact",
                }
            ]
        ),
        universal_kb=_FakeUniversalKB(
            [
                {
                    "id": 11,
                    "bill_pattern": "镀锌钢管安装",
                    "quota_patterns": ["管道安装", "镀锌钢管"],
                    "associated_patterns": ["支架", "试压"],
                    "similarity": 0.92,
                    "confidence": 88,
                    "layer": "authority",
                }
            ]
        ),
        price_db=_FakePriceDB(
            {
                "items": [
                    {
                        "id": 21,
                        "boq_name_raw": "镀锌钢管安装 DN50",
                        "quota_name": "管道安装 镀锌钢管 DN50",
                        "unit": "m",
                        "composite_unit_price": 123.4,
                        "region": "北京",
                        "price_outlier": 0,
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 10,
            }
        ),
        quota_db=_FakeQuotaDB(
            [
                {
                    "quota_id": "C10-1-1",
                    "name": "管道安装 镀锌钢管 DN50",
                    "unit": "m",
                }
            ]
        ),
        vector_engine=_FakeVectorEngine(
            [
                {
                    "quota_id": "C10-1-1",
                    "name": "管道安装 镀锌钢管 DN50",
                    "unit": "m",
                    "vector_score": 0.87,
                }
            ]
        ),
    )

    result = layer.search("镀锌钢管安装 DN50", top_k=6)

    assert result["items"][0]["source"] == "experience"
    assert result["items"][0]["recommended"] is True
    assert result["items"][0]["content"] == "C10-1-1 管道安装 镀锌钢管 DN50"
    assert result["meta"]["failed_sources"] == []


def test_unified_data_layer_marks_failed_source_and_keeps_other_results():
    layer = UnifiedDataLayer(
        experience_db=_FakeExperienceDB([]),
        universal_kb=_FakeUniversalKB([]),
        price_db=_FakePriceDB(error=RuntimeError("price db unavailable")),
        quota_db=_FakeQuotaDB(
            [
                {
                    "quota_id": "A-1",
                    "name": "普通阀门安装",
                    "unit": "个",
                }
            ]
        ),
        vector_engine=_FakeVectorEngine([]),
    )

    result = layer.search("普通阀门安装", sources=["price", "quota"], top_k=5)

    assert result["meta"]["failed_sources"] == ["price"]
    assert result["grouped"]["price"] == []
    assert len(result["grouped"]["quota"]) == 1
    assert result["items"][0]["source"] == "quota"


def test_unified_data_layer_deduplicates_quota_keyword_and_vector_hits():
    layer = UnifiedDataLayer(
        experience_db=_FakeExperienceDB([]),
        universal_kb=_FakeUniversalKB([]),
        price_db=_FakePriceDB(),
        quota_db=_FakeQuotaDB(
            [
                {
                    "quota_id": "C1-1",
                    "name": "配管 SC20",
                    "unit": "m",
                }
            ]
        ),
        vector_engine=_FakeVectorEngine(
            [
                {
                    "quota_id": "C1-1",
                    "name": "配管 SC20",
                    "unit": "m",
                    "vector_score": 0.91,
                },
                {
                    "quota_id": "C1-2",
                    "name": "配管 SC25",
                    "unit": "m",
                    "vector_score": 0.72,
                },
            ]
        ),
    )

    result = layer.search("配管 SC20", sources=["quota"], top_k=5)
    quota_items = result["grouped"]["quota"]

    assert [item["id"] for item in quota_items] == ["C1-1", "C1-2"]
    assert quota_items[0]["match_channel"] == ["keyword", "vector"]
    assert quota_items[0]["score"] == 0.91


def test_unified_data_layer_does_not_auto_recommend_single_confirm_verified_experience():
    layer = UnifiedDataLayer(
        experience_db=_FakeExperienceDB(
            [
                {
                    "id": 1,
                    "bill_text": "PPR pipe install DN25",
                    "quota_ids": ["EXP-1"],
                    "quota_names": ["PPR pipe install DN25"],
                    "source": "user_confirmed",
                    "gate": "green",
                    "layer": "verified",
                    "confidence": 95,
                    "confirm_count": 1,
                    "effective_confidence": 68,
                    "total_score": 0.88,
                    "match_type": "similar",
                }
            ]
        ),
        universal_kb=_FakeUniversalKB(
            [
                {
                    "id": 11,
                    "bill_pattern": "PPR pipe install DN25",
                    "quota_patterns": ["PPR pipe install DN25"],
                    "associated_patterns": ["fittings"],
                    "similarity": 0.92,
                    "confidence": 90,
                    "layer": "authority",
                }
            ]
        ),
        price_db=_FakePriceDB(),
        quota_db=_FakeQuotaDB([]),
        vector_engine=_FakeVectorEngine([]),
    )

    result = layer.search("PPR pipe install DN25", sources=["experience", "universal_kb"], top_k=3)

    experience_item = result["grouped"]["experience"][0]

    assert "recommended" not in experience_item
    assert experience_item["effective_confidence"] == 68
    assert round(experience_item["merge_score"], 4) == 0.7888
    assert result["items"][0]["source"] == "universal_kb"


def test_unified_data_layer_auto_recommend_uses_weighted_top_experience():
    layer = UnifiedDataLayer(
        experience_db=_FakeExperienceDB(
            [
                {
                    "id": 1,
                    "bill_text": "PPR pipe install DN25",
                    "quota_ids": ["EXP-LOW"],
                    "quota_names": ["lower confidence hit"],
                    "source": "user_confirmed",
                    "gate": "green",
                    "layer": "verified",
                    "confidence": 68,
                    "confirm_count": 1,
                    "effective_confidence": 68,
                    "total_score": 0.90,
                    "match_type": "similar",
                },
                {
                    "id": 2,
                    "bill_text": "PPR pipe install DN25",
                    "quota_ids": ["EXP-HIGH"],
                    "quota_names": ["higher confidence hit"],
                    "source": "user_confirmed",
                    "gate": "green",
                    "layer": "verified",
                    "confidence": 95,
                    "confirm_count": 3,
                    "effective_confidence": 95,
                    "total_score": 0.88,
                    "match_type": "similar",
                },
            ]
        ),
        universal_kb=_FakeUniversalKB([]),
        price_db=_FakePriceDB(),
        quota_db=_FakeQuotaDB([]),
        vector_engine=_FakeVectorEngine([]),
    )

    result = layer.search("PPR pipe install DN25", sources=["experience"], strategy="auto", top_k=3)

    assert result["items"][0]["source"] == "experience"
    assert result["items"][0]["id"] == 2
    assert result["items"][0]["recommended"] is True
    assert result["items"][0]["content"] == "EXP-HIGH higher confidence hit"


def test_unified_data_layer_preserves_legacy_experience_confidence_without_confirm_count():
    layer = UnifiedDataLayer(
        experience_db=_FakeExperienceDB(
            [
                {
                    "id": 1,
                    "bill_text": "legacy authority hit",
                    "quota_ids": ["EXP-LEGACY"],
                    "quota_names": ["legacy authority quota"],
                    "source": "openclaw_approved",
                    "gate": "green",
                    "layer": "authority",
                    "confidence": 95,
                    "total_score": 0.90,
                    "match_type": "exact",
                    "updated_at": "1710000000",
                }
            ]
        ),
        universal_kb=_FakeUniversalKB(
            [
                {
                    "id": 11,
                    "bill_pattern": "legacy authority hit",
                    "quota_patterns": ["legacy authority quota"],
                    "associated_patterns": [],
                    "similarity": 0.92,
                    "confidence": 90,
                    "layer": "authority",
                }
            ]
        ),
        price_db=_FakePriceDB(),
        quota_db=_FakeQuotaDB([]),
        vector_engine=_FakeVectorEngine([]),
    )

    result = layer.search("legacy authority hit", sources=["experience", "universal_kb"], strategy="auto", top_k=3)

    experience_item = result["grouped"]["experience"][0]

    assert experience_item["effective_confidence"] == 95
    assert result["items"][0]["source"] == "experience"
    assert result["items"][0]["id"] == 1
    assert result["items"][0]["recommended"] is True
