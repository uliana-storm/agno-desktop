Tony, I need you to set up a daily compliance observability scan for me.
Every day at 6pm Melbourne time, scan [CHANNEL-NAME/S] and post an EOD intelligence report to [CHANNEL-NAME].
For each scan, pull all messages from today using get_messages_since_today and classify every thread across these dimensions:
Response quality — was the query fully resolved, partially addressed, or ignored entirely? Flag threads where the response was incomplete or where the conversation ended without resolution.
Response gaps — identify threads with long delays between a message and a reply, or messages that received no reply at all.
Issue classification — categorise each thread as one of: support request, complaint, process question, technical issue, general comms, or unclear.
Actionability — note what could have been handled differently: better routing, faster response, clearer answer, escalation needed.
Unaddressed items — list anything raised today that has no resolution and likely needs follow-up tomorrow.
Structure the EOD report as:

Summary stats (total threads, resolution rate, avg response gap, issue type breakdown)
Top 3-5 threads worth reviewing (with timestamp and brief reason)
Unresolved items needing follow-up
One observation about today's overall pattern

Post the report using post_eod_report to [REPORT-CHANNEL].
Schedule this as a recurring daily run at 18:00 Australia/Melbourne. Name the schedule compliance-eod-scan.