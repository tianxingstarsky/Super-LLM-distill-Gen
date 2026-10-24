"""File-size labels across boundaries; persisted sizes remain byte integers."""
import pytest

from lib.presentation.file_sizes import format_file_size


@pytest.mark.parametrize("count,label", [
    (None, "—"), (0, "0 B"), (1, "1 B"), (1023, "1023 B"),
    (1024, "1.00 KB"), (1536, "1.50 KB"),
    (1024 ** 2 - 1, "1.00 MB"), (1024 ** 2, "1.00 MB"),
    (1024 ** 3, "1.00 GB"), (3 * 1024 ** 3 // 2, "1.50 GB"),
    (1024 ** 4, "1.00 TB"), (1024 ** 5, "1.00 PB"),
])
def test_units_follow_actual_file_size(count, label):
    assert format_file_size(count) == label


@pytest.mark.parametrize("count", [-1, True, 1.5, "1024"])
def test_invalid_measurements_are_not_displayed_as_valid_sizes(count):
    with pytest.raises(ValueError, match="invalid_byte_count"):
        format_file_size(count)
