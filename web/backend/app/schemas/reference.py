"""
Price reference schemas.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LayeredPriceSample(BaseModel):
    id: int
    raw_name: str = ""
    normalized_name: str = ""
    specialty: str = ""
    unit: str = ""
    brand: str = ""
    model: str = ""
    spec: str = ""
    price_type: str = ""
    price_value: float | None = None
    materials_signature: str = ""
    materials_signature_first: str = ""
    region: str = ""
    source_date: str = ""
    price_date_iso: str | None = None
    date_parse_failed: int = 0
    project_name: str = ""
    quota_code: str = ""
    quota_name: str = ""
    source_record_id: int | None = None
    price_outlier: bool = False
    outlier_method: str | None = None
    outlier_score: float | None = None
    outlier_reason: str | None = None
    remarks: str = ""


class PriceBucket(BaseModel):
    sample_count: int = 0
    median_price: float | None = None
    mean_price: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    p25_price: float | None = None
    p75_price: float | None = None
    latest_price: float | None = None
    latest_date: str | None = None
    price_type: str | None = None
    bucket_key: str | None = None
    samples: list[LayeredPriceSample] = Field(default_factory=list)


class LayeredPriceResult(BaseModel):
    exact_match: PriceBucket | None = None
    brand_match: PriceBucket | None = None
    category_match: PriceBucket | None = None
    recommended_price: float | None = None
    recommended_source: str | None = None
    total_sample_count: int = 0
    valid_sample_count: int = 0
    outlier_count: int = 0


class ItemPriceSample(BaseModel):
    id: int
    item_name_raw: str
    item_name_normalized: str = ""
    brand: str = ""
    model: str = ""
    spec: str = ""
    unit: str = ""
    unit_price: float | None = None
    install_price: float | None = None
    combined_unit_price: float | None = None
    specialty: str = ""
    system_name: str = ""
    region: str = ""
    source_date: str = ""
    price_date_iso: str | None = None
    price_type: str = ""
    price_value: float | None = None
    materials_signature: str = ""
    materials_signature_first: str = ""
    source_record_id: int | None = None
    price_outlier: bool = False
    outlier_method: str | None = None
    outlier_score: float | None = None
    outlier_reason: str | None = None
    project_name: str = ""
    remarks: str = ""


class ItemPriceSearchResponse(BaseModel):
    items: list[ItemPriceSample]
    total: int
    page: int
    size: int


class ItemPriceReferenceResponse(BaseModel):
    query: str
    reference_type: str = "item_price"
    summary: dict = Field(default_factory=dict)
    samples: list[ItemPriceSample] = Field(default_factory=list)
    layered_result: LayeredPriceResult | None = None


class CompositePriceSample(BaseModel):
    id: int
    boq_code: str = ""
    boq_name_raw: str
    boq_name_normalized: str = ""
    feature_text: str = ""
    unit: str = ""
    quantity: float | None = None
    composite_unit_price: float | None = None
    quota_code: str = ""
    quota_name: str = ""
    specialty: str = ""
    region: str = ""
    source_date: str = ""
    price_date_iso: str | None = None
    price_type: str = ""
    price_value: float | None = None
    materials_signature: str = ""
    materials_signature_first: str = ""
    source_record_id: int | None = None
    price_outlier: bool = False
    outlier_method: str | None = None
    outlier_score: float | None = None
    outlier_reason: str | None = None
    project_name: str = ""
    remarks: str = ""


class CompositePriceSearchResponse(BaseModel):
    items: list[CompositePriceSample]
    total: int
    page: int
    size: int


class CompositePriceReferenceResponse(BaseModel):
    query: str
    reference_type: str = "composite_price"
    summary: dict = Field(default_factory=dict)
    samples: list[CompositePriceSample] = Field(default_factory=list)
    layered_result: LayeredPriceResult | None = None
