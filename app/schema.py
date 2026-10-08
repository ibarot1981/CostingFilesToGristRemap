"""Versioned Safari Manufacturing foundation schema and safe diff/apply."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.exceptions import GristValidationError
from app.grist_admin import GristAdminClient, SAFARI_DOCUMENT_NAME, validate_safari_document


SCHEMA_VERSION = "safari-part-intended-sharing-grist-2026-10-08.v10"
FOUNDATION_TABLES: tuple[dict[str, Any], ...] = (
    {"id": "Product", "name": "Product", "columns": [{"id": "Name", "type": "Text"}, {"id": "SourceFile", "type": "Text"}, {"id": "SourceRow", "type": "Numeric"}, {"id": "Active", "type": "Bool"}]},
    {"id": "ProductModel", "name": "Product Model", "columns": [{"id": "Product", "type": "Ref:Product"}, {"id": "ModelNumber", "type": "Text"}, {"id": "Name", "type": "Text"}, {"id": "LegacySparesOnly", "type": "Bool"}, {"id": "SourceFile", "type": "Text"}, {"id": "SourceRow", "type": "Numeric"}, {"id": "Active", "type": "Bool"}, {"id": "SupersededById", "type": "Text"}, {"id": "SupersededAt", "type": "DateTime"}]},
    {"id": "ProductModelCode", "name": "Product Model Code", "columns": [{"id": "ProductModel", "type": "Ref:ProductModel"}, {"id": "Code", "type": "Text"}, {"id": "Description", "type": "Text"}, {"id": "LegacySparesOnly", "type": "Bool"}, {"id": "SourceValues", "type": "Any"}, {"id": "SourceFile", "type": "Text"}, {"id": "SourceRow", "type": "Numeric"}, {"id": "Active", "type": "Bool"}]},
    {"id": "IdentityAlias", "name": "Identity Alias", "columns": [{"id": "EntityType", "type": "Text"}, {"id": "EntityId", "type": "Text"}, {"id": "Value", "type": "Text"}, {"id": "NormalizedValue", "type": "Text"}, {"id": "Source", "type": "Text"}, {"id": "SourceRow", "type": "Numeric"}]},
    {"id": "CostingFile", "name": "Costing File", "columns": [{"id": "RelativePath", "type": "Text"}, {"id": "NormalizedPath", "type": "Text"}, {"id": "Name", "type": "Text"}, {"id": "Extension", "type": "Text"}, {"id": "SizeBytes", "type": "Numeric"}, {"id": "ModifiedAt", "type": "DateTime"}, {"id": "FileHash", "type": "Text"}, {"id": "Product", "type": "Ref:Product"}, {"id": "CandidateClassification", "type": "Text"}, {"id": "MappingStatus", "type": "Text"}]},
    {"id": "FileObservation", "name": "File Observation", "columns": [{"id": "CostingFile", "type": "Ref:CostingFile"}, {"id": "ObservedAt", "type": "DateTime"}, {"id": "RelativePath", "type": "Text"}, {"id": "NormalizedPath", "type": "Text"}, {"id": "SizeBytes", "type": "Numeric"}, {"id": "ModifiedAt", "type": "DateTime"}, {"id": "FileHash", "type": "Text"}, {"id": "Readable", "type": "Bool"}, {"id": "SheetCount", "type": "Numeric"}, {"id": "ExternalReferenceCount", "type": "Numeric"}, {"id": "ParseError", "type": "Text"}]},
    {"id": "FileModelAssociation", "name": "File Model Association", "columns": [{"id": "CostingFile", "type": "Ref:CostingFile"}, {"id": "Product", "type": "Ref:Product"}, {"id": "ProductModel", "type": "Ref:ProductModel"}, {"id": "Actor", "type": "Text"}, {"id": "Reason", "type": "Text"}, {"id": "CreatedAt", "type": "DateTime"}, {"id": "SupersededAt", "type": "DateTime"}, {"id": "Active", "type": "Bool"}, {"id": "Version", "type": "Numeric"}, {"id": "RequestKey", "type": "Text"}, {"id": "RequestFingerprint", "type": "Text"}]},
    {"id": "FileCodeAssociation", "name": "File Code Association", "columns": [{"id": "Association", "type": "Ref:FileModelAssociation"}, {"id": "CostingFile", "type": "Ref:CostingFile"}, {"id": "ProductModelCode", "type": "Ref:ProductModelCode"}, {"id": "Actor", "type": "Text"}, {"id": "CreatedAt", "type": "DateTime"}, {"id": "SupersededAt", "type": "DateTime"}, {"id": "Active", "type": "Bool"}]},
    {"id": "ImportBatch", "name": "Import Batch", "columns": [{"id": "SourceFile", "type": "Text"}, {"id": "SourceHash", "type": "Text"}, {"id": "ParserVersion", "type": "Text"}, {"id": "StartedAt", "type": "DateTime"}, {"id": "CompletedAt", "type": "DateTime"}, {"id": "Status", "type": "Text"}, {"id": "Outcome", "type": "Text"}, {"id": "RequestKey", "type": "Text"}, {"id": "RequestFingerprint", "type": "Text"}]},
    {"id": "ReconciliationIssue", "name": "Reconciliation Issue", "columns": [{"id": "IssueType", "type": "Text"}, {"id": "Severity", "type": "Text"}, {"id": "Message", "type": "Text"}, {"id": "SourceFile", "type": "Text"}, {"id": "SourceRow", "type": "Numeric"}, {"id": "EntityId", "type": "Text"}, {"id": "Status", "type": "Text"}, {"id": "CreatedAt", "type": "DateTime"}, {"id": "Fingerprint", "type": "Text"}, {"id": "EntityType", "type": "Text"}, {"id": "CostingFile", "type": "Ref:CostingFile"}, {"id": "SourcePath", "type": "Text"}, {"id": "SourceCell", "type": "Text"}, {"id": "DetectedFacts", "type": "Any"}, {"id": "ProposedResolution", "type": "Any"}, {"id": "AssignedOwner", "type": "Text"}, {"id": "FirstSeenAt", "type": "DateTime"}, {"id": "LastSeenAt", "type": "DateTime"}, {"id": "Version", "type": "Numeric"}, {"id": "ResolutionAction", "type": "Text"}, {"id": "ResolutionReason", "type": "Text"}, {"id": "ResolvedActor", "type": "Text"}, {"id": "ResolvedAt", "type": "DateTime"}, {"id": "DeferredActor", "type": "Text"}, {"id": "DeferredAt", "type": "DateTime"}, {"id": "ReopenedActor", "type": "Text"}, {"id": "ReopenedAt", "type": "DateTime"}]},
    {"id": "DirectoryProductMapping", "name": "Directory Product Mapping", "columns": [{"id": "RelativePath", "type": "Text"}, {"id": "NormalizedPath", "type": "Text"}, {"id": "Product", "type": "Ref:Product"}, {"id": "Inherit", "type": "Bool"}, {"id": "Status", "type": "Text"}, {"id": "Proposer", "type": "Text"}, {"id": "Approver", "type": "Text"}, {"id": "Reason", "type": "Text"}, {"id": "CreatedAt", "type": "DateTime"}, {"id": "UpdatedAt", "type": "DateTime"}, {"id": "Version", "type": "Numeric"}, {"id": "SupersedesId", "type": "Text"}, {"id": "RequestKey", "type": "Text"}, {"id": "RequestFingerprint", "type": "Text"}]},
    {"id": "CostingSnapshot", "name": "Costing Snapshot", "columns": [{"id": "SnapshotKey", "type": "Text"}, {"id": "CostingFile", "type": "Ref:CostingFile"}, {"id": "ObservedAt", "type": "DateTime"}, {"id": "SemanticHash", "type": "Text"}, {"id": "SemanticContent", "type": "Any"}, {"id": "SourceHashes", "type": "Any"}, {"id": "Status", "type": "Text"}, {"id": "PreviousSnapshotKey", "type": "Text"}, {"id": "AcceptedAt", "type": "DateTime"}, {"id": "AcceptedBy", "type": "Text"}, {"id": "AcceptanceReason", "type": "Text"}, {"id": "RequestKey", "type": "Text"}]},
    {"id": "CostingChangeSetItem", "name": "Costing Change Set Item", "columns": [{"id": "ItemKey", "type": "Text"}, {"id": "ChangeKey", "type": "Text"}, {"id": "Snapshot", "type": "Ref:CostingSnapshot"}, {"id": "PreviousSnapshotKey", "type": "Text"}, {"id": "CostingFile", "type": "Ref:CostingFile"}, {"id": "ChangeType", "type": "Text"}, {"id": "Classification", "type": "Text"}, {"id": "ChangeData", "type": "Any"}, {"id": "PreviousState", "type": "Any"}, {"id": "CurrentState", "type": "Any"}, {"id": "SourceEvidence", "type": "Any"}, {"id": "CostImpact", "type": "Numeric"}, {"id": "CRReference", "type": "Text"}, {"id": "Status", "type": "Text"}, {"id": "AcceptedAt", "type": "DateTime"}, {"id": "AcceptedBy", "type": "Text"}, {"id": "AcceptanceReason", "type": "Text"}, {"id": "RequestKey", "type": "Text"}]},
    {"id": "AuditEvent", "name": "Audit Event", "columns": [{"id": "EventType", "type": "Text"}, {"id": "Actor", "type": "Text"}, {"id": "OccurredAt", "type": "DateTime"}, {"id": "EntityType", "type": "Text"}, {"id": "EntityId", "type": "Text"}, {"id": "Reason", "type": "Text"}, {"id": "Payload", "type": "Any"}, {"id": "RequestKey", "type": "Text"}, {"id": "RequestFingerprint", "type": "Text"}]},
)

# Milestone 3 is appended so an explicit schema plan shows only the new tables
# against a validated v4 Safari document. Startup never calls apply_schema.
def _table(table_id: str, columns: dict[str, str]) -> dict[str, Any]:
    return {"id": table_id, "name": table_id, "columns": [{"id": key, "type": value} for key, value in columns.items()]}


FOUNDATION_TABLES += (
    _table("FileProcessingEvent", {"EventKey": "Text", "FileKey": "Text", "Version": "Numeric", "FromState": "Text", "State": "Text", "SourceHash": "Text", "AssociationKey": "Text", "AssociationVersion": "Numeric", "Actor": "Text", "Reason": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("ProductPart", {"PartKey": "Text", "DisplayName": "Text", "Status": "Text", "NameKey": "Text", "CreatedActor": "Text", "CreatedReason": "Text", "CreatedAt": "DateTime", "CreateRequestKey": "Text", "CreateFingerprint": "Text"}),
    _table("PartMappingReview", {"ReviewKey": "Text", "FileKey": "Text", "SourceHash": "Text", "AssociationKey": "Text", "AssociationVersion": "Numeric", "GroupKey": "Text", "SheetName": "Text", "SourceRow": "Numeric", "SourceDescription": "Text", "ProductPart": "Ref:ProductPart", "Version": "Numeric", "Actor": "Text", "Reason": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text", "RequestRowCount": "Numeric"}),
    _table("PartRevision", {"RevisionKey": "Text", "ProductPart": "Ref:ProductPart", "Revision": "Numeric", "Status": "Text", "Snapshot": "Ref:CostingSnapshot"}),
    _table("PartComponentRevision", {"ComponentKey": "Text", "ParentRevision": "Ref:PartRevision", "ChildRevision": "Ref:PartRevision", "Quantity": "Numeric", "Status": "Text"}),
    _table("Material", {"MaterialKey": "Text", "CanonicalName": "Text", "ODSDisplayName": "Text", "MappingStatus": "Text"}),
    _table("PurchaseItem", {"ItemKey": "Text", "DisplayName": "Text", "Status": "Text"}),
    _table("ProcessOperation", {"OperationKey": "Text", "Name": "Text"}),
    _table("WorkCenter", {"CenterKey": "Text", "Name": "Text", "Status": "Text"}),
    _table("LineMaster", {"LineKey": "Text", "ProductPart": "Ref:ProductPart", "ProcessType": "Text", "Identity": "Any", "Status": "Text", "SourcePartName": "Text"}),
    _table("LineRevision", {"RevisionKey": "Text", "LineMaster": "Ref:LineMaster", "PreviousRevision": "Ref:LineRevision", "PhysicalSignature": "Text", "Status": "Text", "Snapshot": "Ref:CostingSnapshot", "Actor": "Text", "Reason": "Text"}),
    _table("LineDetail", {"DetailKey": "Text", "LineRevision": "Ref:LineRevision", "ProcessType": "Text", "Material": "Ref:Material", "PurchaseItem": "Ref:PurchaseItem", "ProcessOperation": "Ref:ProcessOperation", "WorkCenter": "Ref:WorkCenter", "SourceDepartment": "Text", "IssueRoute": "Text", "ActivityKind": "Text", "Quantity": "Numeric", "QuantityUOM": "Text", "DimensionMM": "Numeric", "DimensionInches": "Numeric", "WeightGrams": "Numeric", "WeightKg": "Numeric", "PartWeightKg": "Numeric", "Length": "Numeric", "Width": "Numeric", "Thickness": "Numeric", "IssueSlipNo": "Text", "IssueSlipDesc": "Text", "InternalMakingCost": "Numeric", "ExternalMachiningCost": "Numeric", "RateCached": "Numeric", "CostCached": "Numeric", "ItemName": "Text", "MaterialDisplayName": "Text", "OptionGroup": "Text"}),
    _table("SourceLineObservation", {"ObservationKey": "Text", "Snapshot": "Ref:CostingSnapshot", "SourceHash": "Text", "DependencyHashes": "Any", "ParserVersion": "Text", "SheetName": "Text", "SourceRow": "Numeric", "Status": "Text", "Cells": "Any", "ObservedFields": "Any", "PartDisplayName": "Text", "CachedCost": "Numeric", "CurrentCost": "Numeric", "CRReference": "Text"}),
    _table("SourceLineMapping", {"MappingKey": "Text", "Observation": "Ref:SourceLineObservation", "LineMaster": "Ref:LineMaster", "Status": "Text"}),
    _table("LineAuditItem", {"AuditKey": "Text", "LineMaster": "Ref:LineMaster", "LineRevision": "Ref:LineRevision", "PreviousRevision": "Ref:LineRevision", "Observation": "Ref:SourceLineObservation", "Actor": "Text", "Reason": "Text", "CRReference": "Text"}),
    _table("PartMetadataVersion", {"MetadataKey": "Text", "ProductPart": "Ref:ProductPart", "Version": "Numeric", "ScopeType": "Text", "ScopeProduct": "Ref:Product", "ScopeProductModel": "Ref:ProductModel", "ScopeModelCode": "Ref:ProductModelCode", "ScopeTargetId": "Text", "ScopeTargetLabel": "Text", "Shortcode": "Text", "Description": "Text", "DesignVariant": "Text", "DisplayName": "Text", "NameKey": "Text", "Actor": "Text", "Reason": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("PartNameAlias", {"AliasKey": "Text", "ProductPart": "Ref:ProductPart", "MetadataVersion": "Ref:PartMetadataVersion", "DisplayName": "Text", "NameKey": "Text", "IsCurrent": "Bool", "CreatedAt": "DateTime", "Actor": "Text", "Reason": "Text", "RequestKey": "Text"}),
    _table("PartScopeShortcode", {"ScopeKey": "Text", "ScopeType": "Text", "ScopeProduct": "Ref:Product", "ScopeProductModel": "Ref:ProductModel", "ScopeModelCode": "Ref:ProductModelCode", "ScopeTargetId": "Text", "ScopeTargetLabel": "Text", "Shortcode": "Text", "NormalizedShortcode": "Text", "Version": "Numeric", "UpdatedAt": "DateTime", "Actor": "Text", "Reason": "Text", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("PartShortcodeHistory", {"HistoryKey": "Text", "PartScopeShortcode": "Ref:PartScopeShortcode", "Version": "Numeric", "OldShortcode": "Text", "NewShortcode": "Text", "Actor": "Text", "Reason": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("PartRevisionLine", {"RevisionLineKey": "Text", "PartRevision": "Ref:PartRevision", "LineMaster": "Ref:LineMaster", "LineRevision": "Ref:LineRevision", "SourceLineObservation": "Ref:SourceLineObservation", "ProcessType": "Text", "QuantityPerPart": "Numeric", "QuantityUOM": "Text", "Status": "Text", "Actor": "Text", "Reason": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("PartDrawing", {"DrawingKey": "Text", "PartRevision": "Ref:PartRevision", "DrawingIdentity": "Text", "LinkType": "Text", "FilePath": "Text", "ExternalURL": "Text", "FileVersion": "Text", "ContentHash": "Text", "Status": "Text", "Actor": "Text", "Reason": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("Vendor", {"VendorKey": "Text", "DisplayName": "Text", "NameKey": "Text", "Status": "Text", "CreatedAt": "DateTime", "Actor": "Text", "Reason": "Text", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("PartPurchaseSpecification", {"SpecificationKey": "Text", "ProductPart": "Ref:ProductPart", "PartRevision": "Ref:PartRevision", "PurchaseItem": "Ref:PurchaseItem", "SpecificationCode": "Text", "Manufacturer": "Text", "ManufacturerPartNumber": "Text", "Description": "Text", "Attributes": "Any", "CostingUOM": "Text", "CostingCurrency": "Text", "Status": "Text", "Actor": "Text", "Reason": "Text", "CreatedAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("VendorPartMapping", {"VendorPartMappingKey": "Text", "Vendor": "Ref:Vendor", "PartPurchaseSpecification": "Ref:PartPurchaseSpecification", "VendorSKU": "Text", "NormalizedSKU": "Text", "VendorDescription": "Text", "Status": "Text", "ReviewedAt": "DateTime", "ReviewedBy": "Text", "ReviewReason": "Text", "Actor": "Text", "Reason": "Text", "CreatedAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("PartPurchaseUnitConversion", {"ConversionKey": "Text", "PartPurchaseSpecification": "Ref:PartPurchaseSpecification", "FromUOM": "Text", "ToUOM": "Text", "Factor": "Numeric", "EvidenceReference": "Text", "Status": "Text", "Actor": "Text", "Reason": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("PartPurchaseCurrencyConversion", {"ConversionKey": "Text", "PartPurchaseSpecification": "Ref:PartPurchaseSpecification", "FromCurrency": "Text", "ToCurrency": "Text", "Rate": "Numeric", "RateDate": "DateTime", "EvidenceReference": "Text", "Status": "Text", "Actor": "Text", "Reason": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("PartPurchaseRecord", {"PurchaseRecordKey": "Text", "VendorPartMapping": "Ref:VendorPartMapping", "PartPurchaseSpecification": "Ref:PartPurchaseSpecification", "ProductPart": "Ref:ProductPart", "PartRevision": "Ref:PartRevision", "Vendor": "Ref:Vendor", "TransactionKey": "Text", "TransactionLineKey": "Text", "RecordType": "Text", "Status": "Text", "TransactionAt": "DateTime", "DocumentReference": "Text", "Quantity": "Numeric", "QuantityUOM": "Text", "RateBasisUOM": "Text", "Currency": "Text", "ExtendedAmount": "Numeric", "DiscountAmount": "Numeric", "TaxAmount": "Numeric", "FreightAmount": "Numeric", "OtherCharges": "Numeric", "RatePolicy": "Text", "Actor": "Text", "Reason": "Text", "RecordedAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text", "ReversesRecord": "Ref:PartPurchaseRecord", "SupersedesRecord": "Text"}),
    _table("PurchasedPartCostEvidence", {"EvidenceKey": "Text", "CostRunKey": "Text", "ConfigurationSelectionKey": "Text", "ProductPart": "Ref:ProductPart", "PartRevision": "Ref:PartRevision", "PartPurchaseSpecification": "Ref:PartPurchaseSpecification", "PurchaseRecord": "Ref:PartPurchaseRecord", "Vendor": "Ref:Vendor", "AsOfDate": "DateTime", "TransactionAt": "DateTime", "PurchaseQuantity": "Numeric", "PurchaseUOM": "Text", "NormalizedQuantity": "Numeric", "CostingUOM": "Text", "Currency": "Text", "BaseUnitPrice": "Numeric", "DiscountPerUnit": "Numeric", "AppliedUnitRate": "Numeric", "RatePolicy": "Text", "ResolutionStatus": "Text", "Reason": "Text", "Actor": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("PartRegistryCoordinator", {"RegistryKey": "Text", "WriterHostId": "Text", "CoordinatorId": "Text", "BoundAt": "DateTime", "Actor": "Text", "Reason": "Text", "RequestKey": "Text", "Fingerprint": "Text"}),
    _table("PartRegistryRequest", {"RequestKey": "Text", "RequestType": "Text", "RequestFingerprint": "Text", "EntityUUID": "Text", "ReservedPartNumber": "Text", "ReservedNameKey": "Text", "Status": "Text", "Result": "Any", "Payload": "Any", "StartedAt": "DateTime", "UpdatedAt": "DateTime", "WriterHostId": "Text", "CoordinatorId": "Text"}),
    _table("PartIntendedSharingState", {"SharingStateKey": "Text", "ProductPart": "Ref:ProductPart", "Version": "Numeric", "Fingerprint": "Text", "UpdatedAt": "DateTime", "UpdatedBy": "Text", "UpdatedReason": "Text", "LastRequestKey": "Text", "LastRequestFingerprint": "Text"}),
    _table("PartIntendedModelCode", {"IntendedModelCodeKey": "Text", "ProductPart": "Ref:ProductPart", "ProductModelCode": "Ref:ProductModelCode", "Status": "Text", "Version": "Numeric", "CreatedAt": "DateTime", "CreatedBy": "Text", "CreatedReason": "Text", "CreateRequestKey": "Text", "CreateRequestFingerprint": "Text", "UpdatedAt": "DateTime", "UpdatedBy": "Text", "UpdatedReason": "Text", "LastRequestKey": "Text", "LastRequestFingerprint": "Text"}),
    _table("PartIntendedSharingEvent", {"SharingEventKey": "Text", "IntendedModelCode": "Ref:PartIntendedModelCode", "ProductPart": "Ref:ProductPart", "ProductModelCode": "Ref:ProductModelCode", "Action": "Text", "Version": "Numeric", "Actor": "Text", "Reason": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
)


def _merge_table_definitions(tables: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    """Coalesce additive definitions so one schema plan has no duplicate IDs."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for table in tables:
        table_id = str(table["id"])
        if table_id not in merged:
            merged[table_id] = {"id": table_id, "name": table["name"], "columns": []}
            order.append(table_id)
        by_id = {str(column["id"]): column for column in merged[table_id]["columns"]}
        for column in table["columns"]:
            by_id[str(column["id"])] = column
        merged[table_id]["columns"] = list(by_id.values())
    return tuple(merged[table_id] for table_id in order)


