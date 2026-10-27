"""EDGE: app factory, envelope, errors, workspace gate, route assembly, OpenAPI export (ARCHITECTURE.md §2.2).

Nothing imports `api` (§2.4 rule 6). Built in S0046: the skeleton, not yet served (BL-0105).
"""

from app.api.app import create_app
from app.api.container import ApiContainer, ContainerFactory
from app.api.contract import export_openapi

__all__ = ["ApiContainer", "ContainerFactory", "create_app", "export_openapi"]
