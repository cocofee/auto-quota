"""
好易支付（彩虹易支付）对接服务

负责：签名生成、签名验证、支付URL构造。
好易支付的 API 非常简单，核心就是 MD5 签名。
"""

import hashlib
import hmac
import uuid
from datetime import datetime
from urllib.parse import urlencode

from loguru import logger

from app.config import EPAY_PID, EPAY_KEY, EPAY_URL, EPAY_NOTIFY_BASE_URL, EPAY_RETURN_BASE_URL


def make_sign(params: dict, key: str) -> str:
    """生成好易支付 MD5 签名

    签名步骤：
    1. 过滤掉 sign、sign_type 和空值参数
    2. 按参数名 ASCII 码升序排列（字典序）
    3. 拼接成 key=value&key=value 格式
    4. 末尾拼接商户密钥（注意：不是用 & 连接，直接拼）
    5. 对整个字符串做 MD5，取32位小写

    参数:
        params: 待签名的参数字典
        key: 商户密钥
    返回:
        32位小写 MD5 签名字符串
    """
    # 第1步：过滤
    filtered = {
        k: v for k, v in params.items()
        if k not in ("sign", "sign_type") and v not in (None, "")
    }
    # 第2步：排序
    sorted_keys = sorted(filtered.keys())
    # 第3步：拼接
    query_string = "&".join(f"{k}={filtered[k]}" for k in sorted_keys)
    # 第4步：加密钥
    sign_string = query_string + key
    # 第5步：MD5
    return hashlib.md5(sign_string.encode("utf-8")).hexdigest()


def verify_sign(params: dict, key: str) -> bool:
    """验证回调签名是否正确

    用同样的签名算法重新计算签名，和收到的 sign 参数对比。
    防止有人伪造支付成功的回调请求。

    参数:
        params: 回调传来的全部参数（含 sign）
        key: 商户密钥
    返回:
        True=签名正确，False=签名不匹配
    """
    received_sign = params.get("sign", "")
    calculated_sign = make_sign(params, key)
    # 用恒定时间比较防止时序攻击（攻击者无法通过响应时间逐字节猜签名）
    return hmac.compare_digest(received_sign, calculated_sign)


def generate_trade_no() -> str:
    """生成唯一的商户订单号

    格式: AQ + 年月日时分秒 + 8位随机十六进制
    例如: AQ20260227143059A1B2C3D4

    唯一性由 数据库 UNIQUE 约束保证，这里的随机后缀降低碰撞概率。
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_suffix = uuid.uuid4().hex[:8].upper()
    return f"AQ{timestamp}{random_suffix}"


def build_pay_url(out_trade_no: str, amount: float, name: str,
                  pay_type: str, order_id: str) -> str:
    """构造好易支付的支付跳转URL

    用户点击"立即支付"后，前端拿到这个URL跳转过去，
    会看到好易支付的收银台页面（支付宝/微信扫码或跳转APP）。

    参数:
        out_trade_no: 商户订单号
        amount: 支付金额（元，如 9.9）
        name: 商品名称（如"500条额度包"）
        pay_type: 支付方式（alipay/wxpay）
        order_id: 订单UUID（用于构造 return_url 参数）
    返回:
        完整的支付URL字符串
    """
    if not all([EPAY_PID, EPAY_KEY, EPAY_URL]):
        raise ValueError("好易支付配置不完整，请在 .env 中设置 EPAY_PID、EPAY_KEY、EPAY_URL")

    # 回调地址（好易支付服务器 → 我们的后端）
    notify_url = f"{EPAY_NOTIFY_BASE_URL}/api/quota/notify"
    # 同步跳转地址（用户支付完 → 我们的前端）
    return_url = f"{EPAY_RETURN_BASE_URL}/quota/pay-result?order_id={order_id}"

    params = {
        "pid": EPAY_PID,
        "type": pay_type,
        "out_trade_no": out_trade_no,
        "notify_url": notify_url,
        "return_url": return_url,
        "name": name,
        "money": f"{amount:.2f}",
        "sitename": "auto-quota",
    }

    # 生成签名
    params["sign"] = make_sign(params, EPAY_KEY)
    params["sign_type"] = "MD5"

    pay_url = f"{EPAY_URL}/submit.php?{urlencode(params)}"
    logger.info(f"生成支付URL: 订单={out_trade_no}, 金额={amount}, 方式={pay_type}")
    return pay_url
