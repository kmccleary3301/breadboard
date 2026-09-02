"""BreadBoard provider credential broker and SQLite store."""

from .authority import CredentialAuthority, CredentialOrigin, CredentialSelector
from .broker import (
    AUTH_SOURCE_PRECEDENCE,
    REMOTE_BROKER_URL_ENV,
    BrokerProblem,
    CredentialAuditPersistenceError,
    ProviderBroker,
    ProviderBrokerConfigurationError,
    get_provider_broker,
    provider_broker,
)
from .catalog import (
    OAuthFlowSpec,
    ProviderCatalogEntry,
    get_provider_catalog_entry,
    get_provider_catalog_entry_for_adapter,
    product_provider_catalog,
    provider_catalog,
    routable_provider_catalog,
)
from .oauth import OAuthFlowAdapter, OAuthFlowError
from .store import SQLiteCredentialStore, default_store_path
__all__ = [
    "AUTH_SOURCE_PRECEDENCE",
    "BrokerProblem",
    "CredentialAuditPersistenceError",
    "CredentialOrigin",
    "CredentialAuthority",
    "CredentialSelector",
    "OAuthFlowAdapter",
    "OAuthFlowError",
    "OAuthFlowSpec",
    "ProviderBroker",
    "ProviderBrokerConfigurationError",
    "REMOTE_BROKER_URL_ENV",
    "ProviderCatalogEntry",
    "SQLiteCredentialStore",
    "default_store_path",
    "get_provider_broker",
    "get_provider_catalog_entry",
    "get_provider_catalog_entry_for_adapter",
    "product_provider_catalog",
    "provider_broker",
    "provider_catalog",
    "routable_provider_catalog",
]
