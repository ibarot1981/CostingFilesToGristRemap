"""Application-specific exceptions."""


class CostingAppError(Exception):
    """Base exception for expected application errors."""


class ConfigError(CostingAppError):
    """Raised when YAML or environment configuration is invalid."""


class WorkbookError(CostingAppError):
    """Raised when an ODS workbook cannot be read or processed."""


class GristError(CostingAppError):
    """Raised when the Grist API cannot be queried successfully."""
