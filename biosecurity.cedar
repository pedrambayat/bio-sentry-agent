// ============================================================
//  biosecurity.cedar  —  Bio-Sentry Agent Policy Set
//  Namespace: Bio_Sentry_Agent  (derived from agent name)
// ============================================================

// ── Base permit ─────────────────────────────────────────────
// Allow all tool calls by default.  The forbid below overrides
// this for synthesis_order when the homology threshold is exceeded.
@id("biosecurity-base-permit")
permit(principal, action, resource);


// ── Hard Biosecurity Guardrail ───────────────────────────────
// Cedar cannot compare decimal literals, so we use a Long:
//   homology_score_int = raw_score × 1000
//   threshold 0.40  →  400 in integer space
//
// This policy fires for ANY action (i.e. any tool call) where
// the score is present AND exceeds the red line.  In practice
// it will only be relevant for synthesis_order because that is
// the only tool that receives homology_score_int as a parameter,
// but keeping it action-agnostic is more defensive.

@id("biosecurity-block-high-homology")
forbid(principal, action, resource)
when {
    context has parameters &&
    context.parameters has homology_score_int &&
    context.parameters.homology_score_int > 400
};


// ── Amber Zone: require explicit human approval ──────────────
// Sequences in the 251–400 range are borderline.  They may proceed
// only when a human operator has explicitly reviewed and approved.
// The agent passes human_approved=True after getting user confirmation.

@id("biosecurity-block-amber-no-approval")
forbid(principal, action, resource)
when {
    context has parameters &&
    context.parameters has homology_score_int &&
    context.parameters.homology_score_int > 250 &&
    context.parameters.homology_score_int <= 400 &&
    context.parameters has human_approved &&
    context.parameters.human_approved == false
};


// ── POST_TOOL Audit: catch flagged verdicts in responses ─────
// Prevents a compromised or malfunctioning screener tool from
// leaking a FLAGGED result downstream by blocking any synthesis_order
// response that contains the string "FLAGGED".

@id("biosecurity-audit-flagged-response")
forbid(principal, action, resource)
when {
    context has response_json &&
    context.response_json like "*FLAGGED*"
};


// ── Annotation: feedback for the STEER strategy ──────────────
// When the above forbid fires and Strategy.STEER is used, Sondera
// will surface the @id as the denial reason back to the model,
// which then knows to attempt a safe redesign.
//
// The reason string is the policy @id (e.g. "biosecurity-block-high-homology").
// The agent's system prompt instructs it to interpret this as a signal
// to redesign the sequence so homology drops below the threshold.
