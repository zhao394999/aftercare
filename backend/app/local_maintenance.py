"""Snapshot and restore synthetic local acceptance data; no secrets in output."""

import argparse
import hashlib
import io
import json
import sys
import tarfile

from sqlalchemy import func, select

from .config import settings
from .database import SessionLocal, set_tenant
from .models import Base, ModelBudget, Order, Tenant
from .storage import s3_client


def snapshot():
    result = {"tenants": {}, "budget": {}, "objects": {}}
    with SessionLocal() as db:
        for tenant_id in db.scalars(select(Tenant.id)).all():
            set_tenant(db, tenant_id)
            counts = {}
            for table in Base.metadata.sorted_tables:
                if "tenant_id" in table.c:
                    counts[table.name] = db.scalar(
                        select(func.count())
                        .select_from(table)
                        .where(table.c.tenant_id == tenant_id)
                    )
            counts["ledger"] = list(
                db.execute(
                    select(
                        func.coalesce(func.sum(Order.paid_cents), 0),
                        func.coalesce(func.sum(Order.refunded_cents), 0),
                        func.coalesce(func.sum(Order.reserved_cents), 0),
                    ).where(Order.tenant_id == tenant_id)
                ).one()
            )
            result["tenants"][tenant_id] = counts
        for row in db.scalars(select(ModelBudget)):
            result["budget"][row.key] = [row.spent_microusd, row.reserved_microusd]
    client = s3_client()
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=settings().s3_bucket
    ):
        for obj in page.get("Contents", []):
            body = client.get_object(Bucket=settings().s3_bucket, Key=obj["Key"])[
                "Body"
            ].read()
            result["objects"][obj["Key"]] = hashlib.sha256(body).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["snapshot", "export", "import"])
    args = parser.parse_args()
    cfg = settings()
    if args.command == "snapshot":
        print(json.dumps(snapshot(), sort_keys=True))
        return
    client = s3_client()
    if args.command == "export":
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
            for page in client.get_paginator("list_objects_v2").paginate(
                Bucket=cfg.s3_bucket
            ):
                for obj in page.get("Contents", []):
                    response = client.get_object(Bucket=cfg.s3_bucket, Key=obj["Key"])
                    body = response["Body"].read()
                    member = tarfile.TarInfo(obj["Key"])
                    member.size = len(body)
                    member.pax_headers["content_type"] = response.get(
                        "ContentType", "application/octet-stream"
                    )
                    archive.addfile(member, io.BytesIO(body))
    else:
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as archive:
            for member in archive:
                if (
                    not member.isfile()
                    or member.size > 20 * 1024 * 1024
                    or ".." in member.name.split("/")
                    or member.name.startswith("/")
                ):
                    raise ValueError("invalid backup object")
                client.put_object(
                    Bucket=cfg.s3_bucket,
                    Key=member.name,
                    Body=archive.extractfile(member).read(),
                    ContentType=member.pax_headers.get(
                        "content_type", "application/octet-stream"
                    ),
                )


if __name__ == "__main__":
    main()
