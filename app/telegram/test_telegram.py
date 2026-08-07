import asyncio

from app.database.database import create_database
from app.telegram.telegram_client import test_connection


def main() -> None:
    create_database()
    asyncio.run(test_connection())


if __name__ == "__main__":
    main()