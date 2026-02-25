"""
咨询模块的请求/响应格式定义
"""

from pydantic import BaseModel


# ============================================================
# 对话消息
# ============================================================

class ChatMessage(BaseModel):
    """单条对话消息"""
    role: str               # "user" 或 "assistant"
    content: str            # 消息文本
    image_base64: str = ""  # 图片的 base64 编码（可选，仅 user 消息）
    image_type: str = ""    # 图片 MIME 类型（如 image/png）


class ChatRequest(BaseModel):
    """对话请求（前端发送完整对话历史）"""
    messages: list[ChatMessage]


# ============================================================
# 解析结果
# ============================================================

class ParsedItem(BaseModel):
    """清单→定额对应"""
    bill_name: str = ""
    quota_id: str = ""
    quota_name: str = ""
    unit: str = ""


# ============================================================
# 提交和审核
# ============================================================

class ConsultSubmitRequest(BaseModel):
    """用户确认提交"""
    items: list[ParsedItem]
    province: str
    image_path: str = ""    # 可选（对话模式可能没有图片）


class ConsultReviewRequest(BaseModel):
    """管理员审核"""
    action: str             # "approve" 或 "reject"
    note: str = ""
