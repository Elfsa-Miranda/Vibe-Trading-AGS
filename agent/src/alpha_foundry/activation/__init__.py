"""Independent, falsification-first topology retriever activation."""

from src.alpha_foundry.activation.analysis import ActivationAnalyzer, holm_adjust
from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
from src.alpha_foundry.activation.capability import (
    ActivationCompatibility,
    ActiveRetrieverCapability,
    ActiveRetrieverResolver,
    RetrieverModeResolution,
)
from src.alpha_foundry.activation.candidate_factory_v1 import (
    ProductionActivationCandidateFactoryV1,
    ProductionActivationCandidateRefsV1,
    ProductionActivationFactoryArmRequestV1,
    ProductionActivationFactoryArmResultV1,
)
from src.alpha_foundry.activation.coordinator_v2 import ActivationPairCoordinatorV2
from src.alpha_foundry.activation.governance_v2 import (
    ActivationGovernanceDecisionV2,
    ActivationGovernanceService,
    ActivationReadinessServiceV4,
    ActivationReadinessV4,
)
from src.alpha_foundry.activation.pair_projector_v2 import (
    ActivationArmEventRefsV2,
    ActivationEvidenceProjector,
    ActivationPairEvidenceV2,
)
from src.alpha_foundry.activation.statistical_v2 import (
    ActivationStatisticalAnalysisV2,
    ActivationStatisticalAnalyzerV2,
    PilotDispersionEvidenceV2,
    PreregisteredConfirmatoryActivationPlanV2,
)
from src.alpha_foundry.activation.model import (
    ActivationAnalysisPolicy,
    ActivationDesign,
    ActivationExperimentPlan,
    ActivationExperimentResult,
    ActivationProvenance,
    ActivationRunManifest,
    PairedEffect,
    RetrieverActivationDecision,
)
from src.alpha_foundry.activation.policy import RetrieverActivationPolicy
from src.alpha_foundry.activation.runner import (
    ActivationArmRequest,
    PairedActivationRunner,
    RegisteredActivationPlan,
    TrainValidActivationScope,
    activation_arm_execution_run_id,
)
from src.alpha_foundry.activation.run_source_v2 import (
    ActivationRunSourceAuditV2,
    ActivationRunSourceAuditorV2,
)
from src.alpha_foundry.activation.resource_v1 import (
    ActivationResourceEvidenceV1,
    MeasuredActivationPairV1,
)
from src.alpha_foundry.activation.service import ActivationEvidenceService

__all__ = [
    "ActivationAnalysisPolicy", "ActivationAnalyzer", "ActivationArmRequest",
    "ActivationArtifactStore", "ActivationEvidenceService",
    "ActivationCompatibility", "ActivationDesign", "ActivationExperimentPlan",
    "ActivationExperimentResult", "ActivationProvenance", "ActivationRunManifest",
    "ActivationRunSourceAuditV2", "ActivationRunSourceAuditorV2",
    "ActivationResourceEvidenceV1", "MeasuredActivationPairV1",
    "ActiveRetrieverCapability", "ActiveRetrieverResolver", "PairedEffect",
    "PairedActivationRunner", "RegisteredActivationPlan", "RetrieverActivationDecision",
    "ProductionActivationCandidateFactoryV1", "ProductionActivationCandidateRefsV1",
    "ProductionActivationFactoryArmRequestV1", "ProductionActivationFactoryArmResultV1",
    "ActivationArmEventRefsV2", "ActivationEvidenceProjector",
    "ActivationPairCoordinatorV2", "ActivationPairEvidenceV2",
    "ActivationReadinessServiceV4", "ActivationReadinessV4",
    "ActivationStatisticalAnalysisV2", "ActivationStatisticalAnalyzerV2",
    "ActivationGovernanceDecisionV2", "ActivationGovernanceService",
    "PilotDispersionEvidenceV2", "PreregisteredConfirmatoryActivationPlanV2",
    "RetrieverActivationPolicy", "RetrieverModeResolution", "TrainValidActivationScope",
    "holm_adjust",
    "activation_arm_execution_run_id",
]
