"""
FluxCode 账号上传功能
逐个创建方式 POST /api/v1/admin/accounts
参照 gpt.py 的 save_account_to_admin 数据结构
"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Tuple, Optional, Dict, Any

from curl_cffi import requests as cffi_requests

from ...database.session import get_db
from ...database.models import Account

logger = logging.getLogger(__name__)


def _build_account_payload(
    account: Account,
    proxy_id: int = 0,
    group_ids: Optional[List[int]] = None,
    concurrency: int = 3,
    account_priority: int = 50,
    rate_multiplier: float = 1.0,
    auto_pause_on_expired: bool = True,
) -> Dict[str, Any]:
    """
    构建单个账号的 FluxCode API payload

    参考 gpt.py 的 save_account_to_admin 函数结构
    """
    # notes JSON
    notes_json = {"email": account.email}
    if account.password:
        notes_json["password"] = account.password

    # expires_at: RFC3339 格式
    expires_at_str = ""
    expires_at_ts = 0
    if account.expires_at:
        if account.expires_at.tzinfo is None:
            expires_at_utc = account.expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at_utc = account.expires_at
        expires_at_str = expires_at_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        expires_at_ts = int(expires_at_utc.timestamp())

    # credentials
    credentials = {
        "access_token": account.access_token or "",
        "refresh_token": account.refresh_token or "",
        "expires_in": 863999,
        "expires_at": expires_at_ts,
        "chatgpt_account_id": account.account_id or "",
        "chatgpt_user_id": "",
        "organization_id": account.workspace_id or "",
    }
    if account.client_id:
        credentials["client_id"] = account.client_id

    payload = {
        "name": account.email,
        "notes": json.dumps(notes_json, ensure_ascii=False),
        "platform": "openai",
        "type": "oauth",
        "credentials": credentials,
        "extra": {"email": account.email},
        "proxy_id": proxy_id,
        "concurrency": concurrency,
        "priority": account_priority,
        "rate_multiplier": rate_multiplier,
        "group_ids": group_ids or [],
        "auto_pause_on_expired": auto_pause_on_expired,
    }

    if expires_at_str:
        payload["expires_at"] = expires_at_str

    return payload


def upload_single_to_fluxcode(
    account: Account,
    api_url: str,
    api_key: str,
    proxy_id: int = 0,
    group_ids: Optional[List[int]] = None,
    concurrency: int = 3,
    account_priority: int = 50,
    rate_multiplier: float = 1.0,
    auto_pause_on_expired: bool = True,
) -> Tuple[bool, str]:
    """
    上传单个账号到 FluxCode 平台（逐个创建）

    Args:
        account: 账号模型实例
        api_url: FluxCode API 地址
        api_key: API Key（同时用于 x-api-key 和 Bearer）
        proxy_id: 代理 ID
        group_ids: 分组 ID 列表
        concurrency: 并发数
        account_priority: 账号优先级
        rate_multiplier: 速率倍率
        auto_pause_on_expired: 过期自动暂停

    Returns:
        (成功标志, 消息)
    """
    if not api_url:
        return False, "FluxCode API URL 未配置"
    if not api_key:
        return False, "FluxCode API Key 未配置"
    if not account.access_token:
        return False, "账号缺少 access_token"

    url = api_url.rstrip("/") + "/api/v1/admin/accounts"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "authorization": f"Bearer {api_key}",
    }

    payload = _build_account_payload(
        account=account,
        proxy_id=proxy_id,
        group_ids=group_ids,
        concurrency=concurrency,
        account_priority=account_priority,
        rate_multiplier=rate_multiplier,
        auto_pause_on_expired=auto_pause_on_expired,
    )

    try:
        response = cffi_requests.post(
            url,
            json=payload,
            headers=headers,
            proxies=None,
            timeout=30,
            impersonate="chrome110",
        )

        if response.status_code in (200, 201):
            return True, "上传成功"

        error_msg = f"上传失败: HTTP {response.status_code}"
        try:
            detail = response.json()
            if isinstance(detail, dict):
                error_msg = detail.get("message", detail.get("error", error_msg))
        except Exception:
            error_msg = f"{error_msg} - {response.text[:200]}"
        return False, error_msg

    except Exception as e:
        logger.error(f"FluxCode 上传异常: {e}")
        return False, f"上传异常: {str(e)}"


def batch_upload_to_fluxcode(
    account_ids: List[int],
    api_url: str,
    api_key: str,
    proxy_ids: Optional[List[int]] = None,
    group_ids: Optional[List[int]] = None,
    concurrency: int = 3,
    account_priority: int = 50,
    rate_multiplier: float = 1.0,
    auto_pause_on_expired: bool = True,
) -> dict:
    """
    批量上传指定 ID 的账号到 FluxCode 平台（逐个创建方式）

    对每个账号逐个调用 POST /api/v1/admin/accounts
    当 proxy_ids 包含多个代理 ID 时，按轮询方式分配给每个账号。

    Returns:
        包含成功/失败/跳过统计和详情的字典
    """
    results = {
        "success_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "details": [],
    }

    # 有效代理列表（过滤 0 和空值）
    effective_proxy_ids = [pid for pid in (proxy_ids or []) if pid]

    upload_index = 0  # 用于轮询计数（只对实际上传的账号递增）

    with get_db() as db:
        for account_id in account_ids:
            acc = db.query(Account).filter(Account.id == account_id).first()
            if not acc:
                results["failed_count"] += 1
                results["details"].append({
                    "id": account_id, "email": None,
                    "success": False, "error": "账号不存在",
                })
                continue
            if not acc.access_token:
                results["skipped_count"] += 1
                results["details"].append({
                    "id": account_id, "email": acc.email,
                    "success": False, "error": "缺少 access_token",
                })
                continue

            # 轮询分配代理 ID
            if effective_proxy_ids:
                current_proxy_id = effective_proxy_ids[upload_index % len(effective_proxy_ids)]
            else:
                current_proxy_id = 0

            success, message = upload_single_to_fluxcode(
                account=acc,
                api_url=api_url,
                api_key=api_key,
                proxy_id=current_proxy_id,
                group_ids=group_ids,
                concurrency=concurrency,
                account_priority=account_priority,
                rate_multiplier=rate_multiplier,
                auto_pause_on_expired=auto_pause_on_expired,
            )

            upload_index += 1  # 每个实际尝试上传的账号递增

            if success:
                results["success_count"] += 1
                results["details"].append({
                    "id": acc.id, "email": acc.email,
                    "success": True, "message": message,
                    "proxy_id": current_proxy_id,
                })
            else:
                results["failed_count"] += 1
                results["details"].append({
                    "id": acc.id, "email": acc.email,
                    "success": False, "error": message,
                    "proxy_id": current_proxy_id,
                })

    return results


def test_fluxcode_connection(api_url: str, api_key: str) -> Tuple[bool, str]:
    """
    测试 FluxCode 连接

    使用 GET /api/v1/admin/accounts 作为探活端点

    Returns:
        (成功标志, 消息)
    """
    if not api_url:
        return False, "API URL 不能为空"
    if not api_key:
        return False, "API Key 不能为空"

    url = api_url.rstrip("/") + "/api/v1/admin/accounts"
    headers = {
        "x-api-key": api_key,
        "authorization": f"Bearer {api_key}",
    }

    try:
        response = cffi_requests.get(
            url,
            headers=headers,
            proxies=None,
            timeout=10,
            impersonate="chrome110",
        )

        if response.status_code in (200, 201, 204, 405):
            return True, "FluxCode 连接测试成功"
        if response.status_code == 401:
            return False, "连接成功，但 API Key 无效"
        if response.status_code == 403:
            return False, "连接成功，但权限不足"

        return False, f"服务器返回异常状态码: {response.status_code}"

    except cffi_requests.exceptions.ConnectionError as e:
        return False, f"无法连接到服务器: {str(e)}"
    except cffi_requests.exceptions.Timeout:
        return False, "连接超时，请检查网络配置"
    except Exception as e:
        return False, f"连接测试失败: {str(e)}"
