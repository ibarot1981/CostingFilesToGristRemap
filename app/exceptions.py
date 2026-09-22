"""Application-specific exceptions."""


class CostingAppError(Exception):
    """Base exception for expected application errors."""


class ConfigError(CostingAppError):
    """Raised when YAML or environment configuration is invalid."""


class WorkbookError(CostingAppError):
    """Raised when an ODS workbook cannot be read or processed."""


class CatalogImportError(CostingAppError):
    """Raised when the canonical identity catalog cannot be interpreted."""


class GristError(CostingAppError):
    """Raised when the Grist API cannot be queried successfully."""


class GristPermissionError(GristError):
    """The authenticated Grist user cannot perform the requested operation."""


class GristConnectivityError(GristError):
    """Grist could not be reached or returned an unusable response."""


class GristDuplicateNameError(GristError):
    """The selected workspace contains multiple exact-name documents."""


class GristValidationError(GristError):
    """A Grist document/workspace failed an identity or safety check."""


class GristWorkspaceSelectionRequired(GristValidationError):
    """More than one writable workspace is available and none was selected."""
