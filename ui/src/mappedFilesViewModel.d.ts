import type { MappedFile } from "./types";

export declare const mappedFileIssueLabels: Record<string, string>;
export declare function deriveMappedFileGroups(
  rows: MappedFile[],
  options?: { query?: string; status?: string; sort?: string },
): Array<{ label: string; rows: MappedFile[] }>;
