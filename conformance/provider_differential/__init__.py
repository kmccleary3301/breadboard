"""F6 provider semantic differential contracts and evidence gates."""

from .contracts import ALL_ROW_IDS, ORACLE_IDENTITY, ContractError
from .gate import DifferentialError, Evaluation, evaluate, load_matrix, load_oracle

__all__ = [
    "ALL_ROW_IDS",
    "ContractError",
    "DifferentialError",
    "Evaluation",
    "ORACLE_IDENTITY",
    "evaluate",
    "load_matrix",
    "load_oracle",
]
