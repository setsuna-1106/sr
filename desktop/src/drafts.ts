export interface StoredDraft {
  format: "sr-workbench-draft-v1";
  content: string;
  baseRevision: string | null;
}

/** Legacy drafts retain their text, but never inherit a newly read disk version. */
export function decodeDraft(raw: string): StoredDraft {
  try {
    const value = JSON.parse(raw) as Partial<StoredDraft> | null;
    if (
      value &&
      value.format === "sr-workbench-draft-v1" &&
      typeof value.content === "string"
    ) {
      return {
        format: "sr-workbench-draft-v1",
        content: value.content,
        baseRevision:
          typeof value.baseRevision === "string" &&
          value.baseRevision.length > 0
            ? value.baseRevision
            : null,
      };
    }
  } catch {
    // An older app stored the source text directly, including malformed JSON.
  }
  return { format: "sr-workbench-draft-v1", content: raw, baseRevision: null };
}

export function encodeDraft(
  content: string,
  baseRevision: string | null,
): string {
  return JSON.stringify({
    format: "sr-workbench-draft-v1",
    content,
    baseRevision,
  } satisfies StoredDraft);
}

export function canSaveRestoredDraft(
  draft: StoredDraft,
  diskRevision: string,
): boolean {
  return draft.baseRevision !== null && draft.baseRevision === diskRevision;
}
