"""
FluxCode 服务管理 API 路由
"""

import json
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ....database import crud
from ....database.session import get_db
from ....core.upload.fluxcode_upload import test_fluxcode_connection, batch_upload_to_fluxcode

router = APIRouter()


# ============== Pydantic Models ==============

class FluxCodeServiceCreate(BaseModel):
    name: str
    api_url: str
    api_key: str
    enabled: bool = True
    priority: int = 0
    # 默认上传配置
    proxy_ids: List[int] = []     # 代理 ID 列表（轮询分配）
    group_ids: List[int] = []
    concurrency: int = 3
    account_priority: int = 50
    rate_multiplier: float = 1.0
    auto_pause_on_expired: bool = True


class FluxCodeServiceUpdate(BaseModel):
    name: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    # 默认上传配置
    proxy_ids: Optional[List[int]] = None   # 代理 ID 列表（轮询分配）
    group_ids: Optional[List[int]] = None
    concurrency: Optional[int] = None
    account_priority: Optional[int] = None
    rate_multiplier: Optional[float] = None
    auto_pause_on_expired: Optional[bool] = None


class FluxCodeServiceResponse(BaseModel):
    id: int
    name: str
    api_url: str
    has_key: bool
    enabled: bool
    priority: int
    # 默认上传配置
    proxy_ids: List[int]          # 代理 ID 列表（轮询分配）
    group_ids: List[int]
    concurrency: int
    account_priority: int
    rate_multiplier: float
    auto_pause_on_expired: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class FluxCodeTestRequest(BaseModel):
    api_url: Optional[str] = None
    api_key: Optional[str] = None


class FluxCodeUploadRequest(BaseModel):
    account_ids: List[int]
    service_id: Optional[int] = None
    # 上传时可覆盖的配置（为 None 则用服务默认值）
    proxy_ids: Optional[List[int]] = None   # 代理 ID 列表（轮询分配）
    group_ids: Optional[List[int]] = None
    concurrency: Optional[int] = None
    account_priority: Optional[int] = None
    rate_multiplier: Optional[float] = None
    auto_pause_on_expired: Optional[bool] = None


def _parse_group_ids(raw: str) -> List[int]:
    """将数据库中的 group_ids JSON 字符串解析为整数列表"""
    try:
        return json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_proxy_ids(raw: str) -> List[int]:
    """将数据库中的 proxy_ids JSON 字符串解析为整数列表"""
    try:
        return json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []


def _to_response(svc) -> FluxCodeServiceResponse:
    return FluxCodeServiceResponse(
        id=svc.id,
        name=svc.name,
        api_url=svc.api_url,
        has_key=bool(svc.api_key),
        enabled=svc.enabled,
        priority=svc.priority,
        proxy_ids=_parse_proxy_ids(svc.proxy_ids),
        group_ids=_parse_group_ids(svc.group_ids),
        concurrency=svc.concurrency or 3,
        account_priority=svc.account_priority or 50,
        rate_multiplier=float(svc.rate_multiplier or 1.0),
        auto_pause_on_expired=svc.auto_pause_on_expired if svc.auto_pause_on_expired is not None else True,
        created_at=svc.created_at.isoformat() if svc.created_at else None,
        updated_at=svc.updated_at.isoformat() if svc.updated_at else None,
    )


# ============== API Endpoints ==============

@router.get("", response_model=List[FluxCodeServiceResponse])
async def list_fluxcode_services(enabled: Optional[bool] = None):
    """获取 FluxCode 服务列表"""
    with get_db() as db:
        services = crud.get_fluxcode_services(db, enabled=enabled)
        return [_to_response(s) for s in services]


@router.post("", response_model=FluxCodeServiceResponse)
async def create_fluxcode_service(request: FluxCodeServiceCreate):
    """新增 FluxCode 服务"""
    with get_db() as db:
        svc = crud.create_fluxcode_service(
            db,
            name=request.name,
            api_url=request.api_url,
            api_key=request.api_key,
            enabled=request.enabled,
            priority=request.priority,
            proxy_ids=json.dumps(request.proxy_ids),
            group_ids=json.dumps(request.group_ids),
            concurrency=request.concurrency,
            account_priority=request.account_priority,
            rate_multiplier=str(request.rate_multiplier),
            auto_pause_on_expired=request.auto_pause_on_expired,
        )
        return _to_response(svc)


@router.get("/{service_id}", response_model=FluxCodeServiceResponse)
async def get_fluxcode_service(service_id: int):
    """获取单个 FluxCode 服务详情"""
    with get_db() as db:
        svc = crud.get_fluxcode_service_by_id(db, service_id)
        if not svc:
            raise HTTPException(status_code=404, detail="FluxCode 服务不存在")
        return _to_response(svc)


@router.get("/{service_id}/full")
async def get_fluxcode_service_full(service_id: int):
    """获取 FluxCode 服务完整配置（含 API Key）"""
    with get_db() as db:
        svc = crud.get_fluxcode_service_by_id(db, service_id)
        if not svc:
            raise HTTPException(status_code=404, detail="FluxCode 服务不存在")
        return {
            "id": svc.id,
            "name": svc.name,
            "api_url": svc.api_url,
            "api_key": svc.api_key,
            "enabled": svc.enabled,
            "priority": svc.priority,
            "proxy_ids": _parse_proxy_ids(svc.proxy_ids),
            "group_ids": _parse_group_ids(svc.group_ids),
            "concurrency": svc.concurrency or 3,
            "account_priority": svc.account_priority or 50,
            "rate_multiplier": float(svc.rate_multiplier or 1.0),
            "auto_pause_on_expired": svc.auto_pause_on_expired if svc.auto_pause_on_expired is not None else True,
        }


