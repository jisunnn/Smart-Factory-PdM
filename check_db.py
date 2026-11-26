"""rul_predictions 저장 상태 확인.  python check_db.py   /   python check_db.py --reset (테이블 비우기)"""
import asyncio
import sys

from models.database import db_manager
from models.rul_repository import fetch_latest_rul, init_rul_tables


async def main():
    await db_manager.connect()
    try:
        await init_rul_tables(db_manager.pool)
        if "--reset" in sys.argv:
            if input("rul_predictions 의 모든 행을 삭제합니다. 계속하려면 yes 입력: ") == "yes":
                async with db_manager.pool.acquire() as c:
                    await c.execute("TRUNCATE rul_predictions")
                print("삭제 완료")
            return
        async with db_manager.pool.acquire() as c:
            total = await c.fetchval("SELECT count(*) FROM rul_predictions")
            engines = await c.fetchval("SELECT count(DISTINCT asset_id) FROM rul_predictions")
        print(f"저장된 행: {total} | 엔진 수: {engines}")
        for r in (await fetch_latest_rul(db_manager.pool))[:5]:
            print(dict(r))
    finally:
        await db_manager.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
