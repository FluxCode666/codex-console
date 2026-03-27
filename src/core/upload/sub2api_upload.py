"""
Sub2API 账号上传功能
逐个创建方式 POST /api/v1/admin/accounts
"""

import json
import logging
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Any
from urllib.parse import quote

from curl_cffi import requests as cffi_requests

from ...database.session import get_db
from ...database.models import Account

logger = logging.getLogger(__name__)


def _extract_error_message(response) -> str:
    """从 Sub2API 响应中提取可读错误信息。"""
    default_msg = f"上传失败: HTTP {response.status_code}"
    try:
        detail = response.json()
        if isinstance(detail, dict):
            return detail.get("message", detail.get("error", default_msg))
    except Exception:
        pass
    return f"{default_msg} - {response.text[:200]}"


def _find_account_id_by_name(api_url: str, api_key: str, account_name: str) -> Optional[int]:
    """按账号名在 Sub2API 中查找账号 ID。"""
    url = api_url.rstrip("/") + "/api/v1/admin/accounts"
    headers = {"x-api-key": api_key}
    search = quote(account_name)

    try:
        response = cffi_requests.get(
            f"{url}?page=1&page_size=50&search={search}",
            headers=headers,
            proxies=None,
            timeout=15,
            impersonate="chrome110",
        )
        if response.status_code != 200:
            return None

        data = response.json()
        items = (
            data.get("data", {}).get("items", [])
            if isinstance(data, dict)
            else []
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("name") == account_name:
                account_id = item.get("id")
                if isinstance(account_id, int):
                    return account_id
        return None
    except Exception:
        return None


def _update_account_by_id(
    account_id: int,
    payload: Dict[str, Any],
    api_url: str,
    api_key: str,
) -> Tuple[bool, str]:
    """按账号 ID 调用 Sub2API 更新接口。"""
    url = api_url.rstrip("/") + f"/api/v1/admin/accounts/{account_id}"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }

    # 更新接口不需要 platform 字段
    update_payload = {
        "name": payload.get("name"),
        "type": payload.get("type"),
        "credentials": payload.get("credentials"),
        "extra": payload.get("extra"),
        "concurrency": payload.get("concurrency"),
        "priority": payload.get("priority"),
        "rate_multiplier": payload.get("rate_multiplier"),
        "group_ids": payload.get("group_ids"),
        "expires_at": payload.get("expires_at"),
        "auto_pause_on_expired": payload.get("auto_pause_on_expired"),
    }

    # proxy_id 有外键约束，仅在 payload 中存在时才传给更新接口
    if "proxy_id" in payload:
        update_payload["proxy_id"] = payload["proxy_id"]

    try:
        response = cffi_requests.put(
            url,
            json=update_payload,
            headers=headers,
            proxies=None,
            timeout=30,
            impersonate="chrome110",
        )

        if response.status_code in (200, 201):
            return True, "账号已存在，已更新"
        return False, _extract_error_message(response)
    except Exception as e:
        return False, f"更新异常: {str(e)}"


def _build_account_payload(
    account: Account,
    proxy_id: Optional[int] = None,
    group_ids: Optional[List[int]] = None,
    concurrency: int = 3,
    priority: int = 50,
) -> Dict[str, Any]:
    """构建单个账号的 Sub2API API payload。"""
    expires_at = int(account.expires_at.timestamp()) if account.expires_at else 0

    payload = {
        "name": account.email,
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": account.access_token or "",
            "chatgpt_account_id": account.account_id or "",
            "chatgpt_user_id": "",
            "client_id": account.client_id or "",
            "expires_at": expires_at,
            "expires_in": 863999,
            "model_mapping": {
                "gpt-5.1": "gpt-5.1",
                "gpt-5.1-codex": "gpt-5.1-codex",
                "gpt-5.1-codex-max": "gpt-5.1-codex-max",
                "gpt-5.1-codex-mini": "gpt-5.1-codex-mini",
                "gpt-5.2": "gpt-5.2",
                "gpt-5.2-codex": "gpt-5.2-codex",
                "gpt-5.3": "gpt-5.3",
                "gpt-5.3-codex": "gpt-5.3-codex",
                "gpt-5.4": "gpt-5.4",
            },
            "organization_id": account.workspace_id or "",
            "refresh_token": account.refresh_token or "",
        },
        "extra": {},
        "group_ids": group_ids or [],
        "concurrency": concurrency,
        "priority": priority,
        "rate_multiplier": 1,
        "auto_pause_on_expired": True,
    }

    # proxy_id 有外键约束，为空时不传该字段（让 Go 端 *int64 保持 nil）
    if proxy_id:
        payload["proxy_id"] = proxy_id

    return payload


