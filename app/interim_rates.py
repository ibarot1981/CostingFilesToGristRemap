"""Read-only Costing-New rate display through reviewed material aliases."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Any
import os
from dotenv import load_dotenv
from app.material_resolution import resolve_material
from app.cost_dates import cost_date_evidence


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, (bool, list, dict)) or value == "":
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (ValueError, InvalidOperation):
        return None


def compare_interim_rates(ods_rates: dict[str, Any], mappings: dict[str, str],
                          masters: list[dict[str, Any]], logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for name, ods_rate in sorted(ods_rates.items()):
        match = resolve_material(name, mappings)
        candidates = [record for record in masters if match.canonical_name and record['fields'].get('MasterMaterial') == match.canonical_name]
        row: dict[str, Any] = {"material": name, "canonicalMaterial": match.canonical_name,
                               "mappingStatus": match.status, "odsRate": ods_rate, "costingNewRate": None,
                               "difference": None, "status": "unmapped" if not match.canonical_name else "master_unavailable",
                               "basis": None, "processingBlocker": False, "rateLogRecordIds": []}
        if len(candidates) > 1:
            row['status'] = 'ambiguous_master'
        elif len(candidates) == 1:
            record = candidates[0]
            entries = [log for log in logs if log['fields'].get('MasterMaterial') == record['id']]
            row['rateLogRecordIds'] = [entry['id'] for entry in entries]
            selected_entry = entries[-1] if entries else None
            field = 'MaterialLatestRate' if entries else 'Default_MaterialRate'
            rate = _number(record['fields'].get(field))
            row.update({"basis": field, "masterRecordId": record['id'], "status": 'available' if rate is not None else 'rate_unavailable',
                        "costingNewRate": str(rate) if rate is not None else None})
            date_fields = selected_entry.get("fields", {}) if selected_entry else record['fields']
            date_evidence = cost_date_evidence(date_fields, source_kind="rate_log" if selected_entry else "rate_master")
            row.update({"selectedRateLogId": selected_entry.get("id") if selected_entry else None,
                **date_evidence})
            old = _number(ods_rate)
            if old is not None and rate is not None:
                row['difference'] = str(rate-old)
        rows.append(row)
    return rows


def load_interim_rates(ods_rates: dict[str, Any]) -> dict[str, Any]:
    """Never exposes a write operation on the legacy source."""
    from app.config import load_material_mapping
    from app.grist import GristClient
    from app.grist_admin import GristAdminClient
    from app.domain import utc_now
    client = GristClient.from_environment()
    document = GristAdminClient(client.api_key, client.base_url).get_document(client.doc_id)
    if document.name != 'Costing-New':
        raise ValueError('The interim rate source must be named exactly Costing-New')
    masters = client.fetch_table_records_with_ids('MasterMaterial')
    logs = client.fetch_table_records_with_ids('MaterialRateLog')
    return {"status": "available", "source": "Costing-New", "readAt": utc_now(), "readOnly": True,
            "processingBlocker": False, "rows": compare_interim_rates(ods_rates, load_material_mapping(), masters, logs)}


def load_material_weight_catalog() -> dict[str, Any]:
    """Read the governed Costing-New kg/m factors through the existing read-only boundary."""
    from app.grist import GristClient
    from app.grist_admin import GristAdminClient
    from app.domain import utc_now

    load_dotenv()
    api_key = os.getenv("GRIST_API_KEY", "").strip()
    source_doc_id = os.getenv("COSTING_NEW_GRIST_DOC_ID", "cPCjjr9oCzhWC7z8vJbcky").strip()
    base_url = os.getenv("GRIST_BASE_URL", "https://docs.getgrist.com").strip()
    if not api_key or not source_doc_id:
        raise ValueError("The read-only Costing-New material-weight source is not configured.")
    client = GristClient(api_key=api_key, doc_id=source_doc_id, base_url=base_url)
    document = GristAdminClient(client.api_key, client.base_url).get_document(client.doc_id)
    if document.name != "Costing-New":
        raise ValueError("The material weight source must be named exactly Costing-New")
    records = []
    for record in client.fetch_table_records_with_ids("MasterMaterial"):
        fields = record.get("fields", {})
        name = str(fields.get("MasterMaterial") or "").strip()
        if not name:
            continue
        factor = _number(fields.get("KGsPerMeter"))
        records.append({
            "canonicalName": name,
            "masterMaterialId": int(record["id"]),
            "code": str(fields.get("Code") or fields.get("MaterialCode") or "").strip(),
            "inUse": fields.get("InUse"),
            "kgPerMeter": str(factor) if factor is not None and factor > 0 else None,
            "factorStatus": "available" if factor is not None and factor > 0 else "unavailable",
            "sourceDate": fields.get("LatestKGsPerMeterDate"),
            "source": "Costing-New",
            "sourceDocumentId": client.doc_id,
            "sourceTable": "MasterMaterial",
            "sourceField": "KGsPerMeter",
            "factorUnit": "kg/m",
        })
    return {"status": "available", "source": "Costing-New", "readOnly": True,
            "sourceDocumentId": client.doc_id, "readAt": utc_now(), "records": records}
