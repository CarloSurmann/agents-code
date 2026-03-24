---
title: "Skill: Classify Sent Email"
type: skill
author: carlo
created: 2026-03-24
---

# Classify Sent Email

You are classifying a sent email to determine if it needs follow-up tracking.

## Decision Rules

Apply these rules IN ORDER. Stop at the first match:

1. **Auto-replies / bounce-backs / out-of-office** → DO NOT TRACK
2. **Internal email** (same domain as sender) → DO NOT TRACK
3. **"Thanks", "Got it", "Sounds good"** type acknowledgment → DO NOT TRACK
4. **Newsletter, automated notification, calendar invite** → DO NOT TRACK
5. **Contains a question expecting a response?** → TRACK
6. **Contains an attachment that is a proposal, quote, or estimate?** → TRACK (type: "proposal")
7. **Contains an invoice or payment request?** → TRACK (type: "invoice")
8. **Requests a meeting, call, or action from the recipient?** → TRACK (type: "request")
9. **Sends information the recipient needs to confirm or review?** → TRACK (type: "review")
10. **None of the above** → DO NOT TRACK

## Output Format

You MUST respond with valid JSON only, no other text:

```json
{
  "should_track": true,
  "reason": "Contains proposal attachment and asks about timeline for decision",
  "item_type": "proposal",
  "context_summary": "Sent proposal for logistics consulting engagement worth approximately €15K. Discussed initial scope at meeting on March 18. Proposal covers Q2 optimization of warehouse routing.",
  "suggested_schedule": "standard"
}
```

### Field definitions:
- **should_track**: `true` or `false`
- **reason**: One sentence explaining your decision
- **item_type**: One of: `"proposal"`, `"quote"`, `"invoice"`, `"request"`, `"review"`, `"question"`, `"general"`
- **context_summary**: 1-3 sentences capturing WHAT was sent, to WHOM, WHY, and any amounts/deadlines mentioned. This summary will be used later to draft follow-up emails, so include the key details.
- **suggested_schedule**: `"standard"` (Day 3/7/14/21), `"urgent"` (Day 1/3/7/14), or `"relaxed"` (Day 5/10/21/30)
