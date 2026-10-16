"""FastAPI dependencies: container/service access and authentication guards."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from core.container import ServiceContainer
from core.security import TokenClaims
from services.auth_service import AuthService
from services.blender_runtime_service import BlenderRuntimeService
from services.job_service import JobService
from services.mesh_tuner_service import MeshTunerService
from services.reference_service import ReferenceService
from services.result_service import ResultService
from services.system_service import SystemService


def get_container(connection: HTTPConnection) -> ServiceContainer:
    return connection.app.state.container


ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def get_auth_service(container: ContainerDep) -> AuthService:
    return container.auth_service


def get_reference_service(container: ContainerDep) -> ReferenceService:
    return container.reference_service


def get_job_service(container: ContainerDep) -> JobService:
    return container.job_service


def get_result_service(container: ContainerDep) -> ResultService:
    return container.result_service


def get_system_service(container: ContainerDep) -> SystemService:
    return container.system_service


def get_blender_runtime(container: ContainerDep) -> BlenderRuntimeService:
    return container.blender_runtime


def get_mesh_tuner_service(container: ContainerDep) -> MeshTunerService:
    return container.mesh_tuner_service


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
ReferenceServiceDep = Annotated[ReferenceService, Depends(get_reference_service)]
JobServiceDep = Annotated[JobService, Depends(get_job_service)]
ResultServiceDep = Annotated[ResultService, Depends(get_result_service)]
SystemServiceDep = Annotated[SystemService, Depends(get_system_service)]
BlenderRuntimeDep = Annotated[BlenderRuntimeService, Depends(get_blender_runtime)]
MeshTunerServiceDep = Annotated[MeshTunerService, Depends(get_mesh_tuner_service)]


# --------------------------------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------------------------------


def bearer_token(connection: HTTPConnection) -> str | None:
    """Token from ``Authorization: Bearer <token>`` (``None`` if absent/malformed)."""
    header = connection.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def request_token(connection: HTTPConnection, *, allow_query: bool) -> str | None:
    token = bearer_token(connection)
    if token is None and allow_query:
        token = connection.query_params.get("token") or None
    return token


def require_auth(connection: HTTPConnection) -> TokenClaims | None:
    """Guard for JSON endpoints: ``Authorization: Bearer`` header only."""
    container = get_container(connection)
    return container.auth_service.authorize(request_token(connection, allow_query=False))


def require_auth_allow_query(connection: HTTPConnection) -> TokenClaims | None:
    """Guard for GET file endpoints (``<img>``, GLTFLoader): header or ``?token=``."""
    container = get_container(connection)
    return container.auth_service.authorize(request_token(connection, allow_query=True))


AuthGuard = Depends(require_auth)
FileAuthGuard = Depends(require_auth_allow_query)
