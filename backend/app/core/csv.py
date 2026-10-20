"""
CSV Formula-Injection Guard

Cells a spreadsheet app (Excel, Google Sheets) would read as a formula rather than plain text.
Every CSV export that can contain user-written text (chat transcripts, word lists, evaluation
questions and answers) passes its cells through csv_safe(); imports that read those files back
undo it with strip_csv_guard(), so an exported file re-imports unchanged.

How to use:
    from app.core.csv import csv_safe, strip_csv_guard

    writer.writerow([csv_safe(conversation.visitor_name or "")])
    term = strip_csv_guard(row[0])
"""

# Cell-leading characters read as "this cell is a formula" — the classic CSV/formula-injection
# set (OWASP), plus a leading tab/CR which some parsers treat the same way.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: str) -> str:
    """Neutralize formula injection in an exported cell.

    A visitor name or message starting with e.g. '=HYPERLINK("http://evil","x")' would otherwise
    become a live, clickable formula the moment a teacher opens the export in Excel or Sheets.
    Prefixing a single quote is the standard fix: spreadsheet apps then show the cell as plain
    text and drop the quote themselves.
    """
    if value and value[0] in _FORMULA_TRIGGERS:
        return "'" + value
    return value


def strip_csv_guard(value: str) -> str:
    """Undo csv_safe() on an imported cell."""
    if len(value) > 1 and value[0] == "'" and value[1] in _FORMULA_TRIGGERS:
        return value[1:]
    return value
