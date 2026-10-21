import argparse
import getpass

from sqlalchemy import delete, select

from .database import SessionLocal, set_tenant
from .models import AuditEvent, Membership, SessionRecord, Tenant, User
from .security import hasher, password_policy


def bootstrap(name, email, user_name):
    password = getpass.getpass("Initial administrator password (12+ characters): ")
    password_policy(password)
    with SessionLocal.begin() as db:
        if db.scalar(select(User).where(User.email == email.casefold())):
            raise RuntimeError("Account already exists")
        tenant = Tenant(
            name=name,
            is_demo=False,
            config={"sla_hours": 24, "approval_hours": 24, "agent_enabled": True},
        )
        user = User(
            email=email.casefold(), name=user_name, password_hash=hasher.hash(password)
        )
        db.add_all([tenant, user])
        db.flush()
        set_tenant(db, tenant.id)
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role="admin"))
        db.add(
            AuditEvent(
                tenant_id=tenant.id,
                actor_id=user.id,
                event="tenant.bootstrapped",
                resource_id=tenant.id,
            )
        )
    print("Merchant workspace created. Password was not stored in logs.")


def reset_password(email, reason):
    password = getpass.getpass("New account password (12+ characters): ")
    password_policy(password)
    if password != getpass.getpass("Repeat password: "):
        raise RuntimeError("Passwords differ")
    with SessionLocal.begin() as db:
        user = db.scalar(
            select(User).where(User.email == email.casefold()).with_for_update()
        )
        if not user:
            raise RuntimeError("Account not found")
        user.password_hash = hasher.hash(password)
        db.execute(delete(SessionRecord).where(SessionRecord.user_id == user.id))
        for tenant_id in db.scalars(
            select(Membership.tenant_id).where(Membership.user_id == user.id)
        ):
            set_tenant(db, tenant_id)
            db.add(
                AuditEvent(
                    tenant_id=tenant_id,
                    actor_id="deployment-operator",
                    event="auth.operator_password_reset",
                    resource_id=user.id,
                    details={"reason": reason},
                )
            )
            db.flush()
    print("Account password reset; all sessions revoked. Operator action audited.")

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    boot = sub.add_parser("bootstrap")
    boot.add_argument("--tenant", required=True)
    boot.add_argument("--email", required=True)
    boot.add_argument("--name", required=True)
    reset = sub.add_parser("reset-password")
    reset.add_argument("--email", required=True)
    reset.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.command == "bootstrap":
        bootstrap(args.tenant, args.email, args.name)
    elif args.command == "reset-password":
        reset_password(args.email, args.reason)


if __name__ == "__main__":
    main()
