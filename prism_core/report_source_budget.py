"""Source-note UTF-8 admission, separate from manifests, requests and tools."""

SOURCE_NOTE_BYTES = 6000
EXPERIMENT_SOURCE_BUDGETS = (6000, 12000, 24000, 32000)


def validate_source_budget(value):
    """Only explicit caller arguments can select a bounded experiment budget."""
    if type(value) is not int or value not in EXPERIMENT_SOURCE_BUDGETS:
        raise ValueError('INVALID_SOURCE_BUDGET')
    return value


def source_note_rejection(note, budget=SOURCE_NOTE_BYTES):
    validate_source_budget(budget)
    if not isinstance(note, str):
        return 'SOURCE_NOTE_INVALID'
    # UTF-8 cannot contain fewer bytes than code points. Avoid large encoding
    # allocations; long invalid strings are rejected by this size gate first.
    if len(note) > budget:
        return 'SOURCE_NOTE_BYTE_LIMIT'
    try:
        if len(note.encode('utf-8')) > budget:
            return 'SOURCE_NOTE_BYTE_LIMIT'
    except UnicodeEncodeError:
        return 'SOURCE_NOTE_INVALID_UTF8'
    return None
