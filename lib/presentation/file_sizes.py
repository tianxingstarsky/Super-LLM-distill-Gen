"""Shared file-size labels for the console and downloadable HTML reports."""


def format_file_size(byte_count: int | None) -> str:
    """Use the console's 1,024-byte units without changing stored byte counts."""
    if byte_count is None:
        return "—"
    if type(byte_count) is not int or byte_count < 0:
        raise ValueError("invalid_byte_count")
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB")
    index, divisor = 0, 1
    while index < len(units) - 1 and byte_count >= divisor * 1024:
        index, divisor = index + 1, divisor * 1024
    if index == 0:
        return f"{byte_count} B"
    # Avoid a rounded label such as 1024.00 KB just below the next boundary.
    if index < len(units) - 1 and round(byte_count / divisor, 2) >= 1024:
        index, divisor = index + 1, divisor * 1024
    return f"{byte_count / divisor:,.2f} {units[index]}"
