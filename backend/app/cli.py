"""CLI: получение набора, пакетная оценка, экспорт и проверка results.json.

Запуск из корня репозитория:
  PowerShell:  $env:PYTHONPATH="backend"; python -m app.cli run --group-id DEV --dataset DEV --out results/dev/results.json
  bash:        PYTHONPATH=backend python -m app.cli run --group-id DEV --dataset DEV --out results/dev/results.json
Относительные пути (--out, --from-file) считаются от корня репозитория.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from app.config import REPO_ROOT, get_settings


def _path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (REPO_ROOT / path)


def _print(*args) -> None:  # noqa: ANN002
    print(*args, flush=True)


def _load_payload(args: argparse.Namespace):
    from app.ingest.organizers_api import OrganizersAPIError, fetch_dataset, load_cached_dataset

    if getattr(args, "from_file", None):
        _print(f"• Читаю набор из файла {args.from_file}")
        return json.loads(_path(args.from_file).read_text(encoding="utf-8"))
    _print(f"• Запрашиваю набор group_id={args.group_id} у организаторов…")
    try:
        payload = fetch_dataset(args.group_id)
    except OrganizersAPIError as exc:
        cached = load_cached_dataset(args.group_id)
        if cached is None:
            raise SystemExit(f"✗ {exc}")
        _print(f"! {exc}\n  Использую локальную копию data/raw.")
        return cached
    _print(f"  получено записей: {len(payload.get('data', {}).get('records', []))}")
    return payload


def cmd_fetch(args: argparse.Namespace) -> int:
    from app.db import configure, session_scope
    from app.store import upsert_conversations

    configure(get_settings().database_url)
    payload = _load_payload(args)
    with session_scope() as s:
        res = upsert_conversations(s, payload, args.dataset or args.group_id.upper())
    _print(f"✓ {res['dataset']}: всего {res['total']}, новых {res['added']}, обновлено {res['updated']}, "
           f"без изменений {res['unchanged']}, ошибок {len(res['errors'])}")
    for e in res["errors"]:
        _print(f"  ✗ {e}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.checklist.service import get_version, to_config
    from app.db import configure, session_scope
    from app.export.results_json import build_results, write_results
    from app.models import ChecklistVersion
    from app.export.validator import validate_results
    from app.jobs import create_job, resolve_options, run_job
    from app.models import ConversationRow
    from app.settings_store import get_runtime_settings
    from app.store import upsert_conversations

    configure(get_settings().database_url)
    dataset = args.dataset or args.group_id.upper()
    if not args.skip_fetch:
        payload = _load_payload(args)
        with session_scope() as s:
            res = upsert_conversations(s, payload, dataset)
        _print(f"✓ Загружено {res['total']} диалогов в набор {dataset} (ошибок разбора: {len(res['errors'])})")
        ids = res["conversation_ids"]
    else:
        with session_scope() as s:
            ids = [r for (r,) in s.execute(select(ConversationRow.conversation_id)
                                           .where(ConversationRow.dataset == dataset).order_by(ConversationRow.seq))]
    if args.ids:
        wanted = [i.strip() for i in args.ids.split(",") if i.strip()]
        ids = [i for i in ids if i in wanted]
    if args.limit:
        ids = ids[: args.limit]
    if not ids:
        raise SystemExit("✗ Нет диалогов для оценки")

    with session_scope() as s:
        rt = get_runtime_settings(s)
        ver = get_version(s, args.checklist_version)
        version_id = ver.id
        opts = resolve_options(args.provider, rt)
    _print(f"• Оцениваю {len(ids)} диалогов: модель {get_settings().model_for(opts.provider)} "
           f"({opts.provider}), чек-лист v{ver.version} (id={version_id})")
    job_id = create_job(ids, version_id, opts, args.force)
    t0 = time.perf_counter()

    def progress(done: int, total: int, cid: str, status: str, cached: bool) -> None:
        mark = "✓" if status == "ok" else "✗"
        _print(f"  [{done:>3}/{total}] {mark} {cid}{' (из кэша)' if cached else ''}  {time.perf_counter() - t0:6.1f} с")

    job = asyncio.run(run_job(job_id, on_progress=progress))
    _print(f"✓ Готово за {time.perf_counter() - t0:.1f} с: ошибок {job['failed']}, из кэша {job['from_cache']}, "
           f"стоимость ${job['cost_usd']:.4f}")

    if args.out:
        with session_scope() as s:
            data = build_results(s, dataset, version_id, conversation_ids=ids if (args.ids or args.limit) else None)
            used_checklist = to_config(s.get(ChecklistVersion, version_id))
        out = write_results(data, _path(args.out))
        errors, warnings = validate_results(data, expect=args.expect, checklist=used_checklist)
        _print(f"✓ Записан {out} ({len(data['results'])} записей)")
        _status_summary(data)
        _report(errors, warnings)
        return 1 if errors else 0
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from app.db import configure, session_scope
    from app.export.results_json import build_results, export_checklist, write_results
    from app.export.validator import validate_results

    configure(get_settings().database_url)
    with session_scope() as s:
        data = build_results(s, args.dataset, args.checklist_version, use_final=not args.ai_only)
        used_checklist = export_checklist(s, args.dataset, args.checklist_version)
    out = write_results(data, _path(args.out))
    errors, warnings = validate_results(data, expect=args.expect, checklist=used_checklist)
    _print(f"✓ Записан {out} ({len(data['results'])} записей)")
    _status_summary(data)
    _report(errors, warnings)
    return 1 if errors else 0


def cmd_validate(args: argparse.Namespace) -> int:
    from app.export.validator import validate_file

    checklist = None
    if args.checklist_version:
        from app.checklist.service import to_config
        from app.db import configure, session_scope
        from app.models import ChecklistVersion

        configure(get_settings().database_url)
        with session_scope() as s:
            checklist = to_config(s.get(ChecklistVersion, args.checklist_version))
    errors, warnings = validate_file(_path(args.path), expect=args.expect, checklist=checklist)
    _report(errors, warnings)
    return 1 if errors else 0


def cmd_status(_: argparse.Namespace) -> int:
    from app.brain.cloud_llm import cloud_configured

    s = get_settings()
    _print(f"Провайдер по умолчанию: {s.default_provider} → модель {s.model_for(s.default_provider)}")
    _print(f"Claude: {'ключ задан' if cloud_configured('anthropic', s) else 'ключ НЕ задан'} "
           f"({s.anthropic_model}, effort {s.anthropic_effort})")
    _print(f"OpenAI-совместимый API: {'настроен' if cloud_configured('openai', s) else 'НЕ настроен'} "
           f"({s.openai_model or '—'}{' · ' + s.openai_base_url if s.openai_base_url else ''})")
    _print(f"API организаторов: {'задан' if s.organizers_api_url else 'НЕ задан'}")
    return 0


def _status_summary(data: dict) -> None:
    from collections import Counter

    st = Counter(r.get("status") for r in data.get("results", []))
    line = f"  записей ok: {st.get('ok', 0)}, error: {st.get('error', 0)}"
    _print(line if not st.get("error") else f"! {line.strip()} — диалоги с error не оценены, проверьте предупреждения")


def _report(errors: list[str], warnings: list[str]) -> None:
    if errors:
        _print(f"✗ Ошибок валидации: {len(errors)}")
        for e in errors[:30]:
            _print(f"  ✗ {e}")
    else:
        _print("✓ Валидация пройдена")
    if warnings:
        _print(f"! Предупреждений: {len(warnings)}")
        for w in warnings[:10]:
            _print(f"  ! {w}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m app.cli", description="CQA — пакетная оценка и экспорт")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="загрузить набор у организаторов в БД")
    f.add_argument("--group-id", required=True)
    f.add_argument("--dataset")
    f.add_argument("--from-file", help="взять набор из локального JSON вместо API")
    f.set_defaults(func=cmd_fetch)

    r = sub.add_parser("run", help="получить набор, оценить, выгрузить results.json")
    r.add_argument("--group-id", required=True)
    r.add_argument("--dataset", help="DEV | PROD | ... (по умолчанию = group_id)")
    r.add_argument("--from-file")
    r.add_argument("--skip-fetch", action="store_true", help="не запрашивать API, оценить уже загруженный набор")
    r.add_argument("--provider", choices=["anthropic", "openai"],
                   help="anthropic — Claude (по умолчанию), openai — OpenAI-совместимый API из .env")
    r.add_argument("--checklist-version", type=int, help="ID версии чек-листа (по умолчанию — из настроек/последняя)")
    r.add_argument("--ids", help="только эти conversation_id через запятую")
    r.add_argument("--limit", type=int)
    r.add_argument("--force", action="store_true", help="игнорировать кэш, переоценить")
    r.add_argument("--out", help="куда записать results.json")
    r.add_argument("--expect", type=int, help="ожидаемое число записей (prod: 10)")
    r.set_defaults(func=cmd_run)

    e = sub.add_parser("export", help="выгрузить results.json из БД")
    e.add_argument("--dataset", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--checklist-version", type=int)
    e.add_argument("--ai-only", action="store_true", help="исходные оценки ИИ без решений по апелляциям")
    e.add_argument("--expect", type=int)
    e.set_defaults(func=cmd_export)

    v = sub.add_parser("validate", help="проверить results.json")
    v.add_argument("path")
    v.add_argument("--expect", type=int)
    v.add_argument("--checklist-version", type=int, help="ID версии чек-листа, против которой проверять (после замены критерия)")
    v.set_defaults(func=cmd_validate)

    st = sub.add_parser("status", help="модель по умолчанию и наличие ключей")
    st.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
