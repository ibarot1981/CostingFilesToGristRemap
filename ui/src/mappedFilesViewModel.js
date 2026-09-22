export const mappedFileIssueLabels = {
  duplicate_code_ownership: "Duplicate code owner",
  catalog_mismatch: "Catalog mismatch",
  parse_error: "Parse failure",
  external_links: "External links",
  missing_file: "File missing",
  moved_file: "File moved",
  changed_file: "Changed since observation",
};

export function deriveMappedFileGroups(rows, { query = "", status = "all", sort = "path" } = {}) {
  const needle = query.trim().toLowerCase();
  const visible = rows.filter((row) => {
    const file = row.file || {};
    const product = row.product || {};
    const model = row.model || {};
    const haystack = `${file.relative_path || ""} ${file.name || ""} ${row.movedTo || ""} ${product.name || ""} ${model.model_number || ""} ${(row.codes || []).map((item) => String(item.code || "")).join(" ")} ${(row.issues || []).join(" ")}`.toLowerCase();
    const reviewStatus = row.reviewStatus || (row.association ? "mapped" : "unmapped");
    return (!needle || haystack.includes(needle)) && (status === "all" || (status === "needs-review" ? Boolean(row.issues?.length) : reviewStatus === status));
  });

  visible.sort((left, right) => {
    const leftFile = left.file || {};
    const rightFile = right.file || {};
    const leftChange = left.lastObservedChange?.modifiedAt || leftFile.modified_at || "";
    const rightChange = right.lastObservedChange?.modifiedAt || rightFile.modified_at || "";
    if (sort === "last-change") return rightChange.localeCompare(leftChange);
    const leftValue = sort === "name" ? (leftFile.name || "") : (leftFile.relative_path || "");
    const rightValue = sort === "name" ? (rightFile.name || "") : (rightFile.relative_path || "");
    return leftValue.localeCompare(rightValue);
  });

  const grouped = new Map();
  for (const row of visible) {
    const product = row.product || {};
    const model = row.model || {};
    const label = row.association ? `${product.name || "Unknown Product"} / ${model.model_number || "Unknown Model"}` : "Unassigned / Unassigned";
    grouped.set(label, [...(grouped.get(label) || []), row]);
  }
  return [...grouped.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([label, groupRows]) => ({ label, rows: groupRows }));
}
