from datetime import datetime, timezone


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 标准化营业日(值: str) -> str:
    值 = (值 or "").strip()
    if len(值) >= 10:
        return 值[:10]
    raise ValueError("营业日格式须为 YYYY-MM-DD")


def 前一营业日(营业日: str) -> str:
    from datetime import date, timedelta

    y, m, d = map(int, 标准化营业日(营业日).split("-"))
    return (date(y, m, d) - timedelta(days=1)).isoformat()
