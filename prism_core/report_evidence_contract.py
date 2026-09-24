"""Common report interpretation rules; no calculations or trading gates."""


def financial_evidence_contract(language='ko'):
    if language == 'ko':
        return (
            '\n공통 근거 해석 계약:\n'
            '문체 개선 제안이나 금융 관행에 맞는 축약 표현은 사실 충돌이 아닙니다. '
            '검수에서는 제공된 근거와 실제로 양립하지 않는 수치·기간·주체 방향·비교 단정을 '
            '구체적으로 지적하고, 단순히 다르게 읽힐 가능성만으로 발행을 막지 마세요. '
            '수익률의 관측 구간 수와 입력 가격 개수를 구분하세요. N구간 수익률은 마지막 가격을 '
            'N구간 이전 기준가격과 비교하므로 기준가격을 포함해 N+1개의 가격을 사용합니다. '
            'N+1은 입력 개수이지 수익률 기간이 늘었다는 뜻이 아닙니다. '
            '같은 종료일과 연속된 관측 세션에서 수익률 대상 구간은 기준가격 다음 관측일부터 '
            '마지막 관측일까지이며, 해당 N일의 이동평균·수급 누적과 함께 설명할 수 있습니다. '
            '따라서 "최근 N일 수익률", "N일 동안 상승", "같은 기간 수급"이라는 표현만으로 '
            '기간 충돌을 선언하지 마세요. 명시한 분모 가격일·가격값, 종료일, 수익률이 실제 계산과 '
            '다르거나 가격·수급의 대상 관측일이 다를 때만 기간 문제로 판정하세요. '
            '가격은 당일을 포함할 수 있고 수급은 완료 관측일만 사용하므로 동일 N만으로 정렬을 '
            '가정하지 마세요. 누락 세션·장중 확정 여부의 한계를 유지하고 관측 구간을 임의로 '
            '달력 기간이나 1년으로 바꾸지 마세요.\n'
            '모든 기업 관련 장의 "1위", "최대", "업계 선도" 같은 상대적 지위·순위 단정에는 '
            '비교군·지표·기간·출처가 필요합니다. 회사의 자기 설명은 그렇게 귀속하고 독립 순위로 '
            '승격하지 마세요. 근거가 없으면 순위 단정만 철회하거나 확인 범위를 한정하세요. '
            '순위 미확인은 경쟁력이 없다는 뜻이 아니며 새 매매 차단 조건도 아닙니다.\n'
        )
    return (
        '\nShared evidence interpretation contract:\n'
        'Style improvements and conventional financial shorthand are not factual conflicts. '
        'Identify actual incompatible values, periods, participant directions or comparative claims, '
        'not merely a possible alternative reading. '
        'An N-interval return compares the ending price with the price N intervals earlier and uses N+1 '
        'input prices. The extra baseline price does not make it an N+1-interval return. With the same '
        'ending date and continuous observed sessions, its interval dates start after the baseline and '
        'can align with the N-session average and flow window. Do not flag ordinary "N-day return", '
        '"rose over N days", or "flows in the same period" solely for that input-count difference. '
        'Reject an explicitly wrong denominator price/date, endpoint or return, or actually misaligned '
        'observed windows. Price may include today whereas flows use completed observations; equal N '
        'alone proves no alignment. Preserve gaps, intraday-finality limits and observation/calendar distinctions.\n'
        'Across all company sections, relative rank or leadership requires a comparison universe, metric, '
        'period and source. Attribute self-reported claims as such. Otherwise withdraw or qualify the rank; '
        'unknown rank is not proof of no competitiveness and is not a new trading gate.\n'
    )
