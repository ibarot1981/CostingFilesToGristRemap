import assert from "node:assert/strict";
import test from "node:test";
import { deriveMappedFileGroups, mappedFileIssueLabels } from "../src/mappedFilesViewModel.js";

const rows = [
  {
    file: { name: "A-new.ods", relative_path: "A/A-new.ods", modified_at: "2026-09-10T00:00:00Z" },
    product: { name: "Product A" }, model: { model_number: "MODEL-1" },
    codes: [{ code: "CODE-1" }], association: { version: 2 }, reviewStatus: "conflict",
    issues: ["duplicate_code_ownership", "moved_file"], movedTo: "Z/A-new.ods", lastObservedChange: { modifiedAt: "2026-09-10T00:00:00Z" },
  },
  {
    file: { name: "A-old.ods", relative_path: "A/A-old.ods", modified_at: "2026-09-08T00:00:00Z" },
    product: { name: "Product A" }, model: { model_number: "MODEL-1" },
    codes: [{ code: "CODE-2" }], association: { version: 1 }, reviewStatus: "mapped",
    issues: [], lastObservedChange: { modifiedAt: "2026-09-08T00:00:00Z" },
  },
  {
    file: { name: "Unassigned.ods", relative_path: "U/Unassigned.ods", modified_at: "2026-09-09T00:00:00Z" },
    product: null, model: null, codes: [], association: null, reviewStatus: "unmapped",
    issues: ["parse_error"], lastObservedChange: { modifiedAt: "2026-09-09T00:00:00Z" },
  },
];

test("groups by Product/Model and sorts rows by last observed change", () => {
  const groups = deriveMappedFileGroups(rows, { sort: "last-change" });
  assert.deepEqual(groups.map((group) => group.label), ["Product A / MODEL-1", "Unassigned / Unassigned"]);
  assert.deepEqual(groups[0].rows.map((row) => row.file.name), ["A-new.ods", "A-old.ods"]);
});

test("filters conflict and needs-review queues and searches identity/path/issues", () => {
  const conflicts = deriveMappedFileGroups(rows, { status: "conflict" });
  assert.deepEqual(conflicts.flatMap((group) => group.rows.map((row) => row.file.name)), ["A-new.ods"]);

  const review = deriveMappedFileGroups(rows, { status: "needs-review" });
  assert.equal(review.reduce((count, group) => count + group.rows.length, 0), 2);

  const search = deriveMappedFileGroups(rows, { query: "product a", status: "all" });
  assert.equal(search.reduce((count, group) => count + group.rows.length, 0), 2);
  assert.equal(mappedFileIssueLabels.duplicate_code_ownership, "Duplicate code owner");
  const movedPath = deriveMappedFileGroups(rows, { query: "Z/A-new.ods" });
  assert.equal(movedPath.flatMap((group) => group.rows).length, 1);
  assert.equal(mappedFileIssueLabels.moved_file, "File moved");
});
