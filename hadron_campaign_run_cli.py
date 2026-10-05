"""Hadron v3.0 executable campaign CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hadron_campaign_run import (
    campaign_health,
    check_campaign_reference,
    create_campaign_report_bundle,
    list_campaign_references,
    run_campaign_to_database,
    save_campaign_reference,
)
from hadron_version import __version__


def main():
    parser = argparse.ArgumentParser(
        description="Execute and verify Hadron campaigns."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health")
    health.add_argument("--db", type=Path, required=True)
    health.add_argument("--campaign-id", type=int, required=True)
    health.add_argument("--output", type=Path, default=None)

    run = sub.add_parser("run")
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("--campaign-id", type=int, required=True)
    run.add_argument("--workers", type=int, default=1)
    run.add_argument("--output", type=Path, default=None)

    reference = sub.add_parser("reference")
    reference.add_argument("--db", type=Path, required=True)
    reference.add_argument("--campaign-id", type=int, required=True)
    reference.add_argument("--name", default="Reference")

    check = sub.add_parser("check-reference")
    check.add_argument("--db", type=Path, required=True)
    check.add_argument("--campaign-id", type=int, required=True)
    check.add_argument("--reference-id", type=int, default=None)
    check.add_argument("--output", type=Path, default=None)

    report = sub.add_parser("report")
    report.add_argument("--db", type=Path, required=True)
    report.add_argument("--campaign-id", type=int, required=True)
    report.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    print(f"HadronCampaignRun v{__version__}")

    if args.command == "health":
        payload = campaign_health(args.db, args.campaign_id)
        if args.output:
            args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(
            f"Healthy: {payload['healthy_members']}/{payload['members']}"
        )
        raise SystemExit(0 if payload["healthy"] else 5)

    if args.command == "run":
        payload = run_campaign_to_database(
            args.db,
            args.campaign_id,
            workers_override=args.workers,
        )
        if args.output:
            args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(
            f"Studies matching: {payload['passed']}/{payload['studies']}"
        )
        raise SystemExit(0 if payload["all_passed"] else 6)

    if args.command == "reference":
        reference_id = save_campaign_reference(
            args.db,
            args.campaign_id,
            name=args.name,
        )
        print(reference_id)
        return

    if args.command == "check-reference":
        references = list_campaign_references(
            args.db,
            args.campaign_id,
            limit=100,
        )
        if args.reference_id is None:
            if not references:
                raise SystemExit("No saved reference for campaign.")
            reference = references[0]
        else:
            matches = [
                item for item in references
                if int(item["id"]) == int(args.reference_id)
            ]
            if not matches:
                raise SystemExit("Reference ID not found for campaign.")
            reference = matches[0]

        payload = check_campaign_reference(
            args.db,
            reference["payload"],
        )
        if args.output:
            args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(
            f"Reference matches: {payload['members_matching']}/{payload['members_checked']}"
        )
        raise SystemExit(0 if payload["matches"] else 7)

    if args.command == "report":
        path = create_campaign_report_bundle(
            args.db,
            args.campaign_id,
            args.output,
        )
        print(path)
        return


if __name__ == "__main__":
    main()
