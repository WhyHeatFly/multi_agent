from app.db.session import SessionLocal
from app.services.rag import KnowledgeService


def main() -> None:
    service = KnowledgeService()
    with SessionLocal() as db:
        inserted, sources = service.ingest_directory(db, reset=True)
    print(f"inserted={inserted}")
    for source in sources:
        print(source)


if __name__ == "__main__":
    main()

