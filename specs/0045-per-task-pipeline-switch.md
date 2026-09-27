# Spec 0045: per-task pipeline switching (UI track)

## Goal

Finish what ChannelSwitcher + PipelineToggle started: the builder can switch
ANY task's engine (asr/s2s/chat) after creation. What exists: channel
multi-select with hybrid emission (voice+chat), single-task asr↔s2s PATCH
flips. Gaps this spec closes: hardcoded `task_index: 0` (multi-task agents
can't switch task 2+), no chat option on the toggle, no clear-to-inferred
action. Backend (spec 0044) already supports all three.

## Non-goals

- Backend changes (spec 0044 owns the contract; this track binds to it).
- Channel switching (shipped in `channel-config.tsx`; verified only).
- New design system work (existing toggle/switch patterns reused).

## UI contracts (binding — disjoint file sets per dev)

**Slice A — pipeline switch owner (Dev A).** Files ONLY:
`src/components/settings/synthesizer-config.tsx`,
`__tests__/components/pipeline-toggle.test.tsx` (new):
- `PipelineToggle` gains `taskIndex` (default 0, preserving the single-task
  invariant) and renders per addressed task; flip PATCH targets
  `tasks_patch: [{ task_index, pipeline }]` with the addressed index.
- Chat option appears if and only if the agent's channels include `chat`
  (reads form-state channels; never its own fetch); pressing it PATCHes
  `pipeline: "chat"`. asr/s2s options unchanged.
- Clear-to-inferred action (subtle button): PATCH `clear: ["pipeline"]` for
  the addressed task; pressed state returns to inference display.
- Dirty-guard + optimistic-sync semantics preserved per addressed task.

**Slice B — verify + e2e owner (Dev B).** Files ONLY:
`__tests__/components/channel-hybrid.test.tsx` (new),
`__tests__/services/api-transforms.test.ts` (append describes only):
- Hybrid emission e2e (mocked hooks): channels [voice,chat] → PUT/payload
  carries both; staged banner copy shows "(hybrid)"; at-least-one invariant
  (unchecking last channel is a no-op).
- Pipeline PATCH shapes: per-task index, chat pointer, clear semantics
  (JSON round-trips of `TaskPatchOperation`).
- No `src/` edits (bugs found go loud to the integrator, never test-side
  workarounds).

## Test plan (UI track, jest)

Per-task flips (mocked `usePatchAgent`), chat-option gating on/off channels,
clear-to-inference, hybrid emission, at-least-one invariant. Existing suites
must stay green.

## Verification

```sh
npm run lint && npx tsc --noEmit && npm test
```

## Rollout

UI-only, additive. PATCH shapes already served by the backend; no fallback
needed (old backends 422 unknown `clear` targets loudly — surfaced, not
silent).

## Burn-down

- [x] Slice A: per-task pipeline switch (Dev A).
- [x] Slice B: verify + e2e (Dev B).
- [x] Integrator: toggle component tests + gate + report (no commit — gate-held).
