"""Read-only Costing-New rate display through reviewed material aliases."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Any
from app.material_resolution import resolve_material


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
            field = 'MaterialLatestRate' if entries else 'Default_MaterialRate'
            rate = _number(record['fields'].get(field))
            row.update({"basis": field, "masterRecordId": record['id'], "status": 'available' if rate is not None else 'rate_unavailable',
                        "costingNewRate": str(rate) if rate is not None else None})
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
