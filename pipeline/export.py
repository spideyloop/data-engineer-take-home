"""Write the canonical schema to an atomic CSV snapshot."""

import csv
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from .extract import FACTS
from .storage import Store, json_text

COLUMNS = (
    "posting_id",
    *FACTS,
    "status",
    "source_urls",
    "evidence",
    "first_seen",
    "last_seen",
)


def export_csv(store: Store, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as out:
        temporary = Path(out.name)
        try:
            writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            for row in store.db.execute("SELECT * FROM jobs ORDER BY posting_id"):
                record = json.loads(row["payload"])
                record.update(
                    posting_id=row["posting_id"],
                    first_seen=row["first_seen"],
                    last_seen=row["last_seen"],
                )
                writer.writerow(
                    {
                        key: json_text(value)
                        if isinstance(value, (list, dict, bool))
                        else value
                        for key, value in record.items()
                    }
                )
                count += 1
            out.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return count
