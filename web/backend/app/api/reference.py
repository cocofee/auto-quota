"""
Unified price reference API.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.auth.deps import get_current_user
from app.models.user import User
from app.schemas.reference import (
    CompositePriceReferenceResponse,
    CompositePriceSearchResponse,
    ItemPriceReferenceResponse,
    ItemPriceSearchResponse,
)
from src.price_reference_db import PriceReferenceDB

router = APIRouter()


def _db() -> PriceReferenceDB:
    return PriceReferenceDB()


def _search_item_prices(
    *,
    q: str,
    specialty: str = "",
    brand: str = "",
    model: str = "",
    region: str = "",
    page: int = 1,
    size: int = 20,
) -> ItemPriceSearchResponse:
    payload = _db().search_item_prices(
        query=q,
        specialty=specialty,
        brand=brand,
        model=model,
        region=region,
        page=page,
        size=size,
    )
    return ItemPriceSearchResponse(**payload)


def _get_item_price_reference(
    *,
    q: str,
    specialty: str = "",
    brand: str = "",
    model: str = "",
    region: str = "",
    top_k: int = 20,
) -> ItemPriceReferenceResponse:
    payload = _db().get_item_price_reference(
        query=q,
        specialty=specialty,
        brand=brand,
        model=model,
        region=region,
        top_k=top_k,
    )
    return ItemPriceReferenceResponse(**payload)


def _search_composite_prices(
    *,
    q: str,
    specialty: str = "",
    quota_code: str = "",
    region: str = "",
    page: int = 1,
    size: int = 20,
) -> CompositePriceSearchResponse:
    payload = _db().search_composite_prices(
        query=q,
        specialty=specialty,
        quota_code=quota_code,
        region=region,
        page=page,
        size=size,
    )
    return CompositePriceSearchResponse(**payload)


def _get_composite_price_reference(
    *,
    q: str,
    specialty: str = "",
    quota_code: str = "",
    region: str = "",
    top_k: int = 20,
) -> CompositePriceReferenceResponse:
    payload = _db().get_composite_price_reference(
        query=q,
        specialty=specialty,
        quota_code=quota_code,
        region=region,
        top_k=top_k,
    )
    return CompositePriceReferenceResponse(**payload)


@router.get("/item-price/search", response_model=ItemPriceSearchResponse)
async def search_item_prices(
    q: str = Query(default="", description="设备/材料查询词"),
    specialty: str = Query(default="", description="专业"),
    brand: str = Query(default="", description="品牌"),
    model: str = Query(default="", description="型号"),
    region: str = Query(default="", description="地区"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    _ = user
    return _search_item_prices(
        q=q,
        specialty=specialty,
        brand=brand,
        model=model,
        region=region,
        page=page,
        size=size,
    )


@router.get("/item-price", response_model=ItemPriceReferenceResponse)
async def get_item_price_reference(
    q: str = Query(description="设备/材料查询词"),
    specialty: str = Query(default="", description="专业"),
    brand: str = Query(default="", description="品牌"),
    model: str = Query(default="", description="型号"),
    region: str = Query(default="", description="地区"),
    top_k: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    _ = user
    return _get_item_price_reference(
        q=q,
        specialty=specialty,
        brand=brand,
        model=model,
        region=region,
        top_k=top_k,
    )


@router.get("/composite-price/search", response_model=CompositePriceSearchResponse)
async def search_composite_prices(
    q: str = Query(default="", description="清单/综合单价查询词"),
    specialty: str = Query(default="", description="专业"),
    quota_code: str = Query(default="", description="定额编号"),
    region: str = Query(default="", description="地区"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    _ = user
    return _search_composite_prices(
        q=q,
        specialty=specialty,
        quota_code=quota_code,
        region=region,
        page=page,
        size=size,
    )


@router.get("/composite-price", response_model=CompositePriceReferenceResponse)
async def get_composite_price_reference(
    q: str = Query(description="清单/综合单价查询词"),
    specialty: str = Query(default="", description="专业"),
    quota_code: str = Query(default="", description="定额编号"),
    region: str = Query(default="", description="地区"),
    top_k: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    _ = user
    return _get_composite_price_reference(
        q=q,
        specialty=specialty,
        quota_code=quota_code,
        region=region,
        top_k=top_k,
    )
