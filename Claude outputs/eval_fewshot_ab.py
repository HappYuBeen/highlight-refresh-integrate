"""few-shot A/B 비교 — 프롬프트 변형별 정확도 / 환각 점검 (서동안, 임시 점검용)

질문마다 검색은 한 번만 하고(같은 자료), 프롬프트만 바꿔 답변을 만든다.
변형
    현재            : 지금 build_messages 그대로
    예시 전부 제거   : few-shot 3쌍을 모두 뺌 (페르소나 + 규칙 + 질문)
    ③만 제거        : 거절 예시(③)만 뺌
    문서 1개만      : 현재 프롬프트 + 근거 문서를 1순위 하나만 넣음
    예시 제거+문서 1개

두 가지 모드
    (기본) 답이 있는 30문항  : 거절 = 오답(0점). 키워드 자동 채점.
    --out-of-scope 파일.json : 보고서에 없는 질문(O01~O16). 거절 = 통과, 지어내서 답함 = 환각(실패).
                               검색 단계에서 막힌(no_answer) 문항도 통과로 센다.

실행 (프로젝트 루트에서, .env·SSH 터널 필요. 이 파일은 저장소 밖에 둬도 됨)
    poetry run python C:/temp/eval_fewshot_ab.py --only Q17,Q13 --repeat 2
    poetry run python C:/temp/eval_fewshot_ab.py
    poetry run python C:/temp/eval_fewshot_ab.py --out-of-scope C:/temp/qa_out_of_scope.json
    poetry run python C:/temp/eval_fewshot_ab.py --out-of-scope C:/temp/qa_out_of_scope.json --variants "현재,예시 전부 제거"
"""

import argparse
import csv
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.getcwd())

from tests.eval_qa_accuracy import (  # noqa: E402
    DEFAULT_ANSWERS,
    _text,
    keyword_score,
    load_answers,
    persona_format_check,
    resolve_report_ids,
)

OUT_DIR = Path("logs/eval_fewshot_ab")
RULE_INDEX = 7  # build_messages 결과: [0] 페르소나 system, [1]~[6] 예시 3쌍, [7] 규칙 system
FIRST_DOC_SPLIT = "\n\n[출처:"
REFUSAL = "자료에 없습니다"


def is_refused(answer: str) -> bool:
    return REFUSAL in answer.strip()[:30]


def make_variants(build_messages):
    def current(q, ctx, persona):
        return build_messages(q, ctx, persona, [])

    def no_fewshot(q, ctx, persona):
        m = build_messages(q, ctx, persona, [])
        return [m[0], *m[RULE_INDEX:]]

    def no_third(q, ctx, persona):
        m = build_messages(q, ctx, persona, [])
        return m[:5] + m[RULE_INDEX:]

    def first_doc(q, ctx, persona):
        return build_messages(q, ctx.split(FIRST_DOC_SPLIT)[0], persona, [])

    def no_fewshot_first_doc(q, ctx, persona):
        m = build_messages(q, ctx.split(FIRST_DOC_SPLIT)[0], persona, [])
        return [m[0], *m[RULE_INDEX:]]

    return {
        "현재": current,
        "예시 전부 제거": no_fewshot,
        "③만 제거": no_third,
        "문서 1개만": first_doc,
        "예시 제거+문서 1개": no_fewshot_first_doc,
    }


def retrieve(item, ids, k, use_mmr):
    """질문 1개의 근거 자료를 한 번만 만든다. 근거가 없으면 None."""
    from hri.dao.report_dao import get_context_docs
    from hri.prompt.rag_prompt import format_docs, has_context
    from hri.retriever.search import hybrid_search

    report_ids = [ids[c] for c in item["reports"]]
    chunks = hybrid_search(item["question"], k=k, report_ids=report_ids, use_mmr=use_mmr)
    if not has_context(chunks):
        return None
    return format_docs(get_context_docs(chunks))


