from datetime import datetime, timedelta, timezone


def safe_parse_datetime(date_string):
    """Safely parse datetime strings with various formats"""
    if not date_string:
        return None

    try:
        # Handle ISO format with Z suffix
        if date_string.endswith("Z"):
            return datetime.fromisoformat(date_string.replace("Z", "+00:00"))

        # Handle ISO format without timezone info (assume UTC)
        if "+" not in date_string and "Z" not in date_string:
            dt = datetime.fromisoformat(date_string)
            return dt.replace(tzinfo=timezone.utc)

        # Handle other ISO formats
        return datetime.fromisoformat(date_string)

    except (ValueError, TypeError) as e:
        print(f"Failed to parse datetime: {date_string}, error: {e}")
        return None


if __name__ == "__main__":
    # Test the parsing with your actual data
    test_timestamps = [
        "2025-08-25T07:31:18.978365",
        "2025-09-11T16:34:56.925815",
        "2025-01-15T10:00:00Z",  # This format would work with original code
    ]

    for ts in test_timestamps:
        parsed = safe_parse_datetime(ts)
        print(f"'{ts}' -> {parsed}")  # This will print the parsed datetime or None if parsing fails