def upload_single_to_sub2api(
    account: Account,
    api_url: str,
    api_key: str,
    concurrency: int = 3,
    priority: int = 50,
    proxy_id: Optional[int] = None,
    group_ids: Optional[List[int]] = None,
) -> Tuple[bool, str]:
    """上传单个账号到 Sub2API 平台。"""
    if not api_url:
        return False, "Sub2API URL 未配置"
    if not api_key:
        return False, "Sub2API API Key 未配置"
    if not account.access_token:
        return False, "账号缺少 access_token"

    payload = _build_account_payload(
        account=account,
        proxy_id=proxy_id,
        group_ids=group_ids,
        concurrency=concurrency,
        priority=priority,
    )

    url = api_url.rstrip("/") + "/api/v1/admin/accounts"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "Idempotency-Key": f"create-{account.email}-{int(datetime.utcnow().timestamp())}",
    }

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

        # 兼容同名账号创建冲突：查找后走更新。
        existing_account_id = _find_account_id_by_name(api_url, api_key, account.email)
        if existing_account_id:
            return _update_account_by_id(existing_account_id, payload, api_url, api_key)

        return False, _extract_error_message(response)

    except Exception as e:
        logger.error(f"Sub2API 上传异常: {e}")
        return False, f"上传异常: {str(e)}"


def upload_to_sub2api(
    accounts: List[Account],
    api_url: str,
    api_key: str,
    concurrency: int = 3,
    priority: int = 50,
    proxy_id: Optional[int] = None,
    group_ids: Optional[List[int]] = None,
) -> Tuple[bool, str]:
    """
    上传账号列表到 Sub2API 平台（逐个创建）

    Args:
        accounts: 账号模型实例列表
        api_url: Sub2API 地址，如 http://host
        api_key: Admin API Key（x-api-key header）
        concurrency: 账号并发数，默认 3
        priority: 账号优先级，默认 50
        proxy_id: 代理 ID，为 None 时不设置代理
        group_ids: 分组 ID 列表

    Returns:
        (成功标志, 消息)
    """
    if not accounts:
        return False, "无可上传的账号"

    if not api_url:
        return False, "Sub2API URL 未配置"

    if not api_key:
        return False, "Sub2API API Key 未配置"

    success_count = 0
    failed_count = 0
    last_error = ""

    for acc in accounts:
        success, message = upload_single_to_sub2api(
            account=acc,
            api_url=api_url,
            api_key=api_key,
            concurrency=concurrency,
            priority=priority,
            proxy_id=proxy_id,
            group_ids=group_ids,
        )
        if success:
            success_count += 1
        else:
            failed_count += 1
            last_error = message

    if failed_count > 0:
        return False, f"部分上传失败: 成功 {success_count}，失败 {failed_count}，最后错误: {last_error}"

    return True, f"成功上传 {success_count} 个账号"


def batch_upload_to_sub2api(
    account_ids: List[int],
    api_url: str,
    api_key: str,
    concurrency: int = 3,
    priority: int = 50,
    proxy_ids: Optional[List[int]] = None,
    group_ids: Optional[List[int]] = None,
) -> dict:
    """
    批量上传指定 ID 的账号到 Sub2API 平台

    当 proxy_ids 包含多个代理 ID 时，按轮询方式分配给每个账号。

    Returns:
        包含成功/失败/跳过统计和详情的字典
    """
    results = {
        "success_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "details": []
    }

    # 有效代理列表（过滤 0 和空值）
    effective_proxy_ids = [pid for pid in (proxy_ids or []) if pid]

    upload_index = 0  # 只对实际尝试上传的账号递增，用于代理轮询

    with get_db() as db:
        accounts = []
        for account_id in account_ids:
            acc = db.query(Account).filter(Account.id == account_id).first()
            if not acc:
                results["failed_count"] += 1
                results["details"].append({"id": account_id, "email": None, "success": False, "error": "账号不存在"})
                continue
            if not acc.access_token:
                results["skipped_count"] += 1
                results["details"].append({"id": account_id, "email": acc.email, "success": False, "error": "缺少 access_token"})
                continue
            accounts.append(acc)

        if not accounts:
            return results

        for acc in accounts:
            if effective_proxy_ids:
                current_proxy_id = effective_proxy_ids[upload_index % len(effective_proxy_ids)]
            else:
                current_proxy_id = None

            success, message = upload_single_to_sub2api(
                account=acc,
                api_url=api_url,
                api_key=api_key,
                concurrency=concurrency,
                priority=priority,
                proxy_id=current_proxy_id,
                group_ids=group_ids,
            )
            upload_index += 1

            if success:
                results["success_count"] += 1
                results["details"].append({
                    "id": acc.id,
                    "email": acc.email,
                    "success": True,
                    "message": message,
                    "proxy_id": current_proxy_id,
                })
            else:
                results["failed_count"] += 1
                results["details"].append({
                    "id": acc.id,
                    "email": acc.email,
                    "success": False,
                    "error": message,
                    "proxy_id": current_proxy_id,
                })

    return results


def test_sub2api_connection(api_url: str, api_key: str) -> Tuple[bool, str]:
    """
    测试 Sub2API 连接（GET /api/v1/admin/accounts 探活）

    Returns:
        (成功标志, 消息)
    """
    if not api_url:
        return False, "API URL 不能为空"
    if not api_key:
        return False, "API Key 不能为空"

    url = api_url.rstrip("/") + "/api/v1/admin/accounts"
    headers = {"x-api-key": api_key}

    try:
        response = cffi_requests.get(
            url,
            headers=headers,
            proxies=None,
            timeout=10,
            impersonate="chrome110",
        )

        if response.status_code in (200, 201, 204, 405):
            return True, "Sub2API 连接测试成功"
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
