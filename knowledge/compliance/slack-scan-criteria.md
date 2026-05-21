# Slack Compliance Scan Criteria

## Purpose
Daily observability scan across operational channels. Goal is pattern recognition
and gap identification — not enforcement. v1 is intelligence gathering only.

## Channels in scope
brokerage, custody, general, industry, it-general, requests, dev

## Classification dimensions

### Response completeness
- Resolved: query answered, thread closed naturally
- Partial: response given but key question unanswered
- Unresolved: message received no meaningful reply

### Response gaps
- Flag threads where reply took longer than 2 hours
- Flag messages sent after business hours with no next-day follow-up
- Flag threads with no reply at all

### Issue types
- support-request: client or internal needs assistance
- complaint: dissatisfaction expressed
- process-question: how-to or procedural query
- technical-issue: system, tool, or access problem
- general-comms: announcements, FYI, non-actionable
- unclear: intent cannot be determined from message content

### Actionability signals
- Wrong channel — message would be better handled elsewhere
- No owner — nobody picked it up
- Incomplete answer — response given but follow-up needed
- Escalation needed — complexity beyond first responder

## Report format
Aggregate stats first, then specific flagged threads, then unresolved items,
then one pattern observation. Keep flagged threads to top 3-5 only.

## Scope
Observability only. Do not make judgements about individuals.
Report patterns and gaps, not blame.