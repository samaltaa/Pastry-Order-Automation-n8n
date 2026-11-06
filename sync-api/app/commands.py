"""Owner commands from Telegram. n8n forwards the message text and sends back the reply.

/sync            run the sync now
/mapping         ask Claude for a new mapping of the active source (SYNC_SOURCE)
/approve 3       approve mapping 3 and sync with it (/reject 3 rejects it)
/runs            the last five sync runs
/held            changes held because they looked like typos
/release REP-010 apply a held change for that product
"""

from app.service import Problem, Service

HELP = "Commands: /sync, /mapping, /approve <id>, /reject <id>, /runs, /held, /release <sku>"


def handle(service: Service, text: str) -> str:
    words = text.strip().split()
    command = words[0].lower().split("@")[0] if words else ""
    argument = words[1] if len(words) > 1 else None
    try:
        if command == "/sync":
            return sync_reply(service.sync(None, dry_run=False, trigger="manual"))
        if command == "/mapping":
            return proposal_reply(service.propose(service.source))
        if command in ("/approve", "/reject"):
            if not argument or not argument.isdigit():
                return f"Which mapping? For example: {command} 3"
            result = service.review(int(argument), command[1:])
            reply = f"Mapping {result['id']} {result['status']}."
            if result["status"] == "approved" and service.load(result["id"])[0] == service.source:
                try:
                    reply += "\n" + sync_reply(service.sync(None, dry_run=False, trigger="manual", wait=True))
                except Problem as problem:
                    reply += f"\nThe sync after it didn't run: {problem.detail}"
            return reply
        if command == "/runs":
            return runs_reply(service.runs(5))
        if command == "/held":
            held = [change for change in service.held_changes() if not change["released"]]
            if not held:
                return "Nothing is held."
            return "Held for review:\n" + "\n".join(
                f"- {c['sku']} {c['field']}: {c['old_value']} -> {c['new_value']} ({c['reason']})"
                for c in held
            )
        if command == "/release":
            if not argument:
                return "Which product? For example: /release REP-010"
            result = service.release(argument)
            return f"Released {result['sku']} ({', '.join(result['released'])}).\n" + sync_reply(
                result["sync"]
            )
        return HELP
    except Problem as problem:
        detail = problem.detail if isinstance(problem.detail, str) else "\n".join(problem.detail)
        return f"Couldn't do that: {detail}"


def sync_reply(report: dict) -> str:
    counts = report["counts"]
    lines = [
        f"Sync run {report['run_id']}: {counts['created']} created, {counts['updated']} updated, "
        f"{counts['stock_updated']} stock changes, "
        + (f"{counts['removed']} set to draft, " if counts["removed"] else "")
        + f"{counts['unchanged']} unchanged."
    ]
    if counts.get("held"):
        lines.append(f"{counts['held']} held for review (/held).")
    if counts["failed"] or counts["conflicts"]:
        lines.append(f"{counts['failed']} failed, {counts['conflicts']} stock conflicts:")
    for change in report["changes"][:10]:
        lines.append(f"- {change['sku']}: {change.get('detail') or change['action']}")
    return "\n".join(lines)


def proposal_reply(report: dict) -> str:
    lines = [f"Mapping {report['id']} for {report['source_table']}:"]
    for field in report["mapping"]["fields"]:
        flag = " (check this)" if field["source_column"] in report["needs_review"] else ""
        target = "ignored" if field["target"] == "ignore" else f"-> {field['target']} ({field['transform']})"
        lines.append(f"- {field['source_column']} {target}{flag}")
    excluded = [row["key"] for row in report["mapping"]["excluded_rows"]]
    if excluded:
        lines.append(f"Skipped rows: {', '.join(excluded)}")
    checks = report.get("checks") or []
    if checks:
        lines.append(f"Checks: {sum(c['passed'] for c in checks)} of {len(checks)} passed")
        lines.extend(f"- failed: {c['name']}: {c['detail']}" for c in checks if not c["passed"])
    if report["problems"]:
        lines.append("Can't be approved: " + "; ".join(report["problems"]))
    elif report.get("status") != "approved":
        lines.append(f"Reply /approve {report['id']} or /reject {report['id']}")
    return "\n".join(lines)


def runs_reply(runs: list[dict]) -> str:
    if not runs:
        return "No sync runs yet."
    return "\n".join(
        f"#{run['id']} {run['started_local']} {run['trigger']}{' (dry run)' if run['dry_run'] else ''}: "
        f"{run['status']}, "
        f"{run['created']} created, {run['updated']} updated, {run['stock_updated']} stock, "
        f"{run['failed']} failed, {run['conflicts']} conflicts"
        for run in runs
    )
