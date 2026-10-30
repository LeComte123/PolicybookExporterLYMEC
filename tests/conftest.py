import pytest
from openpyxl import Workbook

import status_list

HEADER = ["Number", "Title", "Status", "Adopted at (congress id)", "Adopted on", "Replaced by (number)", "Archived at (congress id)"]


@pytest.fixture
def make_status_list():
    """Write a minimal resolution_status_list.xlsx: rows of (number, title, status[, replaced by, archived at])."""

    def make(root, rows):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = status_list.SHEET
        sheet.append(HEADER)
        for number, title, status, *rest in rows:
            replaced_by, archived_at = (rest + [None, None])[:2]
            sheet.append([number, title, status, None, None, replaced_by, archived_at])
        workbook.save(root / status_list.FILE)
        return root / status_list.FILE

    return make
