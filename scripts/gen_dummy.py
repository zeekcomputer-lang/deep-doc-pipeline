"""
더미 JSONL 데이터 생성기.
3~4개월에 걸친 시스템 전환/장애 대응 시나리오 15줄 생성.
"""
from __future__ import annotations
import json
import random
from datetime import date, timedelta
from pathlib import Path


SCENARIOS = [
    # (issue, action) 쌍 — 시스템 전환 + 장애 대응 시나리오 풀
    ("레거시 ERP 인증 모듈 응답 지연 발생", "Redis 캐시 TTL을 30초에서 300초로 조정"),
    ("배포 직후 결제 API 5xx 에러 급증", "이전 빌드로 즉시 롤백 후 핫픽스 준비"),
    ("DB 마이그레이션 중 외래키 제약 충돌", "트랜잭션 분리 후 단계적 마이그레이션 재실행"),
    ("신규 SSO 연동 OAuth 토큰 갱신 실패", "Refresh Token 만료 정책 7일에서 30일로 변경"),
    ("로그 수집 파이프라인 디스크 사용률 95% 초과", "Loki 보존 정책 30일로 단축 + 압축 활성화"),
    ("Kubernetes 노드 OOMKill 빈발", "워크로드 리소스 limit 상향 + HPA 임계값 재산정"),
    ("CDN 캐시 무효화 지연으로 구버전 자산 노출", "캐시 키에 빌드 해시 포함하도록 빌드 파이프라인 수정"),
    ("외부 결제사 PG 인증 키 만료 임박", "신규 키 발급 후 무중단 교체 절차 수립"),
    ("주말 트래픽 피크 시 응답 지연 P99 8초 도달", "Auto Scaling 최소 인스턴스 4대에서 8대로 증설"),
    ("개발자 콘솔 회원가입 봇 트래픽 유입", "CAPTCHA v3 도입 및 IP 기반 Rate Limit 적용"),
    ("내부 GitLab Runner 캐시 손상", "Runner 재구성 + 캐시 스토리지 S3로 이전"),
    ("기간계 배치 마감 실패 — 데드락 감지", "배치 순서 재조정 + 인덱스 힌트 추가"),
    ("모바일 앱 푸시 토큰 등록 실패율 12%", "FCM SDK 버전 업그레이드 및 재시도 백오프 도입"),
    ("ISMS 심사 대비 접근 로그 보관 미흡 지적", "S3 Glacier 90일 보관 정책 적용"),
    ("프론트엔드 번들 사이즈 4.2MB 초과", "Webpack code-splitting 적용 및 동적 import 전환"),
    ("Elasticsearch 클러스터 unassigned shard 발생", "노드 추가 후 shard 재할당 트리거"),
    ("신규 멤버 온보딩 SSO 권한 누락 다수 보고", "Terraform 권한 모듈에 기본 그룹 자동 할당 추가"),
    ("Sentry 알림 노이즈로 실제 장애 인지 지연", "이슈 그룹화 룰 재정의 + Slack 채널 분리"),
    ("GitHub Actions 빌드 큐 적체 평균 18분 대기", "self-hosted runner 4대 추가 투입"),
    ("DLQ 메시지 폭증 — 결제 콜백 처리 실패", "콜백 핸들러 idempotency key 도입"),
]


def gen_records(n: int = 15, seed: int = 42) -> list[dict]:
    rnd = random.Random(seed)
    # 3~4개월 범위 (2026-02-01 ~ 2026-05-15)
    start = date(2026, 2, 1)
    end = date(2026, 5, 15)
    span = (end - start).days
    chosen = rnd.sample(SCENARIOS, n)
    records = []
    for i, (issue, action) in enumerate(chosen):
        d = start + timedelta(days=rnd.randint(0, span))
        rec = {
            "id": f"rec-{i+1:03d}",
            "date": d.isoformat(),
            "title": issue[:30],
            "description": (
                f"{d.isoformat()} 운영팀 보고. 상황: {issue}. "
                f"대응: {action}. 후속 모니터링 필요."
            ),
            "tags": ["incident" if "장애" in issue or "실패" in issue or "오류" in issue
                     else "operation"],
        }
        records.append(rec)
    # 날짜순 정렬
    records.sort(key=lambda r: r["date"])
    return records


def main():
    out = Path(__file__).resolve().parent.parent / "data" / "records.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    records = gen_records(15)
    with out.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} records to {out}")
    # 월 분포
    months: dict[str, int] = {}
    for r in records:
        m = r["date"][:7]
        months[m] = months.get(m, 0) + 1
    print(f"month distribution: {months}")


if __name__ == "__main__":
    main()
