"""Current project-contract policy shared by runtime test fixtures."""

from pathlib import Path

from tools import build_distributions


REPO = Path(__file__).resolve().parents[2]
CURRENT_PROJECT_CONTRACT_VERSION = (
    build_distributions.current_project_contract_version(REPO)
)
