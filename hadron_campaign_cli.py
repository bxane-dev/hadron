"""Hadron v3.0 campaign workspace CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hadron_campaign import (
    add_campaign_member,
    campaign_snapshot,
    create_campaign,
    export_campaign_bundle,
    import_campaign_bundle,
    list_campaigns,
)
from hadron_version import __version__


def main():
    parser = argparse.ArgumentParser(
        description="Manage Hadron campaign workspaces."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--db", type=Path, required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--description", default="")

    add = sub.add_parser("add")
    add.add_argument("--db", type=Path, required=True)
    add.add_argument("--campaign-id", type=int, required=True)
    add.add_argument(
        "--type",
        choices=["study", "capsule", "regression", "reproduction", "template"],
        required=True,
    )
    add.add_argument("--member-id", type=int, required=True)
    add.add_argument("--label", default="")

    show = sub.add_parser("show")
    show.add_argument("--db", type=Path, required=True)
    show.add_argument("--campaign-id", type=int, required=True)

    listing = sub.add_parser("list")
    listing.add_argument("--db", type=Path, required=True)
    listing.add_argument("--all", action="store_true")

    export = sub.add_parser("export")
    export.add_argument("--db", type=Path, required=True)
    export.add_argument("--campaign-id", type=int, required=True)
    export.add_argument("--output", type=Path, required=True)

    import_cmd = sub.add_parser("import")
    import_cmd.add_argument("--db", type=Path, required=True)
    import_cmd.add_argument("bundle", type=Path)
    import_cmd.add_argument("--restore-studies", action="store_true")

    args = parser.parse_args()
    print(f"HadronCampaign v{__version__}")

    if args.command == "create":
        cid = create_campaign(
            args.db,
            name=args.name,
            description=args.description,
        )
        print(cid)
        return

    if args.command == "add":
        membership_id = add_campaign_member(
            args.db,
            args.campaign_id,
            member_type=args.type,
            member_id=args.member_id,
            label=args.label,
        )
        print(membership_id)
        return

    if args.command == "show":
        print(
            json.dumps(
                campaign_snapshot(args.db, args.campaign_id),
                indent=2,
            )
        )
        return

    if args.command == "list":
        print(json.dumps(list_campaigns(args.db, include_archived=args.all), indent=2))
        return

    if args.command == "export":
        path = export_campaign_bundle(
            args.db,
            args.campaign_id,
            args.output,
        )
        print(path)
        return

    if args.command == "import":
        cid = import_campaign_bundle(
            args.db,
            args.bundle,
            restore_studies=args.restore_studies,
        )
        print(cid)
        return


if __name__ == "__main__":
    main()
