"""
额度管理相关的请求/响应数据格式

定义额度查询、购买、管理等接口的数据合同。
"""

import uuid
from datetime import datetime
from pydantic import BaseModel, Field


# ============================================================
# 额度查询
# ============================================================

class QuotaBalanceResponse(BaseModel):
    """额度余额响应"""
    balance: int = Field(description="剩余额度（条）")
    total_used: int = Field(description="已使用总量（条）")
    total_purchased: int = Field(description="已购买总量（条）")


class QuotaLogItem(BaseModel):
    """单条额度变动记录"""
    id: int
    change_type: str = Field(description="变动类型: register_gift/task_deduct/purchase/admin_adjust")
    amount: int = Field(description="变动数量（正=增加，负=扣减）")
    balance_after: int = Field(description="变动后余额")
    ref_id: str | None = Field(description="关联的任务ID或订单ID")
    note: str = Field(description="说明")
    created_at: datetime

    model_config = {"from_attributes": True}


class QuotaLogListResponse(BaseModel):
    """额度变动记录列表"""
    items: list[QuotaLogItem]
    total: int
    page: int
    size: int


# ============================================================
# 额度包
# ============================================================

class PackageItem(BaseModel):
    """额度包信息"""
    id: str = Field(description="额度包ID，如 pkg_500")
    name: str = Field(description="名称，如 500条额度包")
    quota: int = Field(description="额度条数")
    price: float = Field(description="价格（元）")


class PackageListResponse(BaseModel):
    """额度包列表"""
    items: list[PackageItem]


# ============================================================
# 订单
# ============================================================

class CreateOrderRequest(BaseModel):
    """创建支付订单请求"""
    package_id: str = Field(description="额度包ID，如 pkg_500")
    pay_type: str = Field(description="支付方式: alipay 或 wxpay")


class CreateOrderResponse(BaseModel):
    """创建订单响应"""
    order_id: str = Field(description="订单UUID")
    out_trade_no: str = Field(description="商户订单号")
    pay_url: str = Field(description="支付跳转URL")


class OrderResponse(BaseModel):
    """订单详情"""
    id: uuid.UUID
    out_trade_no: str
    package_name: str
    package_quota: int
    amount: float
    pay_type: str
    status: str
    trade_no: str | None
    created_at: datetime
    paid_at: datetime | None

    model_config = {"from_attributes": True}


class OrderListResponse(BaseModel):
    """订单列表"""
    items: list[OrderResponse]
    total: int
    page: int
    size: int
    total_amount: float = Field(description="总金额（元）")


# ============================================================
# 管理员操作
# ============================================================

class AdminAdjustRequest(BaseModel):
    """管理员调整额度请求"""
    user_id: uuid.UUID = Field(description="用户ID")
    amount: int = Field(ge=-1000000, le=1000000, description="调整数量（正数增加，负数扣减）")
    note: str = Field(min_length=1, max_length=200, description="调整原因（必填）")


class AdminAdjustResponse(BaseModel):
    """管理员调整额度响应"""
    message: str
    new_balance: int


class AdminUserQuotaItem(BaseModel):
    """管理员查看的用户额度信息"""
    user_id: uuid.UUID
    email: str
    nickname: str
    quota_balance: int
    total_used: int
    total_purchased: int


class AdminUserQuotaListResponse(BaseModel):
    """用户额度列表"""
    items: list[AdminUserQuotaItem]
    total: int
    page: int
    size: int
