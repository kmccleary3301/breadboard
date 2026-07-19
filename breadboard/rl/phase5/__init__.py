from .f1_preflight import F1ValidationError, promote, validate_scratch, verify_canonical
from .f2_terminal import (
    F2ValidationError,
    build_f2_artifact_graph,
    export_f2_artifacts_from_raw,
    promote as promote_f2,
    validate_scratch as validate_f2_scratch,
    verify_canonical as verify_f2_canonical,
)

__all__ = (
    "F1ValidationError",
    "promote",
    "validate_scratch",
    "verify_canonical",
    "F2ValidationError",
    "build_f2_artifact_graph",
    "export_f2_artifacts_from_raw",
    "promote_f2",
    "validate_f2_scratch",
    "verify_f2_canonical",
    "FixedPolicyAuthorityRefs",
    "F2RuntimeError",
    "MaterializedTerminalPackage",
    "OuterBridgePlanInputs",
    "PreboundServiceSocketPlanInputs",
    "OperatorAuthorityInputs",
    "TerminalPackageInputs",
    "materialize_terminal_package",
    "stock_docker_blocker",
    "write_terminal_package",
    "F2AuthorityAuthoringError",
    "F2C4SemanticInput",
    "F2C4StaticAuthorityInput",
    "F2C4DynamicAuthorityInput",
    "F2C4StaticAuthorityFragment",
    "F2C4TargetDynamicPlanInput",
    "F2C4TargetDynamicObservations",
    "author_f2_target_dynamic_authority",
    "CallbackObservationSigningKeyHandoffV1",
    "EvidenceReceiptSigningKeyRuntimeHandoffV1",
    "TlsPrivateKeyRuntimeHandoffV1",
    "TlsCallbackSocketRuntimeHandoffV1",
    "TlsCallbackRuntimeInputV1",
    "TlsCallbackLiveHandoffV1",
    "build_f2_c4_static_authority",
    "author_f2_operator_input",
    "materialize_f2_c4_semantic_input",
    "verify_f2_operator_input",
)

_RUNTIME_EXPORTS = frozenset({
    "FixedPolicyAuthorityRefs", "F2RuntimeError", "MaterializedTerminalPackage",
    "OperatorAuthorityInputs", "OuterBridgePlanInputs", "PreboundServiceSocketPlanInputs",
    "TerminalPackageInputs",
    "materialize_terminal_package", "stock_docker_blocker", "write_terminal_package",
})
_AUTHORING_EXPORTS = frozenset({
    "F2AuthorityAuthoringError", "F2C4SemanticInput",
    "F2C4StaticAuthorityInput", "F2C4DynamicAuthorityInput",
    "F2C4StaticAuthorityFragment", "build_f2_c4_static_authority",
    "F2C4TargetDynamicPlanInput", "F2C4TargetDynamicObservations",
    "author_f2_target_dynamic_authority",
    "CallbackObservationSigningKeyHandoffV1",
    "EvidenceReceiptSigningKeyRuntimeHandoffV1",
    "TlsPrivateKeyRuntimeHandoffV1", "TlsCallbackSocketRuntimeHandoffV1",
    "TlsCallbackRuntimeInputV1",
    "TlsCallbackLiveHandoffV1",
    "author_f2_operator_input", "verify_f2_operator_input",
    "materialize_f2_c4_semantic_input",
})


def __getattr__(name: str):
    if name in _AUTHORING_EXPORTS:
        from . import f2_authority_authoring
        return getattr(f2_authority_authoring, name)
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(name)
    from . import f2_runtime
    return getattr(f2_runtime, name)
