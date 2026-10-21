"""Tenant-scoped policy chunks shared by questions and investigations."""

import re
from collections import Counter
from math import log

from sqlalchemy import or_, select

from .models import Policy


def terms(text):
    text = text.casefold()
    words = re.findall(r"[a-z0-9_]+", text)
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(part[i : i + 2] for i in range(max(1, len(part) - 1)))
    return words


def retrieve(db, tenant_id, query, channel, at, limit=4):
    # shortcut: rebuild chunks for small libraries; persist an index when measured latency warrants it.
    rows = db.scalars(
        select(Policy)
        .where(
            Policy.tenant_id == tenant_id,
            Policy.state == "published",
            Policy.channel.in_(["*", channel]),
            Policy.effective_from <= at,
            or_(Policy.effective_until.is_(None), Policy.effective_until > at),
        )
        .order_by(Policy.version.desc(), Policy.id)
    ).all()
    # The newest applicable version of a title supersedes older published versions.
    versions = {}
    for policy in rows:
        versions.setdefault((policy.title, policy.channel), policy)
    chunks = []
    for policy in versions.values():
        for offset in range(0, len(policy.body), 700):
            body = policy.body[offset : offset + 800]
            chunks.append(
                {
                    "citation": f"{policy.id}:{offset}",
                    "id": policy.id,
                    "title": policy.title,
                    "version": policy.version,
                    "body": body,
                    "hash": policy.content_hash,
                    "channel": policy.channel,
                    "allow_refund_on_loss": policy.allow_refund_on_loss,
                    "max_refund_cents": policy.max_refund_cents,
                }
            )
    if not chunks:
        return []
    counts = [Counter(terms(c["title"] + " " + c["body"])) for c in chunks]
    average = max(1, sum(sum(c.values()) for c in counts) / len(counts))
    scores = [0.0] * len(chunks)
    for term in set(terms(query)):
        frequency = sum(term in c for c in counts)
        weight = log(1 + (len(chunks) - frequency + 0.5) / (frequency + 0.5))
        for i, count in enumerate(counts):
            tf = count[term]
            scores[i] += (
                weight
                * tf
                * 2.2
                / (tf + 1.2 * (0.25 + 0.75 * sum(count.values()) / average))
            )
    ranked = sorted(zip(scores, chunks), key=lambda pair: pair[0], reverse=True)
    return [dict(chunk, score=round(score, 4)) for score, chunk in ranked if score > 0][
        :limit
    ]
