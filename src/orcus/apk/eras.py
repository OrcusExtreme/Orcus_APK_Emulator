"""App Inventor build era from targetSdk (ARCHITECTURE.md §7.3)."""

ERAS = ((36, "nb206+"), (35, "nb202–205"), (34, "nb198–201"), (33, "nb194–197"), (31, "nb190–193"),
        (30, "nb187–189"), (29, "nb184–186"), (28, "nb178–183"), (26, "nb172–177"))


def ai_era(target_sdk: int) -> str | None:
    for sdk, era in ERAS:
        if target_sdk >= sdk:
            return era if target_sdk == sdk or sdk == 36 else None
    return "2018년 12월 이전 빌드"