@router.patch("/{service_id}", response_model=FluxCodeServiceResponse)
async def update_fluxcode_service(service_id: int, request: FluxCodeServiceUpdate):
    """更新 FluxCode 服务配置"""
    with get_db() as db:
        svc = crud.get_fluxcode_service_by_id(db, service_id)
        if not svc:
            raise HTTPException(status_code=404, detail="FluxCode 服务不存在")

        update_data = {}
        if request.name is not None:
            update_data["name"] = request.name
        if request.api_url is not None:
            update_data["api_url"] = request.api_url
        # api_key 留空则保持原值
        if request.api_key:
            update_data["api_key"] = request.api_key
        if request.enabled is not None:
            update_data["enabled"] = request.enabled
        if request.priority is not None:
            update_data["priority"] = request.priority
        if request.proxy_ids is not None:
            update_data["proxy_ids"] = json.dumps(request.proxy_ids)
        if request.group_ids is not None:
            update_data["group_ids"] = json.dumps(request.group_ids)
        if request.concurrency is not None:
            update_data["concurrency"] = request.concurrency
        if request.account_priority is not None:
            update_data["account_priority"] = request.account_priority
        if request.rate_multiplier is not None:
            update_data["rate_multiplier"] = str(request.rate_multiplier)
        if request.auto_pause_on_expired is not None:
            update_data["auto_pause_on_expired"] = request.auto_pause_on_expired

        svc = crud.update_fluxcode_service(db, service_id, **update_data)
        return _to_response(svc)


@router.delete("/{service_id}")
async def delete_fluxcode_service(service_id: int):
    """删除 FluxCode 服务"""
    with get_db() as db:
        svc = crud.get_fluxcode_service_by_id(db, service_id)
        if not svc:
            raise HTTPException(status_code=404, detail="FluxCode 服务不存在")
        crud.delete_fluxcode_service(db, service_id)
        return {"success": True, "message": f"FluxCode 服务 {svc.name} 已删除"}


@router.post("/{service_id}/test")
async def test_fluxcode_service(service_id: int):
    """测试 FluxCode 服务连接"""
    with get_db() as db:
        svc = crud.get_fluxcode_service_by_id(db, service_id)
        if not svc:
            raise HTTPException(status_code=404, detail="FluxCode 服务不存在")
        success, message = test_fluxcode_connection(svc.api_url, svc.api_key)
        return {"success": success, "message": message}


@router.post("/test-connection")
async def test_fluxcode_connection_direct(request: FluxCodeTestRequest):
    """直接测试 FluxCode 连接（用于添加前验证）"""
    if not request.api_url or not request.api_key:
        raise HTTPException(status_code=400, detail="api_url 和 api_key 不能为空")
    success, message = test_fluxcode_connection(request.api_url, request.api_key)
    return {"success": success, "message": message}


@router.post("/upload")
async def upload_accounts_to_fluxcode(request: FluxCodeUploadRequest):
    """批量上传账号到 FluxCode 平台（逐个创建方式）"""
    if not request.account_ids:
        raise HTTPException(status_code=400, detail="账号 ID 列表不能为空")

    with get_db() as db:
        # 获取服务配置
        if request.service_id:
            svc = crud.get_fluxcode_service_by_id(db, request.service_id)
        else:
            svcs = crud.get_fluxcode_services(db, enabled=True)
            svc = svcs[0] if svcs else None

        if not svc:
            raise HTTPException(status_code=400, detail="未找到可用的 FluxCode 服务")

        api_url = svc.api_url
        api_key = svc.api_key

        # 使用请求覆盖值 or 服务默认值
        proxy_ids = request.proxy_ids if request.proxy_ids is not None else _parse_proxy_ids(svc.proxy_ids)
        group_ids = request.group_ids if request.group_ids is not None else _parse_group_ids(svc.group_ids)
        concurrency = request.concurrency if request.concurrency is not None else (svc.concurrency or 3)
        account_priority = request.account_priority if request.account_priority is not None else (svc.account_priority or 50)
        rate_multiplier = request.rate_multiplier if request.rate_multiplier is not None else float(svc.rate_multiplier or 1.0)
        auto_pause_on_expired = (
            request.auto_pause_on_expired
            if request.auto_pause_on_expired is not None
            else (svc.auto_pause_on_expired if svc.auto_pause_on_expired is not None else True)
        )

    # 执行批量上传
    results = batch_upload_to_fluxcode(
        account_ids=request.account_ids,
        api_url=api_url,
        api_key=api_key,
        proxy_ids=proxy_ids,
        group_ids=group_ids,
        concurrency=concurrency,
        account_priority=account_priority,
        rate_multiplier=rate_multiplier,
        auto_pause_on_expired=auto_pause_on_expired,
    )
    return results
