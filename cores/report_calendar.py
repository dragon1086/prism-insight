"""KRX session calendar context shared by report writers."""
import re
from datetime import date


def calendar_context(reference_date):
    """Local exchange calendar only; unavailable is never inferred as closed."""
    context = {'reference_date': reference_date, 'is_session': None, 'calendar': 'XKRX'}
    try:
        if not isinstance(reference_date, str):
            return context
        if re.fullmatch(r'\d{8}', reference_date):
            day = date(int(reference_date[:4]), int(reference_date[4:6]), int(reference_date[6:]))
        elif re.fullmatch(r'\d{4}-\d{2}-\d{2}', reference_date):
            day = date.fromisoformat(reference_date)
        else:
            return context
        context['reference_date'] = day.isoformat()
        import pandas_market_calendars as mcal
        sessions = mcal.get_calendar('XKRX').valid_days(start_date=day, end_date=day)
        context.update(reference_date=day.isoformat(), is_session=bool(len(sessions)))
    except Exception:  # noqa: BLE001 - calendar failure must remain unknown, never closed.
        return context
    return context
