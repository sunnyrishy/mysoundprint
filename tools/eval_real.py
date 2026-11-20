"""Evaluate local labeled recordings with the existing pipeline and classifier.

Folder (default data/real_audio/, gitignored, never uploaded, never copied or modified):
  data/real_audio/manifest.csv     file,label,origin  (+ optional onset_s,end_s,session,...)
  data/real_audio/<your files>     .wav .flac .ogg

Examples:
  python tools/eval_real.py --init-manifest          # list files, you fill in label and origin
  python tools/eval_real.py                          # production behavior
  python tools/eval_real.py --compare classifier_only unverified_gate_only
  python tools/eval_real.py --set gate.confirm_timeout_s=2.4   # custom EXPERIMENTAL config
  python tools/eval_real.py --split dev              # development set only (see the guide)

Writes reports/real_audio/<name>.json, .csv and .md. See docs/REAL_AUDIO_EVAL.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sentinel.classify.yamnet import YamnetClassifier
from sentinel.config import PROJECT_ROOT, ConfigError, load_config, resolve_path
from sentinel.realaudio import (
    PRESETS,
    csv_rows,
    evaluate,
    parse_run_configs,
    render_markdown,
    write_manifest_skeleton,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default="data/real_audio", help="recordings folder")
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--profile", default=None, help="profile to evaluate under (default: active)"
    )
    parser.add_argument(
        "--compare", nargs="*", default=[], metavar="CONFIG",
        help=f"extra EXPERIMENTAL configs to run beside production: {', '.join(PRESETS)}",
    )  # fmt: skip
    parser.add_argument(
        "--set", action="append", default=[], metavar="SECTION.KEY=VALUE",
        help="a custom EXPERIMENTAL config: dotted overrides applied to a copy of the config",
    )  # fmt: skip
    parser.add_argument(
        "--split", choices=["dev", "holdout"], default=None,
        help="evaluate only this split (needs a split column). Run holdout once, at the end",
    )  # fmt: skip
    parser.add_argument("--out", default="reports/real_audio", help="report folder")
    parser.add_argument("--name", default=None, help="report file name stem")
    parser.add_argument("--init-manifest", action="store_true", help="write a manifest skeleton")
    args = parser.parse_args(argv)

    directory = resolve_path(args.dir)
    try:
        if args.init_manifest:
            path = write_manifest_skeleton(directory)
            print(
                f"Wrote {path}. Fill in label and origin for every row. Labels are never guessed."
            )
            return 0
        cfg = load_config(args.config)
        runs = parse_run_configs(args.compare, args.set)
        if not cfg.classifier.enabled:
            print("classifier.enabled is false: nothing to evaluate", file=sys.stderr)
            return 2
        classifier = YamnetClassifier(cfg.classifier)
        report = evaluate(directory, cfg, classifier, runs, args.profile, args.split)
    except (ConfigError, RuntimeError, OSError) as err:
        print(err, file=sys.stderr)
        return 2

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    stem = args.name or f"eval-{stamp}"
    out = Path(args.out)
    out = out if out.is_absolute() else PROJECT_ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    cols, rows = csv_rows(report)
    with (out / f"{stem}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    md = render_markdown(report)
    (out / f"{stem}.md").write_text(md + "\n")
    print(md)
    print(f"\nWrote {out / stem}.json, .csv and .md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
