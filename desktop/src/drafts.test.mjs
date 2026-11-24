import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("./drafts.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
});
const { decodeDraft, encodeDraft, canSaveRestoredDraft } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

test("restoring across an external edit preserves the old revision and blocks save", () => {
  const persisted = encodeDraft("本人未保存的修改", "original-hash");
  const restored = decodeDraft(persisted);
  assert.equal(restored.content, "本人未保存的修改");
  assert.equal(restored.baseRevision, "original-hash");
  assert.equal(
    canSaveRestoredDraft(restored, "externally-updated-hash"),
    false,
  );
});

test("restoring a draft from the unchanged disk version remains saveable", () => {
  const restored = decodeDraft(encodeDraft("继续编辑", "same-hash"));
  assert.equal(canSaveRestoredDraft(restored, "same-hash"), true);
});

test("legacy plain text is retained but cannot inherit a new disk revision", () => {
  const restored = decodeDraft("# 旧草稿\n\n原话必须保留。");
  assert.equal(restored.content, "# 旧草稿\n\n原话必须保留。");
  assert.equal(restored.baseRevision, null);
  assert.equal(canSaveRestoredDraft(restored, "latest-disk-hash"), false);
});

test("empty drafts and malformed legacy JSON retain their complete content", () => {
  assert.equal(decodeDraft(encodeDraft("", "hash")).content, "");
  assert.equal(decodeDraft('{"unfinished":').content, '{"unfinished":');
  assert.equal(
    canSaveRestoredDraft(decodeDraft('{"unfinished":'), "hash"),
    false,
  );
});

test("a persisted unknown baseline stays unknown after another edit", () => {
  const legacy = decodeDraft("原始草稿");
  const reedited = decodeDraft(
    encodeDraft("再次编辑的草稿", legacy.baseRevision),
  );
  assert.equal(reedited.baseRevision, null);
  assert.equal(canSaveRestoredDraft(reedited, "hash"), false);
});