FOUNDATION_TABLES = _merge_table_definitions(FOUNDATION_TABLES)

# Explicit Model Code configurations and user-published monetary history. These
# are additive: the existing CostingSnapshot remains workbook lineage evidence.
FOUNDATION_TABLES += (
    _table("CostingConfiguration", {"ConfigurationKey": "Text", "ProductModelCode": "Ref:ProductModelCode", "CurrentRevision": "Ref:CostingConfigurationRevision", "Version": "Numeric", "Status": "Text", "Currency": "Text", "UpdatedAt": "DateTime", "Actor": "Text", "Reason": "Text", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("CostingConfigurationRevision", {"ConfigurationRevisionKey": "Text", "Configuration": "Ref:CostingConfiguration", "Revision": "Numeric", "Status": "Text", "Currency": "Text", "Actor": "Text", "Reason": "Text", "CreatedAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("ConfigurationPartSelection", {"SelectionKey": "Text", "SelectionIdentity": "Text", "StablePartId": "Text", "ConfigurationRevision": "Ref:CostingConfigurationRevision", "ProductPart": "Ref:ProductPart", "Quantity": "Numeric", "QuantityUOM": "Text", "SourcingRoute": "Text", "OccurrenceLabel": "Text", "OptionGroup": "Text", "Status": "Text", "Actor": "Text", "Reason": "Text", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("CostingProcessRate", {"ProcessRateKey": "Text", "LineMaster": "Ref:LineMaster", "Rate": "Numeric", "RateUOM": "Text", "Currency": "Text", "EffectiveAt": "DateTime", "SourceReference": "Text", "Status": "Text", "Actor": "Text", "Reason": "Text", "RecordedAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("CostSnapshot", {"SnapshotKey": "Text", "ProductModelCode": "Ref:ProductModelCode", "Configuration": "Ref:CostingConfiguration", "ConfigurationRevision": "Ref:CostingConfigurationRevision", "CapturedAt": "DateTime", "CostingAsOf": "DateTime", "CreatedBy": "Text", "Label": "Text", "Notes": "Text", "Currency": "Text", "CostBasis": "Text", "TotalCost": "Numeric", "CalculationPolicyVersion": "Text", "PublicationStatus": "Text", "InputFingerprint": "Text", "ManifestFingerprint": "Text", "PartRowCount": "Numeric", "LineRowCount": "Numeric", "EvidenceRowCount": "Numeric", "ContentChecksum": "Text", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("CostSnapshotPart", {"PartOccurrenceKey": "Text", "Snapshot": "Ref:CostSnapshot", "StableOccurrencePath": "Text", "ParentOccurrencePath": "Text", "ProductPart": "Ref:ProductPart", "PartRevision": "Ref:PartRevision", "MetadataVersion": "Ref:PartMetadataVersion", "ParentPartOccurrence": "Ref:CostSnapshotPart", "ConfigurationSelection": "Ref:ConfigurationPartSelection", "PartNumber": "Text", "FrozenName": "Text", "Description": "Text", "EngineeringRevision": "Text", "MetadataVersionNumber": "Numeric", "QuantityPerParent": "Numeric", "EffectiveQuantity": "Numeric", "QuantityUOM": "Text", "SourcingRoute": "Text", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("CostSnapshotLine", {"SnapshotLineKey": "Text", "Snapshot": "Ref:CostSnapshot", "PartOccurrence": "Ref:CostSnapshotPart", "StableOccurrencePath": "Text", "ConfigurationSelection": "Ref:ConfigurationPartSelection", "SourceType": "Text", "SourceKey": "Text", "SourceRecordId": "Text", "SourceRevisionId": "Text", "Material": "Ref:Material", "PurchaseItem": "Ref:PurchaseItem", "Description": "Text", "CostCategory": "Text", "Quantity": "Numeric", "QuantityUOM": "Text", "Rate": "Numeric", "RateUOM": "Text", "Currency": "Text", "GrossCost": "Numeric", "RecoveryCost": "Numeric", "AdjustmentCost": "Numeric", "NetCost": "Numeric", "CalculationBasis": "Text", "CostPolicy": "Text", "Status": "Text", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("CostSnapshotRateEvidence", {"EvidenceKey": "Text", "Snapshot": "Ref:CostSnapshot", "SnapshotLine": "Ref:CostSnapshotLine", "CostingProcessRate": "Ref:CostingProcessRate", "SourceType": "Text", "SourceDocumentId": "Text", "SourceTable": "Text", "SourceRecordId": "Text", "SourceVersion": "Text", "EffectiveAt": "DateTime", "TransactionAt": "DateTime", "Vendor": "Ref:Vendor", "PurchaseRecord": "Ref:PartPurchaseRecord", "RawUnitRate": "Numeric", "NormalizedUnitRate": "Numeric", "SourceCurrency": "Text", "SourceUOM": "Text", "NormalizedCurrency": "Text", "NormalizedUOM": "Text", "ConversionFactor": "Numeric", "BaseUnitPrice": "Numeric", "DiscountPerUnit": "Numeric", "TaxExcluded": "Numeric", "FreightExcluded": "Numeric", "OtherChargesExcluded": "Numeric", "RatePolicy": "Text", "EvidenceReference": "Text", "ResolutionStatus": "Text", "Reason": "Text", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("CostSnapshotPolicy", {"PolicyKey": "Text", "ScopeType": "Text", "ProductModel": "Ref:ProductModel", "ProductModelCode": "Ref:ProductModelCode", "Policy": "Text", "TimeZone": "Text", "ScheduleAnchorAt": "DateTime", "AnchorDay": "Numeric", "Version": "Numeric", "Status": "Text", "Actor": "Text", "Reason": "Text", "EffectiveAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("CostSnapshotPublication", {"PublicationKey": "Text", "SnapshotKey": "Text", "ProductModelCode": "Ref:ProductModelCode", "Status": "Text", "RequestFingerprint": "Text", "InputFingerprint": "Text", "ManifestFingerprint": "Text", "ExpectedPartCount": "Numeric", "ExpectedLineCount": "Numeric", "ExpectedEvidenceCount": "Numeric", "CreatedAt": "DateTime", "UpdatedAt": "DateTime", "CompletedAt": "DateTime", "Actor": "Text", "Reason": "Text"}),
)
FOUNDATION_TABLES = _merge_table_definitions(FOUNDATION_TABLES)

# Additive Part columns. Existing legacy values remain in place and are not
# interpreted as managed identities or engineering baselines.
FOUNDATION_TABLES += (
    _table("ProductPart", {"StablePartId": "Text", "PartNumber": "Text", "EngineeringRevision": "Text", "ScopeType": "Text", "ScopeProduct": "Ref:Product", "ScopeProductModel": "Ref:ProductModel", "ScopeModelCode": "Ref:ProductModelCode", "ScopeTargetId": "Text", "ScopeTargetLabel": "Text", "Description": "Text", "DesignVariant": "Text", "CurrentMetadataVersion": "Ref:PartMetadataVersion", "CurrentPartRevision": "Ref:PartRevision", "MetadataVersion": "Numeric", "PublishStatus": "Text"}),
    _table("PartRevision", {"RevisionLabel": "Text", "BaselineStatus": "Text", "DefinitionHash": "Text", "FinalizedAt": "DateTime", "FinalizedBy": "Text", "FinalizationReason": "Text", "RequestKey": "Text", "RequestFingerprint": "Text"}),
    _table("PartMappingReview", {"StablePartId": "Text", "PartRevision": "Ref:PartRevision", "PartMetadataVersion": "Ref:PartMetadataVersion", "PartNumberUsed": "Text", "NameUsed": "Text"}),
    _table("PartComponentRevision", {"QuantityUOM": "Text", "Actor": "Text", "Reason": "Text", "OccurredAt": "DateTime", "RequestKey": "Text", "RequestFingerprint": "Text", "ComponentStatus": "Text", "SourcingRoute": "Text"}),
)
FOUNDATION_TABLES = _merge_table_definitions(FOUNDATION_TABLES)


@dataclass(frozen=True)
class SchemaPlan:
    document_id: str
    document_name: str
    workspace_id: str
    schema_version: str
    create_tables: tuple[dict[str, Any], ...]
    add_columns: tuple[dict[str, Any], ...]
    update_columns: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"documentId": self.document_id, "documentName": self.document_name, "workspaceId": self.workspace_id, "schemaVersion": self.schema_version, "createTables": list(self.create_tables), "addColumns": list(self.add_columns), "updateColumns": list(self.update_columns)}


def validate_schema_target(document_id: str, *, document_name: str, legacy_doc_id: str | None = None) -> None:
    if not document_id or document_name != SAFARI_DOCUMENT_NAME:
        raise GristValidationError("Schema apply requires an explicitly validated Safari Manufacturing document.")
    if legacy_doc_id and document_id == legacy_doc_id:
        raise GristValidationError("Schema apply refused: target matches the legacy Grist document ID.")


def _validate_remote_target(client: GristAdminClient, document_id: str, workspace_id: str, *, document_name: str, legacy_doc_id: str | None) -> None:
    validate_schema_target(document_id, document_name=document_name, legacy_doc_id=legacy_doc_id)
    document = client.get_document(document_id)
    if document.id != document_id:
        raise GristValidationError("Grist returned document metadata for a different document ID.")
    validate_safari_document(document, workspace_id=workspace_id, legacy_doc_id=legacy_doc_id)


def plan_schema(client: GristAdminClient, document_id: str, *, workspace_id: str, document_name: str = SAFARI_DOCUMENT_NAME, legacy_doc_id: str | None = None) -> SchemaPlan:
    _validate_remote_target(client, document_id, workspace_id, document_name=document_name, legacy_doc_id=legacy_doc_id)
    current = {str(item.get("id")): item for item in client.list_tables(document_id)}
    create: list[dict[str, Any]] = []
    add: list[dict[str, Any]] = []
    update: list[dict[str, Any]] = []
    for table in FOUNDATION_TABLES:
        existing = current.get(str(table["id"]))
        if existing is None:
            create.append(table)
            continue
        existing_columns = {str(item.get("id")): item for item in existing.get("columns", [])}
        missing = [column for column in table["columns"] if str(column["id"]) not in existing_columns]
        if missing:
            add.append({"tableId": table["id"], "columns": missing})
        changed = []
        for column in table["columns"]:
            current_column = existing_columns.get(str(column["id"]))
            current_fields = current_column.get("fields", {}) if isinstance(current_column, dict) else {}
            current_type = current_fields.get("type") or (current_column.get("type") if isinstance(current_column, dict) else None)
            if current_column is not None and current_type != column.get("type"):
                changed.append(column)
        if changed:
            update.append({"tableId": table["id"], "columns": changed})
    return SchemaPlan(document_id, document_name, workspace_id, SCHEMA_VERSION, tuple(create), tuple(add), tuple(update))


def apply_schema(client: GristAdminClient, plan: SchemaPlan, *, document_name: str | None = None, workspace_id: str | None = None, legacy_doc_id: str | None = None) -> SchemaPlan:
    target_name = document_name or plan.document_name
    target_workspace_id = workspace_id or plan.workspace_id
    if target_name != plan.document_name or target_workspace_id != plan.workspace_id:
        raise GristValidationError("Schema plan target metadata cannot be changed between plan and apply.")
    _validate_remote_target(client, plan.document_id, plan.workspace_id, document_name=plan.document_name, legacy_doc_id=legacy_doc_id)
    for table in plan.create_tables:
        client.create_table(plan.document_id, table)
    for change in plan.add_columns:
        client.add_columns(plan.document_id, str(change["tableId"]), list(change["columns"]))
    for change in plan.update_columns:
        client.update_columns(plan.document_id, str(change["tableId"]), list(change["columns"]))
    return plan
