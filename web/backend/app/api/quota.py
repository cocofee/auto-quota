"""
用户端额度管理 API

提供额度查询、使用记录、额度包列表、创建支付订单、支付回调等接口。

路由挂载在 /api/quota 前缀下:
    GET    /api/quota/balance        — 查询余额
    GET    /api/quota/logs           — 使用记录（分页）
    GET    /api/quota/packages       — 额度包列表
    POST   /api/quota/create-order   — 创建支付订单
    GET    /api/quota/order/{id}     — 查询订单状态
    POST   /api/quota/notify         — 好易支付异步回调（不需要登录）
    GET    /api/quota/return         — 支付完成跳转（不需要登录）
"""

import uuid
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.database import get_db, get_sync_session
from app.models.user import User
from app.models.order import Order
from app.models.quota_log import QuotaLog
from app.auth.deps import get_current_user
from app.schemas.quota import (
    QuotaBalanceResponse,
    QuotaLogItem,
    QuotaLogListResponse,
    PackageItem,
    PackageListResponse,
    CreateOrderRequest,
    CreateOrderResponse,
    OrderResponse,
)
from app.services.quota_service import (
    get_balance_info,
    QUOTA_PACKAGES,
    get_package_by_id,
    add_quota_sync,
)
from app.services.payment_service import (
    generate_trade_no,
    build_pay_url,
    verify_sign,
)
from app.config import EPAY_KEY, EPAY_RETURN_BASE_URL

router = APIRouter()


