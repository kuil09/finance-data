from __future__ import annotations

import argparse
import json
from pathlib import Path

from .registry import SUPPORTED_DATASETS, rebuild_dataset, sync_dataset, validate_dataset


def _root(value: str) -> Path:
    return Path(value).resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finance-data")
    parser.add_argument("--root", default=".", type=_root, help="repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    datasets = subparsers.add_parser("datasets", help="list supported datasets")
    datasets.set_defaults(handler=_cmd_datasets)

    sync = subparsers.add_parser("sync", help="collect, preserve, normalize, and validate")
    sync.add_argument("dataset", choices=SUPPORTED_DATASETS)
    sync.add_argument("--full", action="store_true", help="request the complete source history")
    sync.add_argument(
        "--overlap-days",
        type=int,
        default=None,
        help="override a day-based overlap for datasets that support it",
    )
    sync.add_argument(
        "--overlap-years",
        type=int,
        default=None,
        help="override a year-based overlap for datasets that support it",
    )
    sync.set_defaults(handler=_cmd_sync)

    rebuild = subparsers.add_parser("rebuild", help="rebuild normalized data from preserved raw snapshots")
    rebuild.add_argument("dataset", choices=SUPPORTED_DATASETS)
    rebuild.set_defaults(handler=_cmd_rebuild)

    validate = subparsers.add_parser("validate", help="validate canonical data against preserved raw data")
    validate.add_argument("dataset", choices=SUPPORTED_DATASETS)
    validate.set_defaults(handler=_cmd_validate)
    return parser


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _cmd_datasets(args: argparse.Namespace) -> int:
    _print({"datasets": list(SUPPORTED_DATASETS)})
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    kwargs: dict[str, object] = {"full": args.full}
    if args.overlap_days is not None:
        kwargs["overlap_days"] = args.overlap_days
    if args.overlap_years is not None:
        kwargs["overlap_years"] = args.overlap_years
    summary = sync_dataset(args.dataset, args.root, **kwargs)
    _print(summary.as_dict())
    return 0


def _cmd_rebuild(args: argparse.Namespace) -> int:
    rows = rebuild_dataset(args.dataset, args.root)
    _print({"dataset": args.dataset, "records": len(rows), "status": "PASS"})
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    _print(validate_dataset(args.dataset, args.root))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
