"""OPEN_TASKS.md: everything still to fill in, computed from the data."""

import shutil

import open_tasks
import pbook


def test_open_tasks_lists_every_gap(tmp_path, make_status_list):
    shutil.copy(pbook.ROOT / "categories.yml", tmp_path / "categories.yml")
    (tmp_path / "congresses.yml").write_text(
        "- id: 2026-Vilnius\n  name: LYMEC Autumn Congress 2026\n  city: Vilnius\n  date_start: null\n  date_end: 2026-10-25\n"
    )
    meta = {
        "id": "PB2026-6.05", "number": "6.05", "title": "Old resolution", "congress": None, "adopted": None,
        "category": "climate-energy", "status": "in_force", "replaces": [], "superseded_by": None, "amends": [],
        "submitted_by": [], "co_signers": [], "source": "migration",
    }  # fmt: skip
    body = "Co-signed by: Silvia Fernández (IMS), JOVD\n\nThis resolution archives Resolution 6.02 on Nuclear Power and merges 6.03."
    pbook.write_resolution(pbook.resolution_path(tmp_path, meta), meta, body)
    make_status_list(tmp_path, [("6.01", "", ""), ("6.02", "", ""), ("6.05", "Old resolution", "in force")])

    text = open_tasks.tasks_markdown(tmp_path)
    assert "## Policy Book numbers without a status (2)\n" in text and "- Chapter 6: 6.01, 6.02" in text
    assert "## Resolutions whose congress is not known (1)" in text and "- 6.05 Old resolution" in text
    assert "- 2026-Vilnius LYMEC Autumn Congress 2026: first day missing" in text
    assert "- 6.05 Old resolution (co-signers)" in text  # the list names a person, so it stayed in the text
    assert "## Resolutions without any submitting organisation (1)" in text
    assert '- 6.05: "This resolution archives Resolution 6.02 on Nuclear Power and merges 6.03." → 6.02, 6.03' in text

    # once the status list records the archived resolution with its title, it is no longer listed
    make_status_list(tmp_path, [("6.01", "", "not used"), ("6.02", "Nuclear Power", "archived"), ("6.03", "X", "archived"),
                                ("6.05", "Old resolution", "in force")])  # fmt: skip
    text = open_tasks.tasks_markdown(tmp_path)
    assert "## Policy Book numbers without a status (0)" in text and "## Archived resolutions mentioned in texts (0)" in text
    assert open_tasks.main([str(tmp_path)]) == 0 and (tmp_path / "OPEN_TASKS.md").exists()
