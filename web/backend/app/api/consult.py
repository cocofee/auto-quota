"""
定额咨询 API

用户和贾维斯多轮对话 → 确认后提取结果 → 提交管理员审核 → 存入经验库。
支持文字提问和贴图。

路由:
    POST /api/consult/chat                  — 多轮对话（发消息给贾维斯）
    POST /api/consult/extract               — 从对话中提取清单→定额对应关系
    POST /api/consult/submit                — 用户确认提交审核
    GET  /api/consult/submissions           — 用户查看自己的提交
    GET  /api/consult/admin/pending         — 管理员查看待审列表
    POST /api/consult/admin/{id}/review     — 管理员审批（通过/拒绝）
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.consult import ConsultSubmission
from app.models.user import User
from app.auth.deps import get_current_user
from app.auth.permissions import require_admin
from app.schemas.consult import (
    ConsultSubmitRequest, ConsultReviewRequest,
    ChatRequest, ChatMessage, ParsedItem,
)
from app.api.shared import store_experience_batch

router = APIRouter()

# 每日对话次数限制（每个用户每天最多发送的消息数）
DAILY_CHAT_LIMIT = 50

# 简单的内存计数器（key: "user_id:日期", value: 已用次数）
# 单进程足够用，重启后计数清零（宽容设计）
_daily_usage: dict[str, int] = {}


def _check_daily_limit(user_id: uuid.UUID) -> None:
    """检查用户当日是否超出对话次数限制"""
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"{user_id}:{today}"

    # 清理过期的计数（只保留今天的）
    expired = [k for k in _daily_usage if not k.endswith(today)]
    for k in expired:
        del _daily_usage[k]

    count = _daily_usage.get(key, 0)
    if count >= DAILY_CHAT_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"今日对话次数已达上限（{DAILY_CHAT_LIMIT}次），请明天再试"
        )
    _daily_usage[key] = count + 1


# 贾维斯的系统提示词
JARVIS_SYSTEM_PROMPT = """你是贾维斯（Jarvis），一个专业的工程造价AI助手。
你的专长是帮助造价人员确定清单项应该套用哪些定额子目。

工作方式：
1. 用户会描述一个清单项（名称、规格、用途等），可能附带截图
2. 你根据造价知识推荐最合适的定额编号和名称
3. 解释你的推荐理由
4. 如果用户有疑问，继续讨论直到达成一致

回答要求：
- 定额编号格式：C开头+册号-章-节，如 C10-6-30、C4-11-25
- 12大册分类：C1机械设备、C4电气、C5智能化、C7通风空调、C8工业管道、C9消防、C10给排水、C12刷油防腐等
- 给出推荐时，说清楚理由（为什么选这个定额而不是其他的）
- 如果不确定，说明可能的几个选项让用户选择
- 用简洁专业的语言，不要废话"""


def _call_claude_chat(messages: list[dict], system: str = "") -> str:
    """调用 Claude API 进行多轮对话

    支持文字和图片混合消息。
    中转模式和官方模式两种（和 agent_matcher.py 保持一致）。
    """
    import config as quota_config

    if quota_config.CLAUDE_BASE_URL:
        # 中转模式
        import httpx

        url = f"{quota_config.CLAUDE_BASE_URL.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": quota_config.CLAUDE_API_KEY,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": quota_config.CLAUDE_MODEL,
            "max_tokens": 4000,
            "temperature": 0.3,
            "messages": messages,
        }
        if system:
            body["system"] = system
        resp = httpx.post(url, headers=headers, json=body, timeout=90)
        # 解析错误响应（Claude API 返回的 error JSON）
        if resp.status_code != 200:
            try:
                err_data = resp.json()
                err_msg = err_data.get("error", {}).get("message", resp.text[:200])
            except Exception:
                err_msg = resp.text[:200]
            raise RuntimeError(f"Claude API 错误 ({resp.status_code}): {err_msg}")
        data = resp.json()
        # 防御性检查响应结构
        content_list = data.get("content", [])
        if not content_list or not isinstance(content_list, list):
            raise RuntimeError(f"Claude API 返回了意外的响应结构: {str(data)[:200]}")
        return content_list[0].get("text", "")
    else:
        # 官方 API 模式
        import anthropic

        client = anthropic.Anthropic(api_key=quota_config.CLAUDE_API_KEY)
        kwargs = {
            "model": quota_config.CLAUDE_MODEL,
            "max_tokens": 4000,
            "temperature": 0.3,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        message = client.messages.create(**kwargs)
        return message.content[0].text


def _parse_extract_response(raw_text: str) -> list[dict]:
    """从 AI 提取结果中解析 JSON 数组"""
    text = raw_text.strip()

    # 去掉 markdown 代码块标记
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        for key in ("items", "data", "results"):
            if key in result and isinstance(result[key], list):
                return result[key]
        return []
    except json.JSONDecodeError:
        logger.warning(f"AI 提取结果无法解析为 JSON: {text[:200]}...")
        return []


# ============================================================
# 端点1：多轮对话
# ============================================================

@router.post("/chat")
async def chat(
    req: ChatRequest,
    user: User = Depends(get_current_user),
):
    """和贾维斯对话

    前端维护完整的对话历史，每次请求带上所有历史消息。
    支持文字消息和图片消息（图片用 base64 编码）。
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="消息不能为空")

    # 每日使用次数检查
    _check_daily_limit(user.id)

    # 限制对话长度（防止 token 过多）
    if len(req.messages) > 30:
        raise HTTPException(status_code=400, detail="对话轮次过多（最多15轮），请开始新对话")

    # 构造 Claude API 的消息格式
    api_messages = []
    for msg in req.messages:
        if msg.role not in ("user", "assistant"):
            continue

        if msg.role == "assistant":
            # AI 回复只有文字
            api_messages.append({"role": "assistant", "content": msg.content})
        else:
            # 用户消息：可能包含文字+图片
            api_messages.append({"role": "user", "content": msg.content})

    try:
        reply = await asyncio.to_thread(
            _call_claude_chat, api_messages, JARVIS_SYSTEM_PROMPT
        )
    except Exception as e:
        logger.error(f"贾维斯对话失败: {e}")
        raise HTTPException(status_code=500, detail="AI 回复失败，请稍后重试")

    return {"reply": reply}