@router.get("/balance", response_model=QuotaBalanceResponse)
async def get_quota_balance(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查询当前用户的额度余额和使用统计"""
    info = await get_balance_info(db, user.id)
    return QuotaBalanceResponse(**info)


@router.get("/logs", response_model=QuotaLogListResponse)
async def get_quota_logs(
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查询额度变动记录（分页，最新的在前面）"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    # 查总数
    count_result = await db.execute(
        select(func.count()).select_from(QuotaLog).where(QuotaLog.user_id == user.id)
    )
    total = count_result.scalar_one()

    # 分页查询
    query = (
        select(QuotaLog)
        .where(QuotaLog.user_id == user.id)
        .order_by(desc(QuotaLog.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    result = await db.execute(query)
    logs = result.scalars().all()

    return QuotaLogListResponse(
        items=[QuotaLogItem.model_validate(log) for log in logs],
        total=total,
        page=page,
        size=size,
    )


@router.get("/packages", response_model=PackageListResponse)
async def get_quota_packages(user: User = Depends(get_current_user)):
    """获取可购买的额度包列表"""
    items = [PackageItem(**pkg) for pkg in QUOTA_PACKAGES]
    return PackageListResponse(items=items)


@router.post("/create-order", response_model=CreateOrderResponse)
async def create_order(
    req: CreateOrderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建支付订单

    选择额度包和支付方式 → 生成订单 → 返回支付跳转URL。
    前端拿到 pay_url 后跳转到好易支付收银台页面。
    """
    # 1. 校验额度包
    package = get_package_by_id(req.package_id)
    if not package:
        raise HTTPException(status_code=400, detail=f"额度包不存在: {req.package_id}")

    # 2. 校验支付方式
    if req.pay_type not in ("alipay", "wxpay"):
        raise HTTPException(status_code=400, detail="支付方式必须是 alipay 或 wxpay")

    # 3. 生成订单
    order_id = uuid.uuid4()
    out_trade_no = generate_trade_no()

    order = Order(
        id=order_id,
        user_id=user.id,
        out_trade_no=out_trade_no,
        package_name=package["name"],
        package_quota=package["quota"],
        amount=package["price"],
        pay_type=req.pay_type,
        status="pending",
    )
    db.add(order)
    await db.commit()

    # 4. 构造支付URL
    try:
        pay_url = build_pay_url(
            out_trade_no=out_trade_no,
            amount=package["price"],
            name=package["name"],
            pay_type=req.pay_type,
            order_id=str(order_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    logger.info(f"用户 {user.id} 创建订单 {out_trade_no}: {package['name']} ¥{package['price']}")

    return CreateOrderResponse(
        order_id=str(order_id),
        out_trade_no=out_trade_no,
        pay_url=pay_url,
    )


@router.get("/order/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查询订单状态

    前端在支付完成页轮询此接口，检测订单是否已支付成功。
    """
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.user_id == user.id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")

    return OrderResponse.model_validate(order)


@router.api_route("/notify", methods=["GET", "POST"], response_class=PlainTextResponse)
async def payment_notify(request: Request):
    """好易支付异步回调（支付成功通知）

    好易支付在用户支付成功后调用此接口（GET 或 POST）。
    注意：此接口不需要登录认证，通过 MD5 签名验证请求合法性。

    处理步骤：
    1. 验签（防伪造）
    2. 查订单
    3. 幂等检查（已处理的直接返回 success）
    4. 核对金额（防篡改）
    5. 事务内更新订单+充值额度+记日志
    6. 返回 "success"（必须是纯文本，否则平台会重复通知）
    """
    # 获取参数（好易支付可能用 GET 或 POST）
    if request.method == "POST":
        form_data = await request.form()
        params = {k: v for k, v in form_data.items()}
    else:
        params = dict(request.query_params)

    logger.info(f"收到支付回调: {params}")

    # 第1步：验签
    if not EPAY_KEY:
        logger.error("好易支付密钥未配置（EPAY_KEY），无法验签")
        return PlainTextResponse("fail")

    if not verify_sign(params, EPAY_KEY):
        logger.warning(f"支付回调验签失败: {params}")
        return PlainTextResponse("fail")

    # 第2步：检查交易状态
    trade_status = params.get("trade_status", "")
    if trade_status != "TRADE_SUCCESS":
        logger.info(f"支付回调：交易状态非成功: {trade_status}")
        return PlainTextResponse("success")  # 非成功状态也返回 success，不让平台重试

    # 第3步：查订单
    out_trade_no = params.get("out_trade_no", "")
    if not out_trade_no:
        logger.warning("支付回调缺少 out_trade_no")
        return PlainTextResponse("fail")

    # 使用同步会话（回调处理需要事务控制），放到线程池避免阻塞事件循环
    def _process_payment():
        """同步处理支付回调（在线程池中执行）"""
        session = get_sync_session()
        try:
            from sqlalchemy import select as sync_select
            # 加行锁（with_for_update）防止并发回调重复充值
            order = session.execute(
                sync_select(Order).where(Order.out_trade_no == out_trade_no).with_for_update()
            ).scalar_one_or_none()

            if not order:
                logger.warning(f"支付回调：订单不存在: {out_trade_no}")
                return "fail"

            # 幂等检查（订单已支付，直接返回 success）
            if order.status == "paid":
                logger.info(f"支付回调：订单已处理过，跳过: {out_trade_no}")
                return "success"

            # 核对金额（防篡改）
            # 用 Decimal 精确比较，避免 float 与 Decimal 运算的 TypeError
            try:
                from decimal import Decimal, InvalidOperation
                callback_money = Decimal(str(params.get("money", "0")))
            except (InvalidOperation, ValueError, TypeError):
                logger.warning(f"支付回调：金额格式无效: {params.get('money')}")
                return "fail"

            if abs(callback_money - order.amount) > Decimal("0.01"):  # 允许1分钱误差
                logger.warning(
                    f"支付回调：金额不一致！订单={order.amount}, 回调={callback_money}, "
                    f"订单号={out_trade_no}"
                )
                return "fail"

            # 事务内处理（更新订单 + 充值额度 + 记日志）
            order.status = "paid"
            order.trade_no = params.get("trade_no", "")
            order.paid_at = datetime.now(timezone.utc)

            # 充值额度（原子操作+记日志）
            add_quota_sync(
                session=session,
                user_id=order.user_id,
                count=order.package_quota,
                order_id=str(order.id),
                package_name=order.package_name,
            )

            session.commit()
            logger.info(
                f"支付成功: 订单={out_trade_no}, 用户={order.user_id}, "
                f"充值={order.package_quota}条, 金额=¥{order.amount}"
            )
            return "success"

        except Exception as e:
            logger.exception(f"支付回调处理异常: {e}")  # exception()自动附带完整堆栈
            try:
                session.rollback()
            except Exception:
                pass
            return "fail"
        finally:
            session.close()

    result = await asyncio.to_thread(_process_payment)
    return PlainTextResponse(result)


@router.get("/return")
async def payment_return(request: Request):
    """支付完成后的同步跳转

    用户在好易支付页面付款后，浏览器跳转到这个地址。
    这里只做简单验签，然后重定向到前端的支付结果页。

    注意：同步跳转不可靠（用户可能关浏览器），
    真正的业务处理在 notify 接口中完成。
    """
    params = dict(request.query_params)
    order_id = params.get("order_id", "")

    # 校验 order_id 格式（防止开放重定向/注入）
    try:
        uuid.UUID(order_id)
    except (ValueError, AttributeError):
        logger.warning(f"支付跳转：order_id 格式无效: {order_id}")
        return RedirectResponse(url=f"{EPAY_RETURN_BASE_URL}/dashboard")

    # 重定向到前端支付结果页
    frontend_url = f"{EPAY_RETURN_BASE_URL}/quota/pay-result?order_id={order_id}"
    return RedirectResponse(url=frontend_url)
