import csv

import pytest

import status_list


@pytest.fixture
def make_status_list():
    """Write a minimal resolution_status_list.csv: rows of (number, title, status[, replaced by, archived at])."""

    def make(root, rows):
        with open(root / status_list.FILE, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Number", "Status", "Archived at", "Replaced by", "Title"])
            for number, title, status, *rest in rows:
                replaced_by, archived_at = (rest + [None, None])[:2]
                writer.writerow([number, status, archived_at or "", replaced_by or "", title])
        return root / status_list.FILE

    return make