# ============================================================
# 端点2：上传对话中的图片
# ============================================================

@router.post("/extract")
async def extract_results(
    req: ChatRequest,
    user: User = Depends(get_current_user),
):
    """从对话历史中提取已确认的清单→定额对应关系

    发送完整对话给 AI，要求提取结构化数据。
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="对话历史不能为空")

    # 构造提取指令：在对话末尾加一条用户消息要求提取
    api_messages = []
    for msg in req.messages:
        if msg.role == "assistant":
            api_messages.append({"role": "assistant", "content": msg.content})
        elif msg.role == "user":
            api_messages.append({"role": "user", "content": msg.content})

    # 加提取指令
    extract_prompt = (
        "请从以上对话中提取所有已讨论确认的清单→定额对应关系。\n"
        "返回纯 JSON 数组，格式如下：\n"
        '[{"bill_name": "清单名称", "quota_id": "定额编号", "quota_name": "定额名称", "unit": "单位"}]\n\n'
        "注意：\n"
        "- 只提取对话中明确讨论过的项目\n"
        "- 如果对话中没有确认任何定额，返回空数组 []\n"
        "- 只返回 JSON，不要加其他文字"
    )
    api_messages.append({"role": "user", "content": extract_prompt})

    try:
        raw_text = await asyncio.to_thread(
            _call_claude_chat, api_messages, JARVIS_SYSTEM_PROMPT
        )
    except Exception as e:
        logger.error(f"提取对话结果失败: {e}")
        raise HTTPException(status_code=500, detail="提取失败，请稍后重试")

    parsed = _parse_extract_response(raw_text)
    items = [
        ParsedItem(
            bill_name=str(item.get("bill_name", "")),
            quota_id=str(item.get("quota_id", "")),
            quota_name=str(item.get("quota_name", "")),
            unit=str(item.get("unit", "")),
        )
        for item in parsed
    ]

    return {"items": items}


# ============================================================
# 端点4：用户确认提交
# ============================================================

@router.post("/submit")
async def submit_consult(
    req: ConsultSubmitRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """用户确认对话结果并提交

    提交后状态为 pending（待管理员审核）。
    数据暂不写入经验库，等管理员审核通过后再写。
    """
    if not req.items:
        raise HTTPException(status_code=400, detail="提交内容不能为空")
    if not req.province or not req.province.strip():
        raise HTTPException(status_code=400, detail="省份不能为空")

    submission = ConsultSubmission(
        user_id=user.id,
        image_path="",
        parsed_items=[item.model_dump() for item in req.items],
        submitted_items=[item.model_dump() for item in req.items],
        province=req.province,
        status="pending",
    )
    db.add(submission)
    await db.flush()

    logger.info(f"咨询提交成功: {submission.id}（{len(req.items)} 条，省份: {req.province}）")

    return {
        "message": "提交成功，等待管理员审核",
        "submission_id": str(submission.id),
        "item_count": len(req.items),
    }


# ============================================================
# 端点5：用户查看自己的提交历史
# ============================================================

@router.get("/submissions")
async def my_submissions(
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查看当前用户的咨询提交历史"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    count_result = await db.execute(
        select(func.count()).select_from(ConsultSubmission)
        .where(ConsultSubmission.user_id == user.id)
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * size
    result = await db.execute(
        select(ConsultSubmission)
        .where(ConsultSubmission.user_id == user.id)
        .order_by(ConsultSubmission.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    submissions = result.scalars().all()

    items = [
        {
            "id": str(s.id),
            "province": s.province,
            "item_count": len(s.submitted_items) if s.submitted_items else 0,
            "status": s.status,
            "review_note": s.review_note,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
        }
        for s in submissions
    ]

    return {"items": items, "total": total, "page": page, "size": size}


# ============================================================
# 端点6：管理员查看待审列表
# ============================================================

@router.get("/admin/pending")
async def admin_pending(
    page: int = 1,
    size: int = 20,
    status_filter: str = "pending",
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员查看咨询提交列表"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    base_query = select(ConsultSubmission)
    count_query = select(func.count()).select_from(ConsultSubmission)

    if status_filter != "all":
        base_query = base_query.where(ConsultSubmission.status == status_filter)
        count_query = count_query.where(ConsultSubmission.status == status_filter)

    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    offset = (page - 1) * size
    result = await db.execute(
        base_query
        .order_by(ConsultSubmission.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    submissions = result.scalars().all()

    items = [
        {
            "id": str(s.id),
            "user_id": str(s.user_id),
            "province": s.province,
            "item_count": len(s.submitted_items) if s.submitted_items else 0,
            "submitted_items": s.submitted_items,
            "image_path": s.image_path,
            "status": s.status,
            "review_note": s.review_note,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
        }
        for s in submissions
    ]

    return {"items": items, "total": total, "page": page, "size": size}


# ============================================================
# 端点7：管理员审批
# ============================================================

@router.post("/admin/{submission_id}/review")
async def review_submission(
    submission_id: uuid.UUID,
    req: ConsultReviewRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员审核咨询提交

    - approve: 通过 → 将数据写入经验库权威层
    - reject: 拒绝 → 标记为已拒绝，不写入经验库
    """
    if req.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action 必须是 approve 或 reject")

    result = await db.execute(
        select(ConsultSubmission).where(ConsultSubmission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    if submission.status != "pending":
        raise HTTPException(status_code=400, detail=f"该提交已{submission.status}，不能重复审核")

    submission.status = "approved" if req.action == "approve" else "rejected"
    submission.reviewed_by = admin.id
    submission.reviewed_at = datetime.now(timezone.utc)
    submission.review_note = req.note

    stored_count = 0
    if req.action == "approve" and submission.submitted_items:
        # 构造批量写入记录
        batch_records = [
            {
                "name": item.get("bill_name", ""),
                "quota_ids": [item.get("quota_id", "").strip()],
                "quota_names": [item.get("quota_name", "")],
            }
            for item in submission.submitted_items
            if item.get("quota_id", "").strip()
        ]
        # 审核场景需要严格保证一致性：写入失败时必须回滚审核状态
        # 不使用 store_experience_batch（那个会吞异常），直接调用 store_one
        try:
            from tools.jarvis_store import store_one

            def _store_all():
                count = 0
                for rec in batch_records:
                    if rec.get("quota_ids"):
                        ok = store_one(
                            name=rec["name"],
                            desc="",
                            quota_ids=rec["quota_ids"],
                            quota_names=rec.get("quota_names", []),
                            reason=f"Web端咨询审核通过 by {admin.email}",
                            specialty="",
                            province=submission.province,
                            confirmed=True,
                        )
                        if ok:
                            count += 1
                return count

            stored_count = await asyncio.to_thread(_store_all)
        except Exception as e:
            # 经验库写入失败 → 回滚审核状态为 pending，避免"已通过但没写入"的不一致
            logger.error(f"咨询审核写入经验库失败: {e}")
            submission.status = "pending"
            submission.reviewed_by = None
            submission.reviewed_at = None
            submission.review_note = f"[经验库写入失败，审核已回滚: {e}]"
            await db.flush()
            raise HTTPException(
                status_code=500,
                detail="经验库写入失败，审核已回滚为待审核状态",
            )

    await db.flush()

    action_text = "通过" if req.action == "approve" else "拒绝"
    logger.info(f"咨询审核{action_text}: {submission_id}（写入经验库 {stored_count} 条）")

    return {
        "message": f"审核{action_text}",
        "stored_count": stored_count,
        "total_items": len(submission.submitted_items) if submission.submitted_items else 0,
    }


# ============================================================
# 端点8：管理员查看咨询图片
# ============================================================

