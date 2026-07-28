"""Tests for the translation-export utility (CSV table embedding)."""

from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.translation_export import (
    embed_tables_in_markdown,
    find_markdown_table_blocks,
    generate_translation_markdown,
    generate_translation_markdown_for_job,
    TranslationExportError,
)


SAMPLE_MD = """# Report

Some intro text.

| Name | Score |
| --- | --- |
| Alice | 10 |
| Bob | 8 |

Some text between tables.

| A | B | C |
| :-- | :-: | --: |
| 1 | 2 | 3 |

Trailing text.
"""


class TestFindMarkdownTableBlocks:
    def test_finds_two_tables(self):
        blocks = find_markdown_table_blocks(SAMPLE_MD.splitlines())
        assert len(blocks) == 2

    def test_no_tables(self):
        lines = "Just some text.\nNo pipes here.".splitlines()
        assert find_markdown_table_blocks(lines) == []

    def test_ignores_stray_pipe_without_delimiter_row(self):
        text = "This | is not a table\nJust more prose."
        assert find_markdown_table_blocks(text.splitlines()) == []


class TestEmbedTablesInMarkdown:
    def test_embeds_both_tables(self, tmp_path):
        tables_dir = tmp_path / "tables"
        tables_dir.mkdir()
        (tables_dir / "table_1.csv").write_text("Name,Score\nAlice,10\nBob,8\n")
        (tables_dir / "table_2.csv").write_text("A,B,C\n1,2,3\n")

        result, stats = embed_tables_in_markdown(SAMPLE_MD, tables_dir)

        assert stats.tables_found_in_markdown == 2
        assert stats.tables_embedded == 2
        assert stats.tables_missing_csv == []
        assert "```csv" in result
        assert "Name,Score" in result
        assert "A,B,C" in result
        assert "| Name | Score |" not in result

    def test_missing_csv_keeps_native_table(self, tmp_path):
        tables_dir = tmp_path / "tables"
        tables_dir.mkdir()
        (tables_dir / "table_1.csv").write_text("Name,Score\nAlice,10\nBob,8\n")
        # table_2.csv intentionally missing.

        result, stats = embed_tables_in_markdown(SAMPLE_MD, tables_dir)

        assert stats.tables_embedded == 1
        assert stats.tables_missing_csv == [2]
        assert "| A | B | C |" in result

    def test_no_tables_dir_keeps_everything_untouched(self, tmp_path):
        missing_dir = tmp_path / "does-not-exist"
        result, stats = embed_tables_in_markdown(SAMPLE_MD, missing_dir)
        assert stats.tables_embedded == 0
        assert stats.tables_missing_csv == [1, 2]
        assert result == SAMPLE_MD

    def test_custom_fence_language(self, tmp_path):
        tables_dir = tmp_path / "tables"
        tables_dir.mkdir()
        (tables_dir / "table_1.csv").write_text("Name,Score\nAlice,10\n")
        (tables_dir / "table_2.csv").write_text("A,B,C\n1,2,3\n")

        result, _ = embed_tables_in_markdown(SAMPLE_MD, tables_dir, fence_lang="table")
        assert "```table" in result
        assert "```csv" not in result


class TestGenerateTranslationMarkdown:
    def test_writes_translation_file(self, tmp_path):
        md_path = tmp_path / "document.md"
        md_path.write_text(SAMPLE_MD)
        tables_dir = tmp_path / "tables"
        tables_dir.mkdir()
        (tables_dir / "table_1.csv").write_text("Name,Score\nAlice,10\n")
        (tables_dir / "table_2.csv").write_text("A,B,C\n1,2,3\n")

        output_path, stats = generate_translation_markdown(md_path, tables_dir)

        assert output_path.name == "document.translation.md"
        assert output_path.exists()
        assert stats.tables_embedded == 2

    def test_missing_markdown_raises(self, tmp_path):
        with pytest.raises(TranslationExportError):
            generate_translation_markdown(tmp_path / "nope.md", tmp_path / "tables")


class TestGenerateTranslationMarkdownForJob:
    def test_full_job_flow(self, tmp_path):
        job_id = "test-job-1"
        job_dir = tmp_path / job_id
        job_dir.mkdir()
        (job_dir / "document.md").write_text(SAMPLE_MD)
        tables_dir = job_dir / "tables"
        tables_dir.mkdir()
        (tables_dir / "table_1.csv").write_text("Name,Score\nAlice,10\n")
        (tables_dir / "table_2.csv").write_text("A,B,C\n1,2,3\n")

        output_path, stats = generate_translation_markdown_for_job(job_id, tmp_path)

        assert output_path == job_dir / "document.translation.md"
        assert output_path.exists()
        assert stats.tables_embedded == 2

    def test_rejects_invalid_job_id(self, tmp_path):
        with pytest.raises(Exception):
            generate_translation_markdown_for_job("../evil", tmp_path)