def main() -> None:
    parser = argparse.ArgumentParser(description="few-shot A/B 비교")
    parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS)
    parser.add_argument("--out-of-scope", type=Path, help="보고서에 없는 질문 JSON (거절=통과, 답변=환각)")
    parser.add_argument("--report-ids", help="예) FX=60,PR=59,GR=61")
    parser.add_argument("--only", help="쉼표로 구분한 문항 번호. 예) Q01,Q02 / O05,O11")
    parser.add_argument("--variants", help="쉼표로 구분한 변형 이름 (기본: 전부)")
    parser.add_argument("--repeat", type=int, default=1, help="변형마다 몇 번씩 돌릴지")
    parser.add_argument("--workers", type=int, default=4, help="동시 호출 수")
    parser.add_argument("--persona", default="실무자")
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--mmr", action="store_true")
    args = parser.parse_args()

    from hri.llm.llm_client import get_chat_model
    from hri.prompt.rag_prompt import build_messages

    oos = args.out_of_scope is not None
    data = load_answers(args.answers)  # report_id 해석용 (범위 밖 모드에서도 사용)
    ids = resolve_report_ids(data, args.report_ids)
    if oos:
        src = json.loads(args.out_of_scope.read_text(encoding="utf-8"))
        all_items = src["items"]
    else:
        all_items = data["items"]
    only = set(args.only.split(",")) if args.only else None
    items = [i for i in all_items if not only or i["id"] in only]

    variants = make_variants(build_messages)
    names = [n.strip() for n in args.variants.split(",")] if args.variants else list(variants)
    for name in names:
        if name not in variants:
            raise SystemExit(f"[중단] 없는 변형: {name} (가능: {', '.join(variants)})")

    mode = "범위 밖(환각 점검)" if oos else "정확도"
    print(f"모드: {mode} · report_id: {ids} · 문항 {len(items)}개 · 변형 {len(names)}개 · 반복 {args.repeat}회")
    contexts = {}
    for n, item in enumerate(items, 1):
        contexts[item["id"]] = retrieve(item, ids, args.k, args.mmr)
        state = "근거 있음" if contexts[item["id"]] else ("검색 차단(통과)" if oos else "근거 없음(제외)")
        print(f"[검색 {n:>2}/{len(items)}] {item['id']} {state}")

    jobs = [(item, name, rep) for item in items if contexts[item["id"]]
            for name in names for rep in range(args.repeat)]
    print(f"\n답변 호출 {len(jobs)}회 (동시 {args.workers}개) ...")

    def work(job):
        item, name, rep = job
        messages = variants[name](item["question"], contexts[item["id"]], args.persona)
        try:
            answer = _text(get_chat_model().invoke(messages).content)
            error = False
        except Exception as exc:  # 한 호출이 실패해도 계속
            answer, error = f"[오류] {type(exc).__name__}: {exc}", True
        return item, name, rep, answer, error

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(work, jobs))

    rows = []
    for item, name, rep, answer, error in results:
        refused = is_refused(answer)
        if oos:
            acc = 1.0 if refused else 0.0  # 거절 = 통과, 답변 = 환각
            fmt = ""
        else:
            acc = 0.0 if refused else keyword_score(answer, item["keywords"], "rag")[0]  # 거절 = 오답
            fmt = "" if refused or error else persona_format_check(args.persona, answer, None)
        rows.append({
            "번호": item["id"], "분류": item.get("category", ""), "변형": name, "반복": rep + 1,
            "정확도": acc, "거절": refused, "차단": False, "오류": error, "형식": fmt, "답변": answer,
        })
    if oos:  # 검색 단계에서 막힌 문항은 모든 변형에서 통과
        for item in items:
            if contexts[item["id"]]:
                continue
            for name in names:
                for rep in range(args.repeat):
                    rows.append({
                        "번호": item["id"], "분류": item.get("category", ""), "변형": name, "반복": rep + 1,
                        "정확도": 1.0, "거절": False, "차단": True, "오류": False, "형식": "",
                        "답변": "(검색 단계에서 차단: no_answer)",
                    })

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"ab_{'oos_' if oos else ''}{stamp}.csv"
    fields = ["번호", "분류", "변형", "반복", "정확도", "거절", "차단", "오류", "형식", "답변"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print("\n=== 변형별 요약 ===")
    if oos:
        print("변형 | 통과율 | 환각(실패) | 검색 차단 | 모델 거절 | 호출 수")
        for name in names:
            sel = [r for r in rows if r["변형"] == name and not r["오류"]]
            if not sel:
                print(f"{name} | - | - | - | - | 0")
                continue
            halluc = sum(r["정확도"] == 0 for r in sel)
            blocked = sum(r["차단"] for r in sel)
            refused = sum(r["거절"] for r in sel)
            print(f"{name} | {(len(sel) - halluc) / len(sel):.1%} | {halluc}/{len(sel)} | {blocked} | {refused} | {len(sel)}")
    else:
        print("변형 | 정확도(거절=0점) | 거절 | 실무자 형식 O (거절 제외) | 호출 수")
        for name in names:
            sel = [r for r in rows if r["변형"] == name and not r["오류"]]
            if not sel:
                print(f"{name} | - | - | - | 0")
                continue
            refused = sum(r["거절"] for r in sel)
            answered = [r for r in sel if not r["거절"]]
            fmt = f"{sum(r['형식'] == 'O' for r in answered)}/{len(answered)}" if answered else "-"
            print(f"{name} | {statistics.mean(r['정확도'] for r in sel):.1%} | {refused}/{len(sel)} | {fmt} | {len(sel)}")
    errors = sum(r["오류"] for r in rows)
    if errors:
        print(f"(오류 {errors}건은 평균에서 제외)")

    print("\n=== 문항별 " + ("통과(1)/환각(0)" if oos else "정확도") + " (변형 순서: " + " / ".join(names) + ") ===")
    for item in items:
        cells = []
        for name in names:
            sel = [r["정확도"] for r in rows if r["번호"] == item["id"] and r["변형"] == name and not r["오류"]]
            cells.append(f"{statistics.mean(sel):.1f}" if sel else "-")
        tag = f" [{item.get('category', '')}]" if oos else ""
        note = "" if contexts[item["id"]] else (" (검색 차단)" if oos else " (근거 없음)")
        print(f"{item['id']}{tag}: " + " / ".join(cells) + note)

    if oos:
        print("\n=== 환각 답변 (지어낸 답) ===")
        bad = [r for r in rows if r["정확도"] == 0 and not r["오류"]]
        if not bad:
            print("없음")
        for r in bad:
            print(f"[{r['번호']} / {r['변형']}] {r['답변'].strip()[:120].replace(chr(10), ' ')}")
    print(f"\n저장: {path}")


if __name__ == "__main__":
    main()
