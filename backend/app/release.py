"""Run only with the deployment migration-owner connection."""

from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from .database import engine


def main():
    command.upgrade(Config("backend/alembic.ini"), "head")
    if engine.dialect.name == "postgresql":
        with engine.begin() as db:
            for statement in (
                Path("deployment/postgres/grants.sql").read_text().split(";")
            ):
                if statement.strip():
                    db.execute(text(statement))
    print("Database migrations and runtime-role grants completed.")


if __name__ == "__main__":
    main()
