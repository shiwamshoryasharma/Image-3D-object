"""FastAPI dependencies of the part-aware routers (part-aware DESIGN.md section 6).

Authentication uses the existing guards: ``AuthGuard`` (J, Bearer header only) for JSON routes and
``FileAuthGuard`` (F, header or ``?token=``) for every file route (``api.deps``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import ContainerDep
from core.feature_errors import FeatureDisabledError
from core.part_aware_container import PartAwareContainer
from services.feature_service import FeatureService
from services.image_generation_service import ImageGenerationService
from services.model_registry_service import ModelRegistryService
from services.part_analysis_service import PartAnalysisService
from services.part_files import PartFiles
from services.segmentation_service import SegmentationService
from services.workflow_service import WorkflowService


def get_part_aware(container: ContainerDep) -> PartAwareContainer:
    part_aware = container.part_aware
    if part_aware is None:
        raise FeatureDisabledError(
            "The part-aware layer is not configured", feature="part_aware", flag="IMG3D_PART_AWARE_ENABLED"
        )
    return part_aware


PartAwareDep = Annotated[PartAwareContainer, Depends(get_part_aware)]


def get_feature_service(part_aware: PartAwareDep) -> FeatureService:
    return part_aware.feature_service


def get_model_registry_service(part_aware: PartAwareDep) -> ModelRegistryService:
    return part_aware.model_registry_service


def get_image_generation_service(part_aware: PartAwareDep) -> ImageGenerationService:
    return part_aware.image_generation_service


def get_segmentation_service(part_aware: PartAwareDep) -> SegmentationService:
    return part_aware.segmentation_service


def get_part_analysis_service(part_aware: PartAwareDep) -> PartAnalysisService:
    return part_aware.part_analysis_service


def get_part_files(part_aware: PartAwareDep) -> PartFiles:
    return part_aware.part_files


def get_workflow_service(part_aware: PartAwareDep) -> WorkflowService:
    return part_aware.workflow_service


FeatureServiceDep = Annotated[FeatureService, Depends(get_feature_service)]
ModelRegistryServiceDep = Annotated[ModelRegistryService, Depends(get_model_registry_service)]
ImageGenerationServiceDep = Annotated[ImageGenerationService, Depends(get_image_generation_service)]
SegmentationServiceDep = Annotated[SegmentationService, Depends(get_segmentation_service)]
PartAnalysisServiceDep = Annotated[PartAnalysisService, Depends(get_part_analysis_service)]
PartFilesDep = Annotated[PartFiles, Depends(get_part_files)]
WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]
