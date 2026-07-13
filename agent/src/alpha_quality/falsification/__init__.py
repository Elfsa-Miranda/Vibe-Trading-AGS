"""Public, feature-gated falsification evidence surface."""

from src.alpha_quality.falsification.catalog import TestCapability
from src.alpha_quality.falsification.authority_v2 import (
    DatedObservationV2,
    FalsificationContractAuthorityV2,
    FalsificationContractV2,
    FalsificationExecutorV2,
    FalsificationSourceProducerV2,
    FalsificationTestCatalogV2,
    TestCapabilityV2,
    TestSpecV2,
    legacy_v1_view,
    render_falsification_narrative_v2,
    validate_contract_v2,
)
from src.alpha_quality.falsification.contract import FalsificationContract
from src.alpha_quality.falsification.executor import (
    FixedFamilyResult,
    FixedHorizonExecutor,
    FixedTestEvidence,
    execute_fixed_family,
)
from src.alpha_quality.falsification.mei import (
    MechanismEvidenceIndex,
    MechanismEvidenceRef,
    aggregate_mechanism_evidence,
)
from src.alpha_quality.falsification.policy import (
    APPROVED_SEQUENTIAL_METHOD,
    MECHANISM_EVIDENCE_POLICY_VERSION,
    SEQUENTIAL_STOPPING_RULE,
    sequential_method_availability,
)
from src.alpha_quality.falsification.regime import RegimeDefinition
from src.alpha_quality.falsification.sequential import (
    SequentialBlock,
    SequentialExecutor,
    SequentialLook,
    SequentialProtocol,
    SequentialState,
)
from src.alpha_quality.falsification.sequential_service import (
    SequentialFalsificationService,
)
from src.alpha_quality.falsification.service import FalsificationService
from src.alpha_quality.falsification.validator import validate_contract

__all__ = [
    "APPROVED_SEQUENTIAL_METHOD",
    "DatedObservationV2",
    "FalsificationContract",
    "FalsificationContractAuthorityV2",
    "FalsificationContractV2",
    "FalsificationExecutorV2",
    "FalsificationService",
    "FalsificationSourceProducerV2",
    "FalsificationTestCatalogV2",
    "FixedFamilyResult",
    "FixedHorizonExecutor",
    "FixedTestEvidence",
    "MECHANISM_EVIDENCE_POLICY_VERSION",
    "MechanismEvidenceIndex",
    "MechanismEvidenceRef",
    "RegimeDefinition",
    "SEQUENTIAL_STOPPING_RULE",
    "SequentialBlock",
    "SequentialExecutor",
    "SequentialFalsificationService",
    "SequentialLook",
    "SequentialProtocol",
    "SequentialState",
    "TestCapability",
    "TestCapabilityV2",
    "TestSpecV2",
    "aggregate_mechanism_evidence",
    "execute_fixed_family",
    "legacy_v1_view",
    "render_falsification_narrative_v2",
    "sequential_method_availability",
    "validate_contract",
    "validate_contract_v2",
]
