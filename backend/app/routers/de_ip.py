"""去 IP 化导出路由 — Sprint 2.E。

POST /api/projects/{project_id}/de_ip_dictionary     生成 / 重新生成字典(LLM 调用)
GET  /api/projects/{project_id}/de_ip_dictionary     拿当前字典(404 if 没生成过)
POST /api/simulations/{simulation_id}/export         导出 markdown(version: original | de_ip)
"""
from __future__ import annotations

import sqlite3
import traceback

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.de_ip import (
    DeIpDictionaryResponse,
    ExportSimulationRequest,
    ExportSimulationResponse,
)
from app.services.de_ip_service import (
    DictionaryNotFound,
    NoCharactersToReplace,
    SimulationNotExportable,
    export_simulation,
    generate_de_ip_dictionary,
    get_dictionary_or_404,
)
from app.services.project_service import ResourceNotFoundOrForbidden


router = APIRouter()


@router.post(
    "/projects/{project_id}/de_ip_dictionary",
    response_model=DeIpDictionaryResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_generate_de_ip_dictionary(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """生成 / 重新生成项目的去 IP 字典(LLM 调用)。

    重新生成会覆盖原字典(INSERT OR REPLACE);created_at 保留首次时间,updated_at 刷新。

    异常:
      404 — project 不属于用户
      422 NO_CHARACTERS_TO_REPLACE — 项目无角色,无名字可替换
      500 — LLM 调用失败 / JSON 解析失败
    """
    try:
        dictionary = generate_de_ip_dictionary(conn, project_id, user.id)
        return dictionary.to_response()
    except ResourceNotFoundOrForbidden:
        raise
    except NoCharactersToReplace as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "NO_CHARACTERS_TO_REPLACE", "message": str(e)},
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"生成字典失败:{type(e).__name__}: {e}"[:300],
            },
        )


@router.get(
    "/projects/{project_id}/de_ip_dictionary",
    response_model=DeIpDictionaryResponse,
)
def api_get_de_ip_dictionary(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """拿项目当前字典。无 → 404 DE_IP_DICTIONARY_NOT_FOUND(前端提示先生成)。"""
    try:
        dictionary = get_dictionary_or_404(conn, project_id, user.id)
        return dictionary.to_response()
    except ResourceNotFoundOrForbidden:
        raise
    except DictionaryNotFound as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "DE_IP_DICTIONARY_NOT_FOUND", "message": str(e)},
        )


@router.post(
    "/simulations/{simulation_id}/export",
    response_model=ExportSimulationResponse,
)
def api_export_simulation(
    simulation_id: str,
    req: ExportSimulationRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """导出推演 markdown(version='original' / 'de_ip')。

    de_ip 版需先生成字典(POST /projects/{id}/de_ip_dictionary)。

    异常:
      404 — sim 不属于用户
      422 SIMULATION_NOT_EXPORTABLE   sim 非 done / narrative 空
      422 NO_DE_IP_DICTIONARY         version='de_ip' 但项目无字典
    """
    try:
        return export_simulation(conn, simulation_id, user.id, req.version)
    except ResourceNotFoundOrForbidden:
        raise
    except SimulationNotExportable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "SIMULATION_NOT_EXPORTABLE", "message": str(e)},
        )
    except DictionaryNotFound as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "NO_DE_IP_DICTIONARY", "message": str(e)},
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"导出失败:{type(e).__name__}: {e}"[:300],
            },
        )
