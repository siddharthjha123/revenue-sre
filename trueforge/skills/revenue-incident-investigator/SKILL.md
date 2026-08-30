---
name: revenue-incident-investigator
description: Verify Revenue SRE payment-incident evidence and calculations in a sandbox before diagnosing or recommending recovery.
---

# Revenue incident investigation

Use this skill after calling the Revenue SRE MCP tool
`get_incident_evidence`. The MCP response is the source evidence; this skill
independently checks its arithmetic and internal consistency. It does not
detect incidents, call Razorpay, persist proposals, or authorize execution.

## Procedure

1. Save the complete structured `get_incident_evidence` result as JSON at
   `/tmp/incident-investigation.json`. Do not add inferred fields.
2. Run:

   ```bash
   python /opt/tfy/skills/revenue-incident-investigator/scripts/verify_incident.py \
     /tmp/incident-investigation.json
   ```

3. Read the JSON written to stdout.
4. Continue only when `verified` is `true`.
5. Cite the returned `evidence_ids` in the final diagnosis.
6. Treat entries in `limitations` as mandatory uncertainty disclosures.

If the command exits non-zero or returns `verified: false`, report that the
evidence failed independent verification and stop. Do not repair, estimate, or
silently replace inconsistent values.

## Razorpay MCP boundary

The official Razorpay MCP may be used after verification for read-only
corroboration. Use only fetch/list operations for payment IDs already present
in the verified evidence. Never use a create, update, capture, refund, payment
link, or notification tool in this investigation procedure.

## Interpretation rules

- Backend incident detection is authoritative; sandbox verification is a
  second check, not another detector.
- `error_source=bank` means the observed failure boundary is the bank. It does
  not prove an internal bank root cause.
- Convert INR subunits only for display: `display_rupees = subunits / 100`.
- Keep confirmed facts separate from hypotheses.
- Never place customer PII, credentials, raw authorization headers, or full
  provider payloads into sandbox files or final output.